"""Compare the best a disc can still roll with the weakest spare for each use."""
from collections import defaultdict

from .reserve_policy import SELECTORS, matching_rules, max_useful_rolls, refresh_upgrade, spare_uses, upgrade_advice, usable_stats


def group_key(row):
    return tuple(row[k] for k in SELECTORS)


def comparable(row):
    personal_lock = row.get('scannedLock') == 'locked' and not row.get('managedSpareLock')
    return (not row['marked'] and not row['review'] and not row['ambiguous'] and row.get('development')
            and not row.get('location') and not row.get('manualUnlock') and not row.get('manualKeep')
            and row.get('observedValues') and not personal_lock and '手动保护' not in row['reasons']
            and '已保存配装用盘' not in row['reasons']
            and not any(r.startswith('配队 · ') for r in row['reasons']))


def spare_floor(row, limits, ranked_scores, exclude):
    from itertools import islice
    floor = []
    for label, count, stats in spare_uses(row, limits):
        keys = usable_stats(row, stats)
        if count is None:
            floor.append(dict(label=label, stats=keys, minimum=0))
        elif keys:
            peers = list(islice((score for score, fp in ranked_scores(keys) if fp not in exclude), count))
            floor.append(dict(label=label, stats=keys, minimum=peers[count-1] if len(peers) >= count else 0))
    return floor


def ranked_spares(group):
    from functools import cache
    @cache
    def scores(keys):
        return sorted(((sum(p['rolls'].get(k, 0) for k in keys), p['fp']) for p in group
                       if p['decision'] == '保留'), reverse=True)
    return scores


def benchmarks(rows, cfg):
    groups = defaultdict(list)
    for row in rows:
        if comparable(row):
            groups[group_key(row)].append(row)
    out = {}
    for group in groups.values():
        limits, _ = matching_rules(group[0], cfg)
        scores = ranked_spares(group)
        for row in group:
            out[row['fp']] = spare_floor(row, limits, scores, {row['fp']})
    return out


def saved_comparison(root, cfg, rules):
    from datetime import datetime
    from .store import digest, inventory, preview_path, read, snapshot_path
    source = snapshot_path(root, cfg)
    snapshot = read(source, {})
    report = read(preview_path(root, cfg), {})
    if (not snapshot or not report.get('generated') or report.get('issues') or
            report.get('inventory') != digest(snapshot) or report.get('configuration') != digest(cfg) or
            report.get('rules') != rules.get('version')):
        return None
    try:
        inventory(snapshot, preview_only=True)
    except ValueError:
        return None
    if cfg.get('inventorySource') == 'cn_export':
        from .cn_export import source_path
        if str(source_path(root, snapshot)) != snapshot.get('exportFile'):
            return None
    rows = report['discs']
    if (len(rows) != len(snapshot['discs']) or
            {r['id'] for r in rows} != {disc['id'] for disc in snapshot['discs']}):
        return None
    groups = defaultdict(list)
    for row in rows:
        if comparable(row) and row['decision'] == '保留':
            groups[group_key(row)].append(row)
    # Recalculating the preview doesn't change the age of its inventory.
    written = snapshot.get('exportWrittenNs')
    if written:
        stamp = '国服导出 ' + datetime.fromtimestamp(written / 1_000_000_000).strftime('%m-%d %H:%M')
    else:
        saved = snapshot.get('observedAt') or source.stat().st_mtime
        stamp = '库存记录 ' + datetime.fromtimestamp(saved).strftime('%m-%d %H:%M')
    return groups, stamp


def probe_floor(row, limits, comparison):
    # The physical observation has no UID. Exclude all possible copies of it,
    # including an older scanner spelling the set name in Chinese.
    from .store import is_pinned
    groups, _ = comparison
    keys = set(row.get('pinKeys') or [row['fp']])
    peers = [p for p in groups.get(group_key(row), ()) if not is_pinned(p, keys)
             and (p['level'], p['observedValues']) != (row['level'], row['observedValues'])]
    return spare_floor(row, limits, ranked_spares(peers), ())


def verdict(level, rolls, floor):
    if not floor:
        return ''
    margins = []
    for use in floor:
        maximum = max_useful_rolls(level, rolls, use['stats'])
        if maximum >= use['minimum']:
            return ''
        margins.append((use['minimum']-maximum, use['label'], maximum, use['minimum']))
    _, label, maximum, minimum = min(margins)
    return f'{label}最多 {maximum} 档有效词条，同类备用已有 {minimum} 档'


def protected(row, cfg):
    return (row.get('location') or row['review'] or row['ambiguous'] or row.get('manualUnlock') or row.get('manualKeep')
            or (row['level'] == 15 and cfg.get('protect15'))
            or (row['scannedLock'] == 'locked' and not row.get('managedSpareLock'))
            or any(r in ('手动保护','已保存配装用盘','四词条双暴胚子') or r.startswith('配队 · ') for r in row['reasons']))


def evaluate(rows, cfg):
    execute_trials = cfg.get('autoUpgrade', False)
    if not execute_trials and not any(row.get('upgrade') for row in rows):
        return
    floors = benchmarks(rows, cfg)
    for row in rows:
        if row['fp'] not in floors:
            continue
        floor = floors[row['fp']]
        if row.get('upgrade'):
            refresh_upgrade(row, cfg, floor)
        if not execute_trials:
            continue
        row['upgradeFloor'] = floor
        if protected(row, cfg):
            continue
        reason = verdict(row['level'], row['rolls'], floor)
        if not reason:
            if (row['level'] < 15 and row['decision'] != '保留' and not row['reasons'] and floor
                    and '超出同类备用名额' in row['explanation'] and row['recommendations']):
                limits, _ = matching_rules(row, cfg)
                advice = upgrade_advice(row, limits, floor)
                if advice or row['level'] == 3:
                    row.update(decision='保留', reasons=['试强化'] if advice else ['+3 胚子留作备用'])
                    row.pop('checkForDiscard', None)
                    row['explanation'] = [s for s in row['explanation'] if s not in
                                          ('超出同类备用名额', '超出留盘名额，穿戴和标记待游戏内核对')]
                    if advice:
                        row['upgrade'] = advice
                        row['explanation'].append(advice['reason'])
            continue
        row['decision'] = '弃置候选'
        row['reasons'] = []
        row['outgrown'] = True
        old = row.pop('upgrade', None)
        if old and old['reason'] in row['explanation']:
            row['explanation'].remove(old['reason'])
        row['explanation'].append(reason)
