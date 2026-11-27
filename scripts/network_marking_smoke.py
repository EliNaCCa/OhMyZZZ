"""Exercise the two reader plans and the live marker lists in a temporary account."""
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory


def smoke(root):
    from drive_disc_manager.disc_trace import teams_for_disc
    from drive_disc_manager.live import Marker
    from drive_disc_manager.network_reader import inventory_guard, normalize
    from drive_disc_manager.salvage_audit import prepare_batch
    from drive_disc_manager.service import preview
    from drive_disc_manager.store import defaults, fingerprint, save

    def disc(third, marked=False):
        return dict(setKey='TheSkyAblaze', slotKey='4', mainStatKey='atk_', rarity='S', level=0,
                    location='', lock=False, trash=marked,
                    substats=[dict(key=k, upgrades=0) for k in ('crit_', 'hp', third)])

    export = normalize(dict(format='ZOD', version=1, source='ZZZ Packet Capture', characters=[], wengines=[],
                            discs=[disc('pen'), disc('def'), disc('hp_', True), disc('crit_dmg_')]))
    save(root/'network-inventory.json', export)
    game_discs = [dict(d, setKey='拂晓行纪', id='scan-'+str(i), observedGrade='S')
                  for i, d in enumerate(export['discs'])]
    # A valid numeric reading, but different from what was exported for the fourth disc.
    game_discs[3]['observedValues'] = dict(game_discs[3]['observedValues'], main={'atk_':7.4})
    def snapshot(discs):
        return dict(export, source='Zenless Optimizer', networkPreview=False, discs=list(reversed(discs)),
                    audit=dict(game_total=len(discs), exported=len(discs), delta=0))

    game = snapshot(game_discs)
    save(root/'inventory.json', game)
    profile = '11号'
    cfg = dict(defaults(), profiles=[dict(name=profile, request=dict(characterName=profile))], reserve=0,
               trimRecommended=True, includeOwned=False, inventorySource='network')
    rules = dict(fields={'matrix/TheSkyAblaze/4':['atk_'], 'matrix/TheSkyAblaze/4/known':['atk_']})
    options = dict(snapshot=(cfg, rules, []), triage_only=True)
    saved = {profile:[fingerprint(export['discs'][0])]}
    save(root/'network-preview.json', dict(keptBuildDiscs=saved))
    planned = preview(root, **options)
    before = preview(root, verified_game=True, **options)
    assert next(r for r in before['discs'] if r['id']=='scan-0')['decision'] == '弃置候选'
    merged = preview(root, verified_game=True, network_plan=planned, **options)
    rows = {r['id']:r for r in merged['discs']}
    assert merged['keptBuildDiscs'] == saved
    assert rows['scan-0']['decision'] == '保留' and '已保存配装用盘' in rows['scan-0']['reasons']
    assert [(name, team['agent']) for name, team, _ in teams_for_disc(merged, rows['scan-0'])] == [(profile, '11号')]
    assert not teams_for_disc(merged, rows['scan-1'])
    assert not rows['scan-0'].get('upgrade')
    assert [rows['scan-'+str(i)]['decision'] for i in (1,2,3)] == ['弃置候选', '已弃置', '待复核']
    marker = Marker.__new__(Marker)
    marker.rows = {r['fp']:r for r in merged['discs']}
    marker.scope = None
    marker.cfg = cfg
    marker.seen_counts = Counter(fingerprint(d) for d in game_discs)
    kept, discarded, historical, _ = map(fingerprint, game_discs)
    assert set(marker.phase_targets('lock')) == {kept}
    assert set(marker.phase_targets('mark')) == {discarded}
    batch = prepare_batch(game, merged, {}, 'A及以下', before_marking=True)
    assert set(batch['markCandidates']) == {discarded} and [r['fp'] for r in batch['entries']] == [historical]

    save(root/'inventory.json', snapshot(game_discs[:-1]))
    try:
        preview(root, verified_game=True, network_plan=planned, **options)
    except ValueError as exc:
        assert '刷新库存' in str(exc)
    else:
        raise AssertionError('stale export reached marking')

    # Relabelling a second physical copy must not make the saved disc look unique.
    guard = inventory_guard(root)
    guard()
    copies = [*export['discs'], dict(export['discs'][0], id='net-copy')]
    save(root/'network-inventory.json', dict(export, discs=copies))
    try:
        guard()
    except ValueError as exc:
        assert '网络库存已更新' in str(exc)
    else:
        raise AssertionError('replaced export accepted during marking')
    save(root/'inventory.json', snapshot([*game_discs, dict(copies[-1], id='scan-copy', observedGrade='S')]))
    current = preview(root, **options)
    merged = preview(root, verified_game=True, network_plan=current, **options)
    assert all(r['review'] for r in merged['discs'] if r['id'] in ('scan-0', 'scan-copy'))
    assert [r['id'] for r in merged['discs'] if r['decision'] == '弃置候选'] == ['scan-1']
    print('PASS saved loadout, surplus, original discard, changed stat, duplicate and replaced export')


if __name__ == '__main__':
    home = Path(sys.argv[1])
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home/'src'), str(home/'plugins')]
    with TemporaryDirectory() as temp:
        smoke(Path(temp)/'1')
