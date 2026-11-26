"""Three synthetic warehouse pages, no game inputs or real inventory."""
import argparse
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def smoke(home):
    import numpy as np
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home/'src'), str(home/'plugins')]
    from one_dragon.base.geometry.point import Point
    from drive_disc_manager.live import Marker

    marker = Marker.__new__(Marker)
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
        top = 247 + 20 * page
        image[top:top+27,1361:1366] = 184
        return image
    marker.frame = frame
    marker.detail = lambda: ('keep', 'unlocked', 'unmarked', False) if page == 2 else None
    calls = []
    def scroll(amount, pos):
        nonlocal page
        calls.append((amount, pos.x, pos.y))
        page = min(page + 1, 2)
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


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    smoke(parser.parse_args().home.resolve())
    print('PASS marker reached third page and reported one unresolved target at the stationary end')
