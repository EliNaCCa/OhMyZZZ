"""Read the finished ZOOD file exported by zzz-drive-exporter for CN Windows."""
import json
import os
import re
import time
from pathlib import Path

from .optimizer import STATS, exported_s_main
from .store import InventoryUnavailable, fingerprint, inventory, read, save
from .subscriptions import SLOT_MAIN


SOURCE = 'zzz-drive-exporter'
FILE = 'cn-inventory.json'
EXPORT_NAME = 'zzz-drives-zood-v1.json'
FOLDER_FILE = 'cn-export-folder.json'
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
    owner = raw.get('location', '')
    if (not isinstance(owner, str) or len(owner) > 80 or (owner and (not owner.isprintable() or not owner.strip()))
            or type(raw.get('lock')) is not bool or raw.get('trash') is not False):
        raise ValueError('该导出版本的穿戴或标记格式有变化')
    owner = owner.strip()
    # v0.2.1 writes a blank for every disc. Only a named wearer is evidence.
    disc = dict(id=uid, setKey=SET_KEYS.get(name, name), slotKey=slot, rarity=rank, level=level,
                mainStatKey=main, substats=[dict(key=k, upgrades=rolls[k]) for k in sorted(rolls)],
                location=owner, ownerState='worn' if owner else 'unknown', markState='unknown', lock=raw['lock'],
                lockState='locked' if raw['lock'] else 'unknown')
    if rank == 'S':
        from .reserve_policy import development
        if development(level, rolls) is None:
            raise ValueError('强化等级和副词条档数不符')
        disc['observedValues'] = dict(main={main: exported_s_main(main, level)},
                                      subs={k: round(STATS[k][1] * v, 4) for k, v in rolls.items()})
    return disc


def normalize(data):
    if (not isinstance(data, dict) or data.get('format') != 'ZOOD' or type(data.get('version')) is not int
            or data['version'] != 1 or data.get('source') != SOURCE or not isinstance(data.get('discs'), list)):
        raise ValueError('请选择国服读取器导出的 ZOOD 文件')
    discs = sorted((normalize_disc(row) for row in data['discs']), key=lambda d: int(d['id']))
    if not discs:
        raise ValueError('导出文件里没有驱动盘，请检查读取器')
    owners = [(d['location'].casefold(), d['slotKey']) for d in discs if d['location']]
    if len(set(owners)) != len(owners):
        raise ValueError('导出文件里同一代理人的一个号位有两张盘')
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
                        ('锁定变化', sum(before[k]['lock'] != after[k]['lock'] for k in common)),
                        ('换装', sum(bool(before[k].get('location') and after[k].get('location')) and
                                    before[k]['location'] != after[k]['location'] for k in common)),
                        ('穿戴记录更新', sum(bool(before[k].get('location')) != bool(after[k].get('location')) for k in common))]:
        if count:
            changed.append(f'{name} {count} 张')
    return '，'.join(changed) or '驱动盘没有变化'


def source_path(root, previous):
    last = previous.get('exportFile', '')
    folder = read(root / FOLDER_FILE, {})
    # A successful file import supersedes an older folder selection in the same commit as the snapshot.
    if folder and folder['previousFile'] == last:
        return Path(folder['file'])
    return Path(last) if last else root / 'exports' / EXPORT_NAME


def check_source_owner(root, path):
    for other in root.parent.iterdir():
        if not other.name.isdecimal() or other == root:
            continue
        snap = read(other / FILE, {})
        claimed = [source_path(other, snap)]
        if snap.get('exportFile'):
            claimed.append(Path(snap['exportFile']))
        for existing in claimed:
            if existing.resolve() == path or (existing.is_file() and path.is_file() and existing.samefile(path)):
                raise ValueError('这个导出文件已用于另一个一条龙账号')


def select_folder(root, directory, check_account):
    """Choose the reader folder, or its exports subfolder, even before the first export."""
    folder = Path(directory).resolve()
    if not folder.is_dir():
        raise ValueError('请选择读取器所在的文件夹或它的 exports 文件夹')
    if folder.name.casefold() != 'exports':
        folder = folder / 'exports'
    path = (folder / EXPORT_NAME).resolve()
    check_source_owner(root, path)
    previous = read(root / FILE, {})
    check_account()
    save(root / FOLDER_FILE, dict(file=str(path), previousFile=previous.get('exportFile', '')))
    return path


def file_stamp(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def same_export(path, stat, previous):
    stamp = previous.get('exportStamp')
    return (str(path) == previous.get('exportFile') and stat.st_mtime_ns == previous.get('exportWrittenNs')
            and (stamp is None or tuple(stamp) == file_stamp(stat)))


def refresh(root, check_account, path=None, *, showcase_uid='', wiki=None):
    """Under the maintenance lock, refresh only the file selected for this account."""
    previous = read(root / FILE, {})
    started = time.time()
    bound = previous.get('exportFile', '')
    manual = path is not None
    path = (Path(path) if manual else source_path(root, previous)).resolve()
    if not path.exists():
        raise InventoryUnavailable(f'尚未收到国服仓库文件。等待读取器导出到 {path}')
    if path.suffix.lower() != '.json':
        raise ValueError('请选择 ZOOD 的 JSON 文件')
    if manual or not bound or path != Path(bound):
        check_source_owner(root, path)
    with path.open('rb') as stream:
        original = os.fstat(stream.fileno())
        written_ns = original.st_mtime_ns
        payload = stream.read(16_000_001)
        if file_stamp(os.fstat(stream.fileno())) != file_stamp(original):
            raise ValueError('读取器正在写入库存，请稍后刷新')
    if len(payload) > 16_000_000:
        raise ValueError('导出文件过大，请选择 ZOOD 仓库文件')
    snapshot = normalize(json.loads(payload.decode('utf-8-sig')))
    before, after = previous.get('discs', []), snapshot['discs']
    if before and not manual and {d['id'] for d in before}.isdisjoint(d['id'] for d in after):
        raise ValueError('这份文件与上次没有同一张盘，请在仓库重新选择本账号的导出文件')
    if showcase_uid:
        from .showcase import read_showcase
        snapshot['showcase'] = read_showcase(showcase_uid, after, wiki or {}, previous.get('showcase', {}))
    check_account()
    if file_stamp(path.stat()) != file_stamp(original):
        raise ValueError('读取器正在更新库存，请稍后刷新')
    if (before == after and same_export(path, original, previous)
            and previous.get('showcase') == snapshot.get('showcase')):
        (root / FOLDER_FILE).unlink(missing_ok=True)
        audit_export(root, previous, started)
        return '驱动盘没有变化'
    message = compare(before, after)
    old_show, new_show = previous.get('showcase') or {}, snapshot.get('showcase') or {}
    if old_show.get('uid') and old_show.get('uid') == new_show.get('uid'):
        old_owners, new_owners = old_show['owners'], new_show['owners']
        moved = sum(old_owners[key] != new_owners[key] for key in old_owners.keys() & new_owners.keys())
        if moved:
            change = f'展柜换装 {moved} 张'
            message = change if message == '驱动盘没有变化' else message + '，' + change
    snapshot.update(exportFile=str(path), exportWrittenNs=written_ns, exportStamp=file_stamp(original), lastChange=message)
    save(root / FILE, snapshot)
    (root / FOLDER_FILE).unlink(missing_ok=True)
    audit_export(root, snapshot, started)
    return message


def audit_export(root, snapshot, started):
    from .salvage_audit import native_run, needs_review, observe_native, reconcile
    previous = read(root/'dismantle-result.json', {})
    pending = previous if needs_review(previous) else read(root/'dismantle-preview.json', {})
    if (not pending.get('beforeUids') or pending.get('afterInventory') or not root.name.isdecimal()
            or pending.get('account') != int(root.name) or not pending.get('exportFile')
            or Path(pending['exportFile']) != Path(snapshot['exportFile'])):
        return
    if not needs_review(previous):
        from one_dragon.utils import os_utils
        account = int(root.name)
        observe_native(root, account, native_run(Path(os_utils.get_work_dir()), account), started)
    reconcile(root, snapshot)
