"""One seven-disc account: transfer a reserved piece into a build when the first pick is discarded."""
from argparse import ArgumentParser
from pathlib import Path
from shutil import copyfile
import sys
from tempfile import TemporaryDirectory


def smoke(host, root):
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(host/'src')]
    from drive_disc_manager.cn_export import normalize
    from drive_disc_manager.engine import NAME, VERSION
    from drive_disc_manager.inventory_comparison import verify_cn_inventory
    from drive_disc_manager.service import preview, reassign_discards
    from drive_disc_manager.store import config, digest, equipment_names, read, save

    exe = Path('_optimizer') / VERSION / NAME
    (root.parent/exe).parent.mkdir(parents=True)
    copyfile(host/'plugin_data/drive_disc_manager'/exe, root.parent/exe)
    names = equipment_names({})
    raws = []
    for slot, main in enumerate(('hp', 'atk', 'def', 'crit_', 'fire_dmg_', 'atk_'), 1):
        raws.append(dict(id=str(1000+slot), slotKey=str(slot), setKey='TheSkyAblaze', level=0,
                         rarity='S', mainStatKey=main, location='', lock=False, trash=False,
                         substats=[dict(key=key, upgrades=1) for key in ('def_', 'crit_dmg_', 'pen')]))
    raws.append(dict(raws[0], id='1007', substats=[dict(key=key, upgrades=1) for key in ('hp_', 'def_', 'pen')]))
    exported = normalize(dict(format='ZOOD', version=1, source='zzz-drive-exporter', discs=raws))
    request = dict(characterName='11号', roleSystem='ATTACK', characterElement='FIRE',
                   baseATK=1250, baseCritRate=80, baseCritDmg=80)
    profiles = [dict(name=name, request=request) for name in ('常态', '爆发')]
    cfg = dict(config(root), inventorySource='cn_export', profiles=profiles, auto_sync=False,
               includeOwned=False, protectNaturalCrit=False, trimRecommended=False,
               reserve_rules=[dict(set='TheSkyAblaze', slot='1', count=1, trimRecommended=True)])
    rules = dict(version='fixture', fields={'matrix/TheSkyAblaze/4':['crit_']})
    pinned = cfg, rules, []
    save(root/'config.json', cfg)
    save(root/'cn-inventory.json', exported)
    planned = preview(root, snapshot=pinned)
    assert not planned['issues'], planned['issues']
    assert all(result['ids'][0]=='1001' for result in planned['assignments'].values())
    assert '备用 · 通用' in planned['discs'][-1]['reasons']
    readings = [dict(d, observedGrade='S', ownerState='bag', lockState='unlocked',
                     markState='marked' if d['id']=='1001' else 'unmarked', trash=d['id']=='1001')
                for d in exported['discs']]
    view = verify_cn_inventory(exported, dict(source='Zenless Optimizer', audit=dict(game_total=7)), readings, names)
    # With this slot's quota set to zero the lower-scored duplicate-slot piece should be actionable.
    zero = dict(cfg, reserve_rules=[dict(set='TheSkyAblaze', slot='1', count=0, trimRecommended=True)])
    no_spares = preview(root, snapshot=(zero, rules, []))
    available = [dict(d, trash=False, markState='unmarked') for d in readings]
    clear = verify_cn_inventory(exported, dict(source='Zenless Optimizer', audit=dict(game_total=7)), available, names)
    save(root/'inventory.json', clear)
    checked = preview(root, snapshot=(zero, rules, []), verified_game=True, cn_plan=no_spares)
    assert checked['discs'][-1]['decision'] == '弃置候选', checked['discs'][-1]
    save(root/'inventory.json', view)
    partial = dict(view, audit=dict(view['audit'], incremental=True, verifiedUids=[]))
    assert reassign_discards(root, None, pinned, planned, partial) is planned
    partial['audit']['verifiedUids'] = ['1001']
    revised = reassign_discards(root, None, pinned, planned, partial)
    assert revised['inventory'] == digest(exported) and read(root/'cn-inventory.json') == exported
    assert all(result['ids'][0]=='1007' for result in revised['assignments'].values())
    actual = preview(root, snapshot=pinned, verified_game=True, cn_plan=revised)
    rows = {row['exportUid']:row for row in actual['discs']}
    assert rows['1001']['decision'] == '已弃置' and rows['1007']['decision'] == '保留'
    assert {'配队 · 常态','配队 · 爆发'} <= set(rows['1007']['reasons'])
    assert not any(r.startswith('备用 · ') for r in rows['1007']['reasons'])
    assert all(result['ids'][0] == rows['1007']['fp'] for result in actual['assignments'].values())
    view['discs'][-1].update(markState='marked', trash=True)
    try:
        reassign_discards(root, None, pinned, revised, view)
    except ValueError as exc:
        assert '配装未配齐' in str(exc), str(exc)
    else:
        raise AssertionError('allocated a marked disc when both alternatives were discarded')
    print('PASS: 7 discs, two builds reranked, preserved old discard, no extra spare, empty slot reported')


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--home', required=True, type=Path)
    args = parser.parse_args()
    with TemporaryDirectory() as folder:
        smoke(args.home.resolve(), Path(folder)/'1')
