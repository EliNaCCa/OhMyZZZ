"""View incomplete game readings without publishing an actionable inventory."""
from datetime import datetime

from .stat_reading import checked_values, format_stats
from .store import observed_agents, read, remember_agents, save


def save_interrupted_scan(root, scanner, observed_at):
    """Use only this invocation's worker and its warehouse header, never the shared exports directory."""
    import logging
    try:
        worker = scanner.ocr_worker
        total = getattr(scanner, '_disc_game_total', None)
        scan = dict(source='Zenless Optimizer', audit=dict(game_total=total, partial=True),
                    discs=list(worker.scanned_discs), characters=list(getattr(worker, 'scanned_agents', ())))
        save_observations(root, scan, observed_at)
    except Exception:
        # Saving optional readings must not hide the game's original failure.
        logging.getLogger(__name__).exception('未能保存本轮扫描记录')


def save_observations(root, scan, observed_at, *, recount=None):
    rows = scan.get('discs', [])
    total = (scan.get('audit') or {}).get('game_total')
    if scan.get('source') != 'Zenless Optimizer' or type(total) is not int or total < 1:
        return
    agents = observed_agents({'characters':scan.get('characters', [])})
    rows = [d for d in rows if isinstance(d, dict) and d.get('setKey') and str(d.get('slotKey')) in tuple('123456')
            and type(d.get('level')) is int]
    if rows:
        record = dict(at=observed_at, total=total, rows=rows)
        if recount:
            record['recount'] = recount
        save(root/'scan-observations.json', record)
        worn = [d for d in rows if d.get('ownerState') == 'worn' and d.get('observedGrade') in ('S','A','B')
                and d['observedGrade'] == d.get('rarity') and isinstance(d.get('substats'), list)
                and d.get('mainStatKey') and checked_values(d, 1)]
        agents.update(observed_agents({'discs':worn}))
    remember_agents(root, agents)


def show_observations(grid, fill, root, enabled, agents):
    data = read(root/'scan-observations.json', {}) if enabled else {}
    rows = data.get('rows', [])
    grid.setVisible(bool(rows))
    if not rows:
        grid.setRowCount(0)
        return '', False
    def label(row):
        values = row.get('observedValues') or {}
        owner = row.get('location', '')
        return (row['setKey'], row['slotKey'], f"+{row['level']}", format_stats(values.get('main', {})) or '未读清',
                format_stats(values.get('subs', {})) or '未读清', agents.get(owner, owner) or
                ('未装备' if row.get('ownerState') == 'bag' else '未读清'))
    fill(grid, [label(r) for r in rows])
    for col, width in enumerate((140, 56, 56, 128, 365)):
        grid.setColumnWidth(col, width)
    day = datetime.fromtimestamp(data['at']).strftime('%m-%d %H:%M')
    return f"{day} 扫描记录 · {len(rows)} 条（仓库共 {data['total']} 张）", bool(data.get('recount'))
