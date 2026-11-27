"""Two synthetic physical copies and one overlapping page. No running game."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager.planner import plan
from drive_disc_manager.scan_copies import with_copies
from drive_disc_manager.store import defaults, import_inventory, read, save


def smoke():
    disc = dict(id='first', setKey='SwingJazz', slotKey='1', rarity='S', level=0, mainStatKey='hp',
                substats=[dict(key=k, upgrades=1) for k in ('crit_', 'pen', 'anomProf')],
                observedValues=dict(main={'hp':550}, subs={'crit_':2.4, 'pen':9, 'anomProf':9}),
                markState='unmarked', trash=False, lockState='unlocked', lock=False, scanCell=(0, 1))
    copy = dict(disc, id='copy', lockState='locked', lock=True, scanCell=(0, 2))
    overlap = dict(copy, id='seen-again', scanCell=(1, 2))
    export = dict(format='ZOD', dbVersion=2, version=1, source='Zenless Optimizer',
                  discs=[disc], audit=dict(game_total=2, exported=1, delta=-1), characters=[], wengines=[])
    # Replay two consecutive views of the same column. The second view can
    # include the first view's bottom card, plus one otherwise identical card.
    warehouse = dict(export, audit=dict(game_total=1302, exported=1, delta=-1301))
    def sight(page, y, scroll, *, height=18, stamp=None, x=8):
        stamp = stamp or str(page)*64
        return dict(disc, scanCell=(page, 1, y, stamp, (x, scroll, 5, height)))
    def count(*cells):
        rebuilt = with_copies(warehouse, cells)
        assert rebuilt['audit']['delta'] == len(rebuilt['discs']) - 1302
        return len(rebuilt['discs'])
    assert count(sight(0, 450, 100), sight(1, 450, 113)) == 2
    assert count(sight(0, 798, 100), sight(1, 275, 113)) == 1
    assert count(sight(0, 275, 100), sight(0, 798, 100), sight(1, 275, 113), sight(1, 798, 113)) == 3
    for changed in (sight(1, 450, 100), sight(1, 450, 102), sight(1, 450, 92),
                    sight(1, 450, 113, height=20), sight(1, 450, 113, x=7)):
        assert count(sight(0, 450, 100), changed) == 1
    # A viewport conflict on a different card also breaks the evidence chain.
    other = dict(sight(1, 450, 110), setKey='Other')
    inconsistent = dict(other, scanCell=(1, 1, 624, 'f'*64, (8, 110, 5, 18)))
    assert count(sight(0, 450, 100), other, inconsistent, sight(2, 450, 120)) == 1
    assert count(sight(0, 450, 100), dict(sight(1, 450, 130), setKey='Other'), sight(2, 450, 120)) == 1
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root/'scanner.json'
        save(source, export)
        assert import_inventory(root, source, readings=[disc, copy, overlap]) == 2
        saved = read(root/'inventory.json')
        assert saved['audit']['samePageCopies'] == 1 and saved['audit']['scannerExported'] == 1
        result = plan(saved, defaults(), {}, {'SwingJazz':'摇摆爵士'})
        assert all(r['ambiguous'] and r['review'] and r['decision'] != '弃置候选' for r in result['discs'])
        assert [d['lock'] for d in saved['discs']] == [False, True]
        # Same column, different rows on a verified stationary viewport. A small
        # jitter of the border must never add another disc to the export.
        stamp = 'a'*64
        upper = dict(disc, scanCell=(0, 1, 319, stamp))
        lower = dict(copy, scanCell=(0, 1, 493, stamp))
        jitter = dict(upper, scanCell=(0, 1, 320, stamp))
        assert import_inventory(root, source, readings=[lower, jitter, upper]) == 2
        saved = read(root/'inventory.json')
        assert [d['lock'] for d in saved['discs']] == [False, True]
        assert all(d['ambiguous'] and d['review'] for d in plan(saved, defaults(), {}, {'SwingJazz':'摇摆爵士'})['discs'])
        for evidence in ([disc, dict(overlap, scanCell=(1, 1))], [disc, dict(copy, scanCell=(0, 1))], [disc, dict(copy, observedValues=None)],
                         [upper, jitter], [upper, dict(lower, scanCell=(0,1,493,'b'*64))],
                         [upper, dict(lower, scanCell=(1,1,493,stamp))], [upper, dict(lower, scanCell=(0,1))],
                         [dict(upper, scanCell=(0,1,319)), dict(lower, scanCell=(0,1,493))]):
            try:
                import_inventory(root, source, readings=evidence)
            except ValueError as exc:
                assert '游戏内 2，导出 1' in str(exc), exc
            else:
                raise AssertionError('Unproved duplicate was counted')
        assert read(root/'inventory.json') == saved and read(source)['audit']['delta'] == -1
        # Scrolling keeps each disc in its column. Seeing column 2 again is
        # an overlap, while the copy in column 1 counts even on another page.
        assert import_inventory(root, source, readings=[disc, overlap, dict(overlap, scanCell=(2, 2))]) == 2
        saved = read(root/'inventory.json')
        assert saved['audit']['samePageCopies'] == 0 and saved['audit']['crossPageCopies'] == 1
        assert {d['id'] for d in saved['discs']} == {'first', 'seen-again'}
        # Preserve proven copies for read-only inspection when another disc
        # is missing. The previous complete inventory is still authoritative.
        save(source, dict(export, audit=dict(game_total=3, exported=1, delta=-2)))
        try:
            import_inventory(root, source, readings=[disc, overlap])
        except ValueError as exc:
            assert '游戏内 3，导出 2' in str(exc)
        else:
            raise AssertionError('incomplete inventory accepted')
        observed = read(root/'scan-observations.json')
        assert observed['total'] == 3 and len(observed['rows']) == 2
        assert read(root/'inventory.json') == saved
        # The host dedup key also collapses A/S pieces with matching roll counts.
        gold = dict(upper, observedGrade='S')
        silver = dict(lower, observedGrade='A', rarity='A',
                      observedValues=dict(main={'hp':367}, subs={'crit_':1.6, 'pen':6, 'anomProf':6}))
        save(source, dict(export, discs=[gold]))
        assert import_inventory(root, source, readings=[gold,silver]) == 2
        saved = read(root/'inventory.json')
        assert {d['rarity'] for d in saved['discs']} == {'A','S'}
        for invalid in (dict(silver, observedGrade=None), dict(silver, observedValues=gold['observedValues']),
                        dict(silver, scanCell=gold['scanCell'])):
            try:
                import_inventory(root, source, readings=[gold,invalid])
            except ValueError:
                pass
            else:
                raise AssertionError('Unverified grade collision was accepted')
        assert read(root/'inventory.json') == saved
    print('PASS copies in distinct columns or verified rows retained for review; scroll overlap and repeated selections rejected')


if __name__ == '__main__':
    smoke()
