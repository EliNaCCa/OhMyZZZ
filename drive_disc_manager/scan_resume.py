"""Reuse verified OCR text when the current disc detail image matches exactly."""
import hashlib
import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np

from .store import digest


class DetailFrame(np.ndarray):
    """Tag only disc detail jobs submitted to the upstream OCR worker."""


class ScanTextCache:
    def __init__(self, root, matcher):
        self.path = root / 'scan-text.sqlite'
        self.matcher = matcher
        self.reused = 0
        self.verified = set()
        self.disabled = True
        params = getattr(matcher, '_ocr_param', None)
        if not params or not callable(getattr(params, 'to_dict', None)):
            return
        options = params.to_dict()
        models = []
        try:
            for key in ('det_model_dir', 'rec_model_dir', 'rec_char_dict_path', 'cls_model_dir'):
                if key == 'cls_model_dir' and not options.get('use_angle_cls'):
                    continue
                stat = Path(options[key]).stat()
                models.append((key, stat.st_size, stat.st_mtime_ns))
            self.version = digest([1, type(matcher).__name__, options, models])
            root.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('CREATE TABLE IF NOT EXISTS disc_text (image TEXT PRIMARY KEY, boxes TEXT NOT NULL)')
                # Bound growth across repeated interrupted scans, including older cache files.
                db.execute('DELETE FROM disc_text WHERE rowid <= '
                           '(SELECT rowid FROM disc_text ORDER BY rowid DESC LIMIT 1 OFFSET 6000)')
            self.disabled = False
        except (OSError, KeyError, sqlite3.Error) as exc:
            logging.getLogger(__name__).info('本轮重新识别驱动盘词条 · %s', exc)

    def frame(self, image):
        if self.disabled:
            return image
        frame = image.view(DetailFrame)
        tag = hashlib.sha256(f'{self.version}:{frame.shape}:{frame.dtype}'.encode())
        tag.update(np.ascontiguousarray(frame).data)
        frame.text_key = tag.hexdigest()
        frame.from_cache = False
        return frame

    def run_ocr(self, image, *args, **kwargs):
        if isinstance(image, DetailFrame) and not self.disabled and not (args or kwargs):
            try:
                with closing(sqlite3.connect(self.path)) as db:
                    row = db.execute('SELECT boxes FROM disc_text WHERE image=?', (image.text_key,)).fetchone()
                if row:
                    from one_dragon.base.matcher.match_result import MatchResult
                    result = {}
                    for item in json.loads(row[0]):
                        x, y, right, bottom = item['position']
                        box = MatchResult(item['confidence'], x, y, right-x, bottom-y)
                        result.setdefault(item['text'], []).append(box)
                    if result:
                        image.from_cache = True
                        self.reused += 1
                        return result
            except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
                self.disable(exc)
        return self.matcher.run_ocr(image, *args, **kwargs)

    def remember(self, frame, items):
        if self.disabled or not isinstance(frame, DetailFrame):
            return
        self.verified.add(frame.text_key)
        if frame.from_cache:
            return
        try:
            data = json.dumps(items, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('INSERT OR REPLACE INTO disc_text VALUES (?,?)', (frame.text_key, data))
        except (sqlite3.Error, TypeError, ValueError) as exc:
            self.disable(exc)

    def disable(self, exc):
        logging.getLogger(__name__).info('本轮重新识别驱动盘词条 · %s', exc)
        self.disabled = True

    def finish(self):
        if self.disabled:
            return
        try:
            # The warehouse count has been reconciled. Keep just the verified
            # images from this scan for the next run, including cache hits.
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('CREATE TEMP TABLE current_text (image TEXT PRIMARY KEY)')
                db.executemany('INSERT INTO current_text VALUES (?)', ((key,) for key in self.verified))
                db.execute('DELETE FROM disc_text WHERE image NOT IN (SELECT image FROM current_text)')
        except sqlite3.Error as exc:
            self.disable(exc)
