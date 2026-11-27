"""Match a scanner click to its card, with one on-page retry when needed."""
from contextlib import contextmanager

from .scan_copies import selected_position, viewport_stamp
from .warehouse import warehouse_header


@contextmanager
def park_off_grid(grid):
    """Redirect this grid's corner parking point on fullscreen desktops."""
    ctx = grid.ctx

    class Controller:
        def mouse_move(self, point):
            if (point.x, point.y) == (0, 0):
                point = type(point)(40, 182)
            return ctx.controller.mouse_move(point)

        def __getattr__(self, name):
            return getattr(ctx.controller, name)

    class GridContext:
        controller = Controller()

        def __getattr__(self, name):
            return getattr(ctx, name)

    grid.ctx = GridContext()
    try:
        yield
    finally:
        grid.ctx = ctx


def confirmed_frame(scanner, grid, row, col, frame, guard):
    from one_dragon.base.geometry.point import Point

    target = grid.grid_rows[row][col]
    # Upstream detects the colored strip below the art. A low hit on that
    # strip can land just outside the selected border's inner margin.
    center = Point(target.x, target.y - 44)
    selected = selected_position(frame, target) or selected_position(frame, center)
    if selected is not None or guard is None:
        return frame, selected

    total = getattr(scanner, '_disc_game_total', None)
    if (type(total) is not int or total <= 0 or frame.shape[:2] != (1080, 1920) or
            col not in range(9) or abs(center.x - (167 + 135 * col)) > 20 or not 220 <= center.y <= 900):
        return frame, None
    guard()
    page = viewport_stamp(frame, col)
    if page is None or warehouse_header(scanner.ctx.ocr, frame) != ('驱动仓库', total):
        return frame, None
    # Fitting can extrapolate an empty cell. Only try a real detected card.
    if not any(abs(hit.x - target.x) < 20 and abs(hit.y - target.y) < 20 for hit in grid._detect_points(frame)):
        return frame, None
    guard()
    before = grid.screenshot()
    if (before is None or before.shape[:2] != (1080, 1920) or viewport_stamp(before, col) != page or
            warehouse_header(scanner.ctx.ocr, before) != ('驱动仓库', total)):
        return frame, None
    guard()
    if not grid.ctx.controller.click(center):
        return frame, None
    guard()
    grid.ctx.controller.mouse_move(Point(40, 182))
    current = grid._stable_screenshot()
    guard()
    if (current is None or current.shape[:2] != (1080, 1920) or
            warehouse_header(scanner.ctx.ocr, current) != ('驱动仓库', total) or viewport_stamp(current, col) != page):
        return frame, None
    selected = selected_position(current, center)
    return (current, selected) if selected is not None else (frame, None)
