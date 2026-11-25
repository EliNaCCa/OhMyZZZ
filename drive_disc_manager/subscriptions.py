import csv
import io
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from .store import digest, read, save

GUIDE = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vTj2PaPq6Py_1B5fsOPj_Moc-tN_7mut7fICczI6lz1njyEIAInTnfB7lAraX4pYCRGNbaHGlIbFZ90/pub'
MATRIX = 'https://docs.google.com/spreadsheets/d/1ZIZa4zg0zkkPIP2TrHQaMw9XzjJLvO2iOpUAyREDFwY/export?format=csv&gid=133245023'
ELEMENTS = {'physical','electric','fire','ice','ether','wind','lumiflux'}
STATS = {'ATK':'atk_', 'HP':'hp_', 'DEF':'def_', 'CRIT Rate':'crit_', 'CRIT DMG':'crit_dmg_',
         'Anomaly Proficiency':'anomProf', 'PEN Ratio':'pen_', 'Anomaly Mastery':'anomMas_',
         'Impact':'impact','Energy Regen':'energyRegen_', **{e.title()+' DMG':e+'_dmg_' for e in ELEMENTS}}


def fetch(url):
    with urllib.request.urlopen(url, timeout=15) as response:
        raw = response.read(4_000_001)
    if len(raw) > 4_000_000:
        raise ValueError('来源超过大小上限')
    return raw.decode('utf-8-sig')


def matrix_fields(raw):
    fields, seen_slots, columns = {}, set(), {}
    for row in csv.reader(io.StringIO(raw)):
        name = row[0].strip() if row else ''
        if not name:
            continue
        if name in ('Disc 4','Disc 5','Disc 6'):
            slot = name[-1]
            if slot in seen_slots:
                raise ValueError('主词条表重复表头')
            seen_slots.add(slot)
            columns = {}
            for i, cell in enumerate(row[1:], 1):
                if not cell.strip():
                    break
                if cell.strip() not in STATS:
                    raise ValueError('未知主词条表头 '+cell)
                columns[i] = STATS[cell.strip()]
            if len(columns) < 6:
                raise ValueError('主词条表缺列')
            continue
        if not columns:
            raise ValueError('缺少表头')
        key = 'matrix/'+name.replace(' ','')+'/'+slot
        if key in fields:
            raise ValueError('重复套装行 '+name)
        accepted, known = [], []
        for i, stat in columns.items():
            value = row[i].strip() if i < len(row) else ''
            if not value:
                continue
            if not value.isdecimal():
                raise ValueError('非数字推荐数 '+name)
            known.append(stat)
            if int(value) > 0:
                accepted.append(stat)
        fields[key] = sorted(accepted)
        fields[key+'/known'] = sorted(known)
    if seen_slots != {'4','5','6'} or not all(any(k.endswith('/'+s) for k in fields) for s in seen_slots):
        raise ValueError('清理表缺失号位')
    return fields


def guide_fields(raw):
    rows = [r+['']*40 for r in csv.reader(io.StringIO(raw))]
    result, agent, cols = {}, '', {}
    for i, row in enumerate(rows):
        if row[1].strip().startswith('Last Updated:') and i:
            agent = rows[i-1][1].strip()
            cols = {}
            if not agent:
                raise ValueError('角色栏为空')
        if not agent:
            continue
        if 'Main Stats' in row and 'Sub Stats' in row:
            cols = {'main':row.index('Main Stats'), 'subs':row.index('Sub Stats')}
            continue
        if not cols:
            continue
        field = 'guide/'+agent+'/'
        m = re.search(r'(.+?)\s+Disc\s*([456])\b', row[cols['main']], re.I)
        if m:
            result.setdefault(field+'slot'+m[2], []).append(m[1].strip())
        sub = row[cols['subs']].strip()
        if sub == 'Baseline Stats':
            cols['subs_done'] = True
        elif sub and not cols.get('subs_done'):
            result.setdefault(field+'subs', []).append(sub)
    if not result:
        raise ValueError('角色表结构未知')
    return result


def sources():
    index = fetch(GUIDE+'html')
    tabs = [(name,gid) for name,gid in re.findall(r'items[.]push[(][{]name:\s*"([^"]+)"[^}]*?gid:\s*"([0-9]+)"', index) if name.lower() in ELEMENTS]
    if not {'Physical','Fire','Ice','Electric','Ether'}.issubset({name for name,_ in tabs}):
        raise ValueError('攻略表页签变化')
    fields = matrix_fields(fetch(MATRIX))
    with ThreadPoolExecutor(max_workers=3) as pool:
        urls = [GUIDE+'?single=true&output=csv&gid='+gid for _, gid in tabs]
        for data in pool.map(fetch, urls):
            for key, value in guide_fields(data).items():
                fields[key] = sorted(set(fields.get(key, [])+value))
    return fields, dict(tabs)


def effective(remote, override=None):
    if not override or override['mode'] == 'inherit':
        return remote
    local = set(override['values'])
    if override['mode'] == 'add':
        local.update(remote or [])
    elif override['mode'] != 'only':
        raise ValueError('字段覆盖模式无效')
    return sorted(local)


def merge(previous, fields, overrides, tabs):
    old = previous.get('fields', {})
    rev = digest(fields)
    pending = dict(previous.get('conflicts', {}))
    for key, override in overrides.items():
        before, after = old.get(key), fields.get(key)
        mode = override['mode']
        values = set(override.get('values',[]))
        if mode == 'inherit' or set(after or []) == values:
            pending.pop(key, None)
            continue
        if old and before != after:
            conflict = mode == 'only' or bool((set(before or []) - set(after or [])) & values)
            if conflict:
                pending[key] = dict(old=before, remote=after, local=override, active=effective(after,override), version=rev)
            else:
                pending.pop(key,None)
    return dict(fields=fields, version=rev, tabs=tabs, updated=time.time(), conflicts={k:v for k,v in pending.items() if k in overrides})


def sync(root, cfg):
    old = read(root/'rules.json', {})
    fields, tabs = sources()
    new = merge(old, fields, cfg['overrides'], tabs)
    save(root/'rules.json', new)
    return new
