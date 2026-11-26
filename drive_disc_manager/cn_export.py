"""Read the finished ZOOD file exported by zzz-drive-exporter for CN Windows."""
import json
import re
from pathlib import Path

from .optimizer import STATS
from .store import fingerprint, inventory, read, save
from .subscriptions import SLOT_MAIN


SOURCE = 'zzz-drive-exporter'
FILE = 'cn-inventory.json'
LEVEL_CAP = {'S': 15, 'A': 12, 'B': 9}
STAT_KEYS = {'impact_': 'impact', 'enerRegen_': 'energyRegen_'}
SET_KEYS = {'BranchBladeSong': 'Branch&BladeSong', 'PhaethonsMelody': "Phaethon'sMelody",
            'KingOfTheSummit': 'KingoftheSummit', 'DawnsBloom': "Dawn'sBloom",
            'BunnyInWonderland': 'BunnyinWonderland'}


def normalize_disc(raw):
    if not isinstance(raw, dict):
        raise ValueError('盘的数据格式有误')
    uid = raw.get('id')
    if not isinstance(uid, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', uid) or int(uid) >= 2**64:
        raise ValueError('缺少有效的驱动盘编号')
    slot, rank, level = raw.get('slotKey'), raw.get('rarity'), raw.get('level')
    if not isinstance(rank, str) or rank not in LEVEL_CAP or type(level) is not int or not 0 <= level <= LEVEL_CAP[rank]:
        raise ValueError('驱动盘品级或等级有误')
    raw_main, name = raw.get('mainStatKey'), raw.get('setKey')
    if not isinstance(name, str) or not name.strip() or not isinstance(raw_main, str):
        raise ValueError('缺少套装或主属性')
    main = STAT_KEYS.get(raw_main, raw_main)
    if not isinstance(slot, str) or main not in SLOT_MAIN.get(slot, ()):
        raise ValueError('号位和主属性不匹配')
    substats = raw.get('substats')
    if not isinstance(substats, list) or len(substats) > 4:
        raise ValueError('副词条格式有误')
    rolls = {}
    for sub in substats:
        key = sub.get('key') if isinstance(sub, dict) else None
        if not isinstance(key, str) or key == main or key in rolls or not STATS.get(key, (None, None))[1]:
            raise ValueError('副词条重复或无法识别')
        count = sub.get('upgrades')
        if type(count) is not int or not 1 <= count <= 6:
            raise ValueError('副词条强化档数有误')
        rolls[key] = count
    if type(raw.get('lock')) is not bool or raw.get('location') != '' or raw.get('trash') is not False:
        raise ValueError('该导出版本的穿戴或标记格式有变化')
    # This exporter has no owner/trash data and may use false when the lock field is missing.
    disc = dict(id=uid, setKey=SET_KEYS.get(name, name), slotKey=slot, rarity=rank, level=level,
                mainStatKey=main, substats=[dict(key=k, upgrades=rolls[k]) for k in sorted(rolls)],
                location='', ownerState='unknown', markState='unknown', lock=raw['lock'],
                lockState='locked' if raw['lock'] else 'unknown')
    if rank == 'S':
        from .reserve_policy import development
        if development(level, rolls) is None:
            raise ValueError('强化等级和副词条档数不符')
        maximum = STATS[main][2]
        # Integer main stats are displayed rounded down in game.
        value = maximum * (5 + level) / 20
        if main in ('hp', 'atk', 'def', 'anomProf'):
            value = int(value)
        disc['observedValues'] = dict(main={main: round(value, 4)},
                                      subs={k: round(STATS[k][1] * v, 4) for k, v in rolls.items()})
    return disc


def normalize(data):
    if (not isinstance(data, dict) or data.get('format') != 'ZOOD' or type(data.get('version')) is not int
            or data['version'] != 1 or data.get('source') != SOURCE or not isinstance(data.get('discs'), list)):
        raise ValueError('请选择国服读取器导出的 ZOOD 文件')
    discs = sorted((normalize_disc(row) for row in data['discs']), key=lambda d: int(d['id']))
    if not discs:
        raise ValueError('导出文件里没有驱动盘，请检查读取器')
    snapshot = dict(format='ZOD', version=1, dbVersion=2, networkPreview=True, source=SOURCE,
                    discs=discs, characters=[], wengines=[])
    inventory(snapshot, preview_only=True)
    return snapshot


def compare(previous, current):
    before = {d['id']: d for d in previous}
    after = {d['id']: d for d in current}
    common = before.keys() & after.keys()
    if not common:
        return f'已导入 {len(after)} 张'
    changed = []
    for name, count in [('新增', len(after.keys() - before.keys())), ('减少', len(before.keys() - after.keys())),
                        ('属性变化', sum(fingerprint(before[k]) != fingerprint(after[k]) for k in common)),
                        ('锁定变化', sum(before[k]['lock'] != after[k]['lock'] for k in common))]:
        if count:
            changed.append(f'{name} {count} 张')
    return '，'.join(changed) or '驱动盘没有变化'


def refresh(root, check_account, path=None):
    """Under the maintenance lock, refresh only the file selected for this account."""
    previous = read(root / FILE, {})
    bound = previous.get('exportFile', '')
    manual = path is not None
    if not manual and not bound:
        raise ValueError('先到仓库选择本账号的国服导出文件')
    path = Path(path if manual else bound).resolve()
    if not path.is_file():
        raise ValueError('找不到导出文件，请在仓库重新选择')
    if path.suffix.lower() != '.json':
        raise ValueError('请选择 ZOOD 的 JSON 文件')
    if manual:
        for other in root.parent.iterdir():
            if other.name.isdecimal() and other != root:
                other_file = read(other / FILE, {}).get('exportFile')
                if other_file and Path(other_file) == path:
                    raise ValueError('这个导出文件已用于另一个一条龙账号')
    with path.open('rb') as stream:
        payload = stream.read(16_000_001)
    if len(payload) > 16_000_000:
        raise ValueError('导出文件过大，请选择 ZOOD 仓库文件')
    snapshot = normalize(json.loads(payload.decode('utf-8-sig')))
    before, after = previous.get('discs', []), snapshot['discs']
    if before and not manual and {d['id'] for d in before}.isdisjoint(d['id'] for d in after):
        raise ValueError('这份文件与上次没有同一张盘，请在仓库重新选择本账号的导出文件')
    check_account()
    if bound and path == Path(bound) and before == after:
        return '驱动盘没有变化'
    message = compare(before, after)
    snapshot.update(exportFile=str(path), lastChange=message)
    save(root / FILE, snapshot)
    return message
