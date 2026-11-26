"""Read four-piece recommendations stored as cell images in the published guide."""
import csv
import hashlib
import io
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from html.parser import HTMLParser
from urllib.parse import urlsplit


HEADER = '4 Piece Drive Disc Set'
UNKNOWN = '图标待核对'
UPDATED = re.compile(r'^Last Updated(?:\s*:|\s+\d)', re.I)
# The guide uses one composite icon for either set in a disorder team.
COMPOSITE = {'88ea364e44cef5ff913f54d2489d4b6f680e671fd6186a512fe37417093113bc':
             ('FeatheredFate', 'ChaosJazz')}


class Sheet(HTMLParser):
    """Index real sheet coordinates, including merged cells and skipped row headers."""
    def __init__(self, html):
        super().__init__()
        self.active = False
        self.row = self.col = 0
        self.occupied = {}
        self.cells = {}
        self.current = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'table' and 'waffle' in attrs.get('class', '').split():
            self.active = True
        if not self.active:
            return
        if tag == 'tr':
            self.row = self.col = 0
        elif tag == 'th':
            match = re.fullmatch(r'\d+R(\d+)', attrs.get('id', ''))
            if match:
                self.row = int(match[1]) + 1
        elif tag == 'td' and self.row:
            while self.occupied.get(self.col, 0) >= self.row:
                self.col += 1
            width = int(attrs.get('colspan', 1))
            height = int(attrs.get('rowspan', 1))
            if not 1 <= width <= 128 or not 1 <= height <= 1024:
                raise ValueError('攻略表合并单元格变化')
            self.current = {'text':'', 'images':[], 'width':width}
            self.cells[self.row, self.col] = self.current
            for col in range(self.col, self.col + width):
                self.occupied[col] = self.row + height - 1
            self.col += width
        elif tag == 'img' and self.current is not None and attrs.get('src'):
            self.current['images'].append(attrs['src'])

    def handle_data(self, data):
        if self.current is not None:
            self.current['text'] += data

    def handle_endtag(self, tag):
        if tag == 'td':
            self.current = None
        if tag == 'table':
            self.active = False


def reference_icons(html, matrix_sets):
    cells = Sheet(html).cells
    names = {key.casefold():key for key in matrix_sets}
    aliases = {'whitewatterballad':'whitewaterballad', 'phaethonmelody':"phaethon'smelody"}
    refs = {}
    for (row, col), cell in cells.items():
        if cell['text'].strip().lower() != '2-piece set effect':
            continue
        title = cells.get((row, col-1), {}).get('text', '').strip()
        raw_key = title.replace(' ', '').casefold()
        key = names.get(aliases.get(raw_key, raw_key), title.replace(' ', ''))
        icons = cells.get((row+2, col-1), {}).get('images', [])
        paired = cells.get((row+2,col), {}).get('text','').strip().lower() == '4-piece set effect'
        if not key or key in refs or len(icons) != 1 or not paired:
            raise ValueError('攻略表套装图鉴变化 · '+title)
        refs[key] = icons[0]
    if len(refs) < 2:
        raise ValueError('攻略表缺少套装图鉴')
    return refs


def role_icons(raw, html):
    cells = Sheet(html).cells
    roles = {}
    previous = agent = ''
    previous_row = actor_row = 0
    section = None
    for row, values in enumerate(csv.reader(io.StringIO(raw), strict=True), 1):
        if not any(v.strip() for v in values):
            continue
        title = values[1].strip() if len(values) > 1 else ''
        if UPDATED.match(title):
            if section:
                raise ValueError('攻略表缺少两件套表头 · '+agent)
            agent = previous
            actor_row = previous_row
            roles.setdefault(agent, [])
        previous, previous_row = title, row
        if 'Main Stats' in values:
            if not agent or values.count(HEADER) != 1 or section:
                raise ValueError('攻略表四件套表头变化 · '+agent)
            col = values.index(HEADER)
            cell = cells.get((row,col), {})
            # Cross-check the agent as well as the header: exports can straddle edits.
            if cell.get('text') != HEADER or cells.get((actor_row,1), {}).get('text','').strip() != agent:
                raise ValueError('攻略表角色或图标位置变化 · '+agent)
            section = row, col, col+cell['width']
        elif section and '2 Piece Drive Disc Set' in values:
            start, left, right = section
            if cells.get((row,left), {}).get('text') != '2 Piece Drive Disc Set':
                raise ValueError('攻略表图标区域变化 · '+agent)
            for (nr, nc), cell in cells.items():
                if not (start < nr < row and left <= nc < right):
                    continue
                for url in cell['images']:
                    caption = next(((r, c, v['text'].strip()) for (r,c),v in cells.items()
                                    if nr < r < row and c == nc and v['text'].strip()), '')
                    roles[agent].append((nr, nc, url, caption))
            if not roles[agent]:
                roles[agent].append((start, left, '', ''))
            section = None
    if section or not roles or any(not entries for entries in roles.values()):
        raise ValueError('角色攻略表缺少完整四件套区域')
    return roles


def read_icon(url, session):
    from urllib3.exceptions import HTTPError
    parts = urlsplit(url)
    if (parts.scheme, parts.netloc) != ('https','docs.google.com') or not parts.path.startswith('/sheets-images-rt/'):
        raise ValueError('攻略表图标来源变化')
    with session.get(url, timeout=(8, 15), stream=True) as response:
        response.raise_for_status()
        try:
            data = response.raw.read(512_001, decode_content=True)
        except HTTPError as exc:
            raise ValueError('攻略表图标下载中断') from exc
    if len(data) > 512_000:
        raise ValueError('攻略表图标过大')
    return data


def match_icons(refs, urls, download=None):
    import numpy as np
    from PIL import Image
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util import Retry

    worker = threading.local()
    sessions = ExitStack()
    def get_image(url):
        if download:
            return download(url)
        if not hasattr(worker, 'session'):
            worker.session = sessions.enter_context(requests.Session())
            retry = Retry(total=2, backoff_factor=0.2, status_forcelist=(502,503,504))
            worker.session.mount('https://', HTTPAdapter(max_retries=retry))
        return read_icon(url, worker.session)

    def signature(url):
        data = get_image(url)
        try:
            image = Image.open(io.BytesIO(data))
        except Image.DecompressionBombError as exc:
            raise ValueError('攻略表图标尺寸变化') from exc
        with image:
            if max(image.size) > 1536:
                raise ValueError('攻略表图标尺寸变化')
            rgba = image.convert('RGBA')
            box = rgba.getchannel('A').point(lambda n: 255 if n > 60 else 0).getbbox()
            if not box:
                raise ValueError('攻略表图标为空')
            cropped = rgba.crop(box)
            bg = Image.new('RGBA', cropped.size, (64,64,64,255))
            bg.alpha_composite(cropped)
            scaled = bg.convert('RGB').resize((32,32), Image.Resampling.LANCZOS)
            return hashlib.sha256(data).hexdigest(), np.asarray(scaled, dtype=np.float32)

    needed = sorted(set(refs.values()) | (set(urls) - {''}))
    with sessions, ThreadPoolExecutor(max_workers=4) as pool:
        icons = dict(zip(needed, pool.map(signature, needed), strict=True))
    matches = {'':(UNKNOWN,)}
    for url in set(urls) - {''}:
        digest, picture = icons[url]
        distances = sorted((float(np.mean(np.abs(picture-icons[ref][1]))), name) for name,ref in refs.items())
        (error, name), (next_error, _) = distances[:2]
        if digest in COMPOSITE and all(key in refs for key in COMPOSITE[digest]):
            matches[url] = COMPOSITE[digest]
        elif error <= 13 and next_error - error >= 4:
            matches[url] = (name,)
        else:
            matches[url] = (UNKNOWN,)
    return matches
