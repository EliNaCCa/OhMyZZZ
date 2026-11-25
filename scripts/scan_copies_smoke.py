"""Two synthetic physical copies and one overlapping page. No running game."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager.planner import plan
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
        for evidence in ([disc, overlap], [disc, dict(copy, scanCell=(0, 1))], [disc, dict(copy, observedValues=None)]):
            try:
                import_inventory(root, source, readings=evidence)
            except ValueError as exc:
                assert '游戏内 2，导出 1' in str(exc), exc
            else:
                raise AssertionError('Unproved duplicate was counted')
        assert read(root/'inventory.json') == saved and read(source)['audit']['delta'] == -1
    print('PASS two co-visible copies preserved with separate states, overlap rejected, previous inventory retained')


if __name__ == '__main__':
    smoke()
