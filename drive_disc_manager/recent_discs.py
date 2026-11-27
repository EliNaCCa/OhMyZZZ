"""Read the newest visible discs under the current warehouse filter."""
from time import sleep, time

from .disc_probe import read_selected
from .manual_equip import guarded_grid
from .scan_copies import scroll_thumb
from .stat_reading import checked_values, format_stats
from .warehouse import warehouse_header
from .warehouse_sort import newest_first


def leave_disc_detail(scanner, guard):
    from one_dragon.base.geometry.point import Point
    from .upgrade_ui import back_button, tokens
    # The previous preview can leave the material picker open. Back out of
    # recognized disc screens only, leaving any selected materials unspent.
    visited = set()
    for _ in range(2):
        guard()
        shot = scanner.screenshot()
        if shot is None or shot.shape[:2] != (1080, 1920) or warehouse_header(scanner.ctx.ocr, shot)[0]:
            return
        words = tokens(scanner.ctx.ocr, shot)
        back = back_button(words, shot)
        if not back or back in visited:
            return
        visited.add(back)
        guard()
        if not scanner.ctx.controller.click(Point(*back)):
            raise ValueError('驱动盘页面没有关闭')
        sleep(.7)


def read_front_row(scanner, total, guard, count, *, previous=None):
    from one_dragon.base.geometry.point import Point
    if type(count) is not int or not 1 <= count <= 9:
        raise ValueError('一次可以看 1 到 9 张盘')
    if total == 0:
        return []
    old = previous or {}
    baseline = old.get('warehouseCount')
    gain = total - baseline if type(baseline) is int else 0
    # In daily mode a disposal can hide a drop in the net count. Look for
    # the previous leading pair as well. This only bounds a read-only list.
    initial = min(total, 36, max(count, gain))
    anchor = [d.get('fingerprint') for d in old.get('discs', [])[:2]]
    if len(anchor) != 2 or not all(anchor) or anchor[0] == anchor[1]:
        anchor = []
    limit = min(total, 36) if anchor else initial
    if not newest_first(scanner, total, guard):
        raise ValueError('这次没能确认哪一端是最新的盘')

    with guarded_grid(scanner.drive_disk_scanner, guard) as grid:
        grid.ctx.controller.scroll(-120, grid._scroll_list_center())
        sleep(.5)
        guard()
        shot = scanner.screenshot()
        if shot is None or shot.shape[:2] != (1080, 1920) or warehouse_header(scanner.ctx.ocr, shot) != ('驱动仓库', total):
            raise ValueError('请打开驱动仓库后再读')
        if total > 36:
            thumb = scroll_thumb(shot)
            if thumb is None or thumb[1] > 7:
                raise ValueError('没读到仓库顶部的滚动条')
        points = grid._detect_points(shot)
        if not points:
            raise ValueError('没读到当前筛选下的盘位')
        rows, _ = grid._resolve_grid(points)
        discs = []
        cells = [(r, c, point) for r, line in enumerate(rows[:4]) for c, point in enumerate(line[:9])]
        overlap = None
        for idx, (page_row, col, point) in enumerate(cells[:limit]):
            center = Point(167 + 135 * col, 275 + 174 * page_row)
            if (idx != page_row * 9 + col or abs(point.x - center.x) >= 20 or abs(point.y - center.y - 44) >= 20
                    or not any(abs(hit.x-point.x) < 20 and abs(hit.y-point.y) < 20 for hit in points)):
                break
            if not grid.ctx.controller.click(point):
                raise ValueError('没能选中第 %d 张盘' % (idx + 1))
            grid.ctx.controller.mouse_move(Point(40, 182))
            sleep(.35)
            grid._stable_screenshot()
            # Verify the selection at the center of the requested card.
            disc = read_selected(scanner, total, guard, target=center)
            if not checked_values(disc, 1):
                raise ValueError('第 %d 张盘的词条没读清' % (idx + 1))
            discs.append(disc)
            if anchor and len(discs) - 2 >= gain and overlap is None and [d['fingerprint'] for d in discs[-2:]] == anchor:
                overlap = len(discs) - 2
            if overlap is not None and len(discs) >= initial:
                return discs[:max(initial, overlap)]
        if not discs:
            raise ValueError('没读到第一张盘')
        return discs


def daily_read(app, pinned, wiki):
    from .scanner_status import scanner_status
    from .store import read, save
    from .warehouse import enter_warehouse

    ctx = app.ctx
    def guard():
        if ctx.current_instance_idx != app.account or not ctx.run_context.is_context_running:
            raise ValueError('任务已停止或账号已切换')
        if not ctx.controller.game_win.is_win_active:
            raise ValueError('游戏窗口已失去焦点')

    guard()
    ready, reason = scanner_status(ctx, app.account)
    if not ready:
        raise ValueError(reason)
    scanner = ctx.run_context.get_application('inventory_scan', app.account, app.group)
    scanner._ensure_screen_areas()
    started = time()
    leave_disc_detail(scanner, guard)
    total = enter_warehouse(scanner, guard)
    previous = read(app.root/'recent-discs.json', {})
    if previous.get('status') != 'verified' or str(previous.get('account')) != str(app.account):
        previous = {'warehouseCount': read(app.root/'scan-observations.json', {}).get('total')}
    discs = read_front_row(scanner, total, guard, 3, previous=previous)
    guard()
    record = dict(account=app.account, started=started, finished=time(), status='verified',
                  warehouseCount=total, discs=discs, rules=pinned[1].get('version'))
    record['rows'] = describe(app.root, record, read(wiki, {}), pinned=pinned)
    guard()
    save(app.root/'recent-discs.json', record)
    return len(discs)


def describe(root, record, wiki, *, pinned=None):
    from .disc_trace import probe_trace
    from .potential import saved_comparison
    from .store import config, equipment_names, known_agents, read
    cfg, rules = pinned[:2] if pinned else (config(root), read(root/'rules.json', {}))
    names = equipment_names(wiki) | cfg.get('aliases', {})
    agents = known_agents(root)
    comparison = saved_comparison(root, cfg, rules)
    observations = {} if comparison else read(root/'scan-observations.json', {})
    rows = []
    for disc in record['discs']:
        row, _ = probe_trace(dict(status='verified', disc=disc), cfg, rules, wiki, agents, comparison=comparison)
        notes = row['reasons'] + row['explanation'] + row['review']
        rows.append(dict(name=names.get(row['set'], row['set']), slot=row['slot'], level=row['level'],
                         main=format_stats(row['observedValues']['main']), subs=format_stats(row['observedValues']['subs']),
                         suggestion=row.get('probeAdvice', '待比较同类盘'),
                         advice='、'.join(dict.fromkeys(notes)) or '留盘要和仓库里的同类盘比较'))
        if comparison:
            rows[-1]['basis'] = comparison[1]
        elif row.get('trialUse') and observations:
            reference = observed_peer(row, observations, names)
            if reference:
                rows[-1]['reference'] = reference
    return rows


def observed_peer(row, observations, names):
    """Pick an example from a past scan for display. Keep quota decisions in the planner."""
    from datetime import datetime
    from .store import equipment_key, equipment_keys
    reverse = equipment_keys(names)
    use = row['trialUse']
    best_score = 0
    best = None
    for disc in observations.get('rows', []):
        if (disc.get('observedGrade') != 'S' or disc.get('rarity') != 'S' or disc.get('ownerState') != 'bag'
                or disc.get('location') or disc.get('markState') != 'unmarked' or disc.get('lockState') != 'unlocked'
                or str(disc.get('slotKey')) != row['slot'] or disc.get('mainStatKey') != row['main']
                or equipment_key(disc['setKey'], names, reverse) != row['set']):
            continue
        if not isinstance(disc.get('substats'), list):
            continue
        try:
            values = checked_values(disc, 1)
        except (TypeError, KeyError):
            continue
        if not values or (disc['level'], values) == (row['level'], row['observedValues']):
            continue
        score = sum(s['upgrades'] for s in disc['substats'] if s['key'] in use['stats'])
        if score > best_score:
            best, best_score = disc, score
    if best is None:
        return None
    subs = {k:v for k,v in best['observedValues']['subs'].items() if k in use['stats']}
    stamp = datetime.fromtimestamp(observations['at']).strftime('%m-%d')
    return f"{stamp} 扫到的同类盘 +{best['level']} · {format_stats(subs)}（{use['purpose']}有效 {best_score} 档，这张 {use['rolls']} 档）"
