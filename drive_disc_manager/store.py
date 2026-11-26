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
    return 1 if z.get('source') == 'Zenless Optimizer' or z.get('networkPreview') is True else 0


def equipment_names(wiki):
    """Map a scanner setKey to its Chinese name in flat and localized wiki exports."""
    # Missing from inventory_scan 1.4.7. Exact names share a Suit ID in Enka's ZZZ store.
    names = {'BunnyinWonderland':'雪兔梦游仙境', 'NotesFromtheChained':'囚徒手记',
             'WutheringSalon':'呼啸沙龙', 'TheSkyAblaze':'拂晓行纪',
             'FeatheredFate':'谶羽之誓', 'ThornedRose':'荆棘玫瑰'}
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


def equipment_keys(names):
    """Resolve a displayed name only when it identifies exactly one subscription key."""
    reverse = {}
    for key, label in names.items():
        if isinstance(label, str) and label.strip():
            label = label.strip()
            reverse[label] = key if label not in reverse else None
    return {label:key for label, key in reverse.items() if key}


def equipment_key(raw, names, reverse):
    return raw if raw in names else reverse.get(raw.strip(), raw)


def pin_keys(disc, names, reverse, fp):
    """Canonical saved pin first, then exact spellings used by older readers."""
    raw = disc['setKey']
    key = equipment_key(raw, names, reverse)
    label = names.get(key)
    spellings = [key, raw]
    if isinstance(label, str):
        label = label.strip()
        if label not in names and reverse.get(label) == key:
            spellings.append(label)
    return [fp if name == raw else fingerprint(dict(disc, setKey=name)) for name in dict.fromkeys(spellings)]


def is_pinned(row, pins):
    return any(key in pins for key in (row.get('pinKeys') or (row['fp'],)))


def toggle_pin(row, pins):
    keys = row.get('pinKeys') or [row['fp']]
    saved = set(pins)
    return sorted(saved.difference(keys) if is_pinned(row, saved) else saved | {keys[0]})


def inventory(z, *, preview_only=False):
    if not isinstance(z, dict) or (z.get('format'), z.get('dbVersion'), z.get('version')) != ('ZOD', 2, 1):
        raise ValueError('需要 ZOD v1 / dbVersion 2 完整导出')
    if not all(isinstance(z.get(k), list) for k in ('discs', 'characters', 'wengines')):
        raise ValueError('缺少盘、角色或音擎清单')
    ds = z['discs']
    base = roll_base(z)
    audit = z.get('audit') or {}
    if z.get('networkPreview') and not preview_only:
        raise ValueError('请先在游戏中核对库存')
    if not (preview_only and z.get('networkPreview') is True) and (audit.get('partial') or any(type(audit.get(k)) is not int or audit[k] != v
                                   for k, v in [('game_total',len(ds)),('exported',len(ds)),('delta',0)])):
        total = audit.get('game_total')
        detail = f'（游戏内 {total}，导出 {len(ds)}）' if type(total) is int else ''
        raise ValueError('库存不完整或缺少游戏内数量核对'+detail+'，请重新扫描')
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
    return dict(reserve=2, reserve_rules=[], trimRecommended=False, pins=[], protect15=True, protectNaturalCrit=True, profiles=[], overrides={},
                auto_sync=True, run_scanner=True, applyMarkers=False, rotateSpareLocks=False, fieldProfile='',
                optimizer_url='', roster=[], aliases={}, includeOwned=True, futureAgents=[], excludedAgents=[],
                inventorySource='ocr', dailyLimit=1, weeklyLimit=7)


def snapshot_path(root, cfg):
    if cfg.get('inventorySource') == 'cn_export':
        return root / 'cn-inventory.json'
    return root / ('network-inventory.json' if cfg.get('inventorySource') == 'network' else 'inventory.json')


def preview_path(root, cfg):
    if cfg.get('inventorySource') == 'cn_export':
        return root / 'cn-preview.json'
    return root / ('network-preview.json' if cfg.get('inventorySource') == 'network' else 'preview.json')


def config(root):
    saved = read(root / 'config.json', {})
    saved.pop('afterEarly', None)  # Retired scheduler integration.
    return {**defaults(), **saved}


def observed_agents(snapshot):
    keys = [c.get('key') for c in snapshot.get('characters', ()) if isinstance(c, dict)]
    keys.extend(d.get('location') for d in snapshot.get('discs', ())
                if isinstance(d, dict) and d.get('ownerState') == 'worn')
    return {key.strip() for key in keys if isinstance(key, str) and key.strip()}


def network_agents(root):
    path = root/'network-owned-agents.json'
    if path.is_file():
        return set(read(path))
    # Earlier releases saved the account's network inventory without a roster index.
    return observed_agents(read(root/'network-inventory.json', {}))


def known_agents(root):
    declared = read(root/'challenge-preferences.json', {}).get('agents', {})
    cn = read(root/'cn-inventory.json', {})
    showcase = cn.get('showcase') or {}
    shown = showcase.get('owners', {}).values() if showcase.get('uid') == config(root).get('showcaseUid') else ()
    return set(read(root/'owned-agents.json', [])) | network_agents(root) | set(declared) | observed_agents(cn) | set(shown)


def import_inventory(root, path, *, readings=None):
    path = Path(path)
    if 'partial' in path.name.lower():
        raise ValueError('部分扫描文件')
    z = read(path)
    if readings is not None:
        from .scan_copies import with_copies
        z = with_copies(z, readings)
    inventory(z)
    # An account keeps unlocked agents. A later disc-only export has an empty
    # characters array and should not retire agents seen in earlier full scans.
    keys = set(read(root/'owned-agents.json', []))
    previous = read(root/'inventory.json', {})
    for snapshot in (previous, z):
        keys.update(observed_agents(snapshot))
    if keys:
        save(root/'owned-agents.json', sorted(keys))
    save(root / 'inventory.json', z)
    return len(z['discs'])
