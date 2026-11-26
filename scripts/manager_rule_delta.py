"""Compare two plugin revisions on the same offline drive-disc inventory."""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def snapshot(root):
    sys.path.insert(0, str(root.resolve()))
    from drive_disc_manager.planner import plan
    from drive_disc_manager.store import defaults

    key = 'matrix/SwingJazz/4'
    fields = {key: ['atk_'], key+'/known': ['atk_', 'def_']}
    samples = [
        ('套装推荐', 'atk_', ('crit_', 'hp', 'pen'), {}),
        ('暴击备用', 'def_', ('crit_', 'hp', 'pen'), {}),
        ('同类超额', 'def_', ('atk', 'hp', 'pen'), {}),
        ('玩家锁定', 'def_', ('atk_', 'hp', 'pen'), dict(lock=True, lockState='locked')),
        ('原有弃置', 'def_', ('atk_', 'def', 'pen'), dict(trash=True, markState='marked')),
        ('主词条待核', 'hp_', ('atk', 'def', 'pen'), {}),
    ]
    discs = []
    for label, main, stats, flags in samples:
        disc = dict(id=label, setKey='SwingJazz', slotKey='4', mainStatKey=main, level=0,
                    rarity='S', substats=[dict(key=stat, upgrades=0) for stat in stats],
                    lock=False, lockState='unlocked', trash=False, markState='unmarked', ownerState='bag')
        discs.append(disc | flags)
    count = len(discs)
    inventory = dict(format='ZOD', version=1, dbVersion=2, characters=[], wengines=[], discs=discs,
                     audit=dict(game_total=count, exported=count, delta=0))
    settings = dict(defaults(), reserve=1, auto_sync=False)
    result = plan(inventory, settings, dict(fields=fields, version='sample'), {})
    if result['issues'] or len(result['discs']) != count:
        raise ValueError('固定库存未能完整计算')
    return {row['id']: row['decision'] for row in result['discs']}


def load_snapshot(root):
    # Each interpreter imports only the specified revision. Both use this fixture.
    output = subprocess.check_output([sys.executable, '-I', __file__, '--snapshot', str(root)])
    return json.loads(output)


def compare(before, after):
    if before.keys() != after.keys():
        raise ValueError('两版样盘无法逐件对应')
    added = [label for label in before if before[label] != '弃置候选' and after[label] == '弃置候选']
    lines = ['### OhMyZZZ 实际留盘结果', '', f'固定库存 {len(after)} 张，新增弃置候选 {len(added)} 张。']
    if added:
        lines.append('新增候选为' + '、'.join(added) + '。')
    lines += ['', '| 样盘 | 更新前 | 更新后 |', '| --- | --- | --- |']
    lines.extend(f'| {label} | {before[label]} | {decision} |' for label, decision in after.items())
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('revisions', nargs='*', type=Path)
    args = parser.parse_args()
    if args.snapshot:
        print(json.dumps(snapshot(args.snapshot), ensure_ascii=True))
    elif len(args.revisions) == 2:
        print(compare(*(load_snapshot(path) for path in args.revisions)))
    else:
        parser.error('需要更新前和更新后两份插件源码的路径')
