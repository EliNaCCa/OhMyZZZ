"""Three synthetic warehouse pages, no game inputs or real inventory."""
import argparse
import sys
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def smoke(home, root):
    import numpy as np
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home/'src'), str(home/'plugins')]
    from one_dragon.base.geometry.point import Point
    from drive_disc_manager.live import Marker

    marker = Marker.__new__(Marker)
    marker.root = root
    marker.observed = {}
    page = 0
    center = Point(430, 580)
    point = Point(167, 400)
    marker.grid = SimpleNamespace(_detect_points=lambda frame:[point], _resolve_grid=lambda pts:([[point]], ''),
                                  _ensure_selected=lambda *args:True, _stable_screenshot=lambda:None,
                                  _scroll_list_center=lambda:center)
    def frame():
        image = np.zeros((1080,1920,3), dtype=np.uint8)
        # Cards look identical across pages. Only the scrollbar thumb moves.
        top = 237 + 20 * page
        image[top:top+27,1361:1366] = 184
        return image
    marker.frame = frame
    marker.detail = lambda: ('keep', 'unlocked', 'unmarked', False) if page == 2 else None
    calls = []
    def scroll(amount, pos):
        nonlocal page
        calls.append((amount, pos.x, pos.y))
        page = min(page + 1, 2) if amount > 0 else max(page - 1, 0)
    marker.app = SimpleNamespace(ctx=SimpleNamespace(controller=SimpleNamespace(scroll=scroll)))
    matched = []
    def handle(mode, detail, row):
        matched.append((mode, detail[0], row))
        return True
    marker.handle_disc = handle
    with patch('drive_disc_manager.live.time.sleep'):
        marker.apply('lock', {'keep':'target row'})
        assert matched == [('lock', 'keep', 'target row')]
        assert calls == [(3, center.x, center.y)] * 2
        calls.clear()
        try:
            marker.apply('mark', {'missing':'unresolved row'})
        except ValueError as exc:
            assert '1 张' in str(exc) and '未移动' in str(exc)
        else:
            raise AssertionError('lost target reported success at end of list')
        assert len(calls) == 2 and len(matched) == 1
        # A phase with one stale position and one valid position should revisit
        # only the outstanding item, including when the viewport is near the end.
        from contextlib import nullcontext
        marker.app.account, marker.app.group = 1, 'one_dragon'
        marker.report = dict(issues=[], rules='sample', discs=[{},{}])
        marker.cfg, marker.history = {}, {}
        marker.guard = lambda:None
        marker.scanner = SimpleNamespace(_ensure_screen_areas=lambda:None)
        marker.positions = {'done':['one position']}
        marker.phase_targets = lambda mode: {'done':{}, 'first':{}} if mode == 'lock' else {}
        marker.detail = lambda: ('first', 'locked', 'unmarked', False) if page == 0 else None
        def visit(mode, todo):
            nonlocal page
            assert mode == 'lock' and set(todo) == {'first','done'}
            del todo['done']
            page = 2
        marker.visit_positions = visit
        with (patch('drive_disc_manager.live.marker_conflict', return_value=''),
              patch('drive_disc_manager.live.enter_warehouse', return_value=2),
              patch('zzz_od.operation.back_to_normal_world.BackToNormalWorld') as world,
              patch('drive_disc_manager.manual_equip.guarded_grid', return_value=nullcontext())):
            world.return_value.execute.return_value.success = True
            marker.run()
        assert page == 0 and matched[1:] == [('lock','first',{})]

        # Daily inspection already selected acquisition order. The first target
        # is on page three; the second phase must start back at the top.
        marker.scope = {'1001', '1002'}
        marker.positions = {}
        marker.phase_targets = lambda mode: {mode:mode} if mode in ('lock', 'mark') else {}
        marker.detail = lambda: (('lock' if page == 2 else 'mark'), 'unlocked', 'unmarked', False)
        matched.clear()
        calls.clear()
        with (patch('drive_disc_manager.live.marker_conflict', return_value=''),
              patch('drive_disc_manager.live.enter_warehouse', return_value=2) as warehouse,
              patch('zzz_od.operation.back_to_normal_world.BackToNormalWorld') as world,
              patch('drive_disc_manager.warehouse_sort.newest_first') as sort,
              patch('drive_disc_manager.manual_equip.guarded_grid', return_value=nullcontext())):
            marker.run()
            assert matched == [('lock', 'lock', 'lock'), ('mark', 'mark', 'mark')]
            from drive_disc_manager.store import read
            assert read(root/'last-marking.json')['newlyMarked'] == []  # The stub did not click any mark.
            assert [amount for amount, *_ in calls] == [3, 3, -120, -120]
            assert warehouse.call_count == 2
            world.assert_not_called()
            sort.assert_not_called()

            def reenter(app, guard, *, trace):
                trace.append('菜单')
                return 2
            warehouse.side_effect = reenter
            marker.phase_targets = lambda mode: {'mark':'mark'} if mode == 'mark' else {}
            marker.run()
            sort.assert_called_once_with(marker.scanner, 2, marker.guard)

            warehouse.side_effect = None
            warehouse.return_value = 3
            matched.clear()
            calls.clear()
            try:
                marker.run()
            except ValueError as exc:
                assert '数量' in str(exc), exc
            else:
                raise AssertionError('accepted stale warehouse count')
            assert not matched and not calls


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    with TemporaryDirectory() as tmp:
        smoke(parser.parse_args().home.resolve(), Path(tmp))
    print('PASS three-page traversal, same-warehouse lock and discard, re-entry sorting and changed count')
