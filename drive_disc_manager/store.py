"""Atomic per-account files. A ZOD export id is only meaningful in its own snapshot."""
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        if default is None:
            raise
        return default


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            f.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def fingerprint(d):
    # Include roll counts and any supplied numeric values. lock/location can change.
    return digest([d['setKey'], str(d['slotKey']), d['mainStatKey'], d['level'], d['rarity'],
                   d.get('mainStatValue'), sorted((s['key'], s['upgrades'], s.get('value')) for s in d['substats'])])


def roll_base(z):
    # inventory_scan v1.4.7 writes the initial roll as 1. Keep raw fields
    # intact so upgrading this calculation preserves pins and marker history.
    return 1 if z.get('source') == 'Zenless Optimizer' else 0


def equipment_names(wiki):
    """Map a scanner setKey to its Chinese name in flat and localized wiki exports."""
    names = {}
    for entry in wiki.get('equipment', {}).values():
        if not isinstance(entry, dict):
            continue
        en, zh = entry.get('EN'), entry.get('CHS')
        if isinstance(en, dict):
            en = en.get('name')
        if isinstance(zh, dict):
            zh = zh.get('name')
        if isinstance(en, str) and en.strip() and isinstance(zh, str) and zh.strip():
            names[en.replace(' ', '')] = zh.strip()
    return names


def inventory(z):
    if not isinstance(z, dict) or (z.get('format'), z.get('dbVersion'), z.get('version')) != ('ZOD', 2, 1):
        raise ValueError('需要 ZOD v1 / dbVersion 2 完整导出')
    if not all(isinstance(z.get(k), list) for k in ('discs', 'characters', 'wengines')):
        raise ValueError('缺少盘、角色或音擎清单')
    ds = z['discs']
    base = roll_base(z)
    audit = z.get('audit') or {}
    if audit.get('partial') or any(type(audit.get(k)) is not int or audit[k] != v
                                   for k, v in [('game_total',len(ds)),('exported',len(ds)),('delta',0)]):
        raise ValueError('库存不完整或缺少游戏内数量核对，请重新扫描')
    ids = set()
    for d in ds:
        if not isinstance(d,dict) or not all(isinstance(d.get(k),str) and d[k] for k in ('id','setKey','mainStatKey','rarity')):
            raise ValueError('缺少盘身份字段')
        if d['id'] in ids or str(d.get('slotKey')) not in list('123456') or type(d.get('level')) is not int or not 0 <= d['level'] <= 15:
            raise ValueError('重复导出编号或号位、等级异常')
        if not isinstance(d.get('substats'),list) or len(d['substats']) > 4:
            raise ValueError('副词条格式异常')
        keys = set()
        for s in d['substats']:
            if not isinstance(s,dict) or not isinstance(s.get('key'),str) or not s['key'] or s['key'] in keys or type(s.get('upgrades')) is not int or not base <= s['upgrades'] <= base+5:
                raise ValueError('副词条重复或缺失强化档数')
            keys.add(s['key'])
        ids.add(d['id'])
    return ds, Counter(fingerprint(d) for d in ds)


def defaults():
    return dict(reserve=2, reserve_rules=[], trimRecommended=False, pins=[], protect15=True, profiles=[], overrides={},
                auto_sync=True, run_scanner=False, afterEarly=False, applyMarkers=False, fieldProfile='',
                optimizer_url='', roster=[], aliases={})


def config(root):
    return {**defaults(), **read(root / 'config.json', {})}


def import_inventory(root, path):
    path = Path(path)
    if 'partial' in path.name.lower():
        raise ValueError('部分扫描文件')
    z = read(path)
    inventory(z)
    save(root / 'inventory.json', z)
    return len(z['discs'])
