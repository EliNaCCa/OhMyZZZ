"""Compare the best a disc can still roll with the weakest spare for each use."""
from collections import defaultdict

from .reserve_policy import DEFAULT_STATS, SELECTORS, matching_rules, normalized_rule
from .store import equipment_keys


def group_key(row):
    return tuple(row[k] for k in SELECTORS)


def uses(row, limits):
    for purpose, (_, count, stats) in limits.items():
        if count == 0:
            continue
        if purpose == '通用' and stats is DEFAULT_STATS and row.get('roleStats'):
            for role, preferred in row['roleStats'].items():
                yield role, count, preferred
        else:
            yield purpose, count, stats


def benchmarks(rows, cfg, names):
    groups = defaultdict(list)
    for row in rows:
        if (not row['marked'] and not row['review'] and not row['ambiguous'] and row.get('development')
                and not row.get('location') and not row.get('manualUnlock') and not row.get('manualKeep')
                and row.get('observedValues') and '已保存配装用盘' not in row['reasons']
                and not any(r.startswith('配队 · ') for r in row['reasons'])):
            groups[group_key(row)].append(row)
    reverse = equipment_keys(names)
    cfg = dict(cfg, reserve_rules=[normalized_rule(r, reverse) for r in cfg['reserve_rules']])
    out = {}
    for group in groups.values():
        limits, _ = matching_rules(group[0], cfg)
        cache = {}
        for row in group:
            floor = []
            for label, count, stats in uses(row, limits):
                if count is None:
                    floor = []
                    break
                keys = tuple(sorted(set(stats)-{row['main']}))
                if not keys:
                    continue
                # Include one extra so a disc is never compared with itself.
                if (keys, count) not in cache:
                    scores = sorted(((sum(p['rolls'].get(k, 0) for k in keys), p['fp']) for p in group
                                     if p['decision'] == '保留'), reverse=True)
                    cache[keys, count] = scores[:count+1]
                peers = [score for score, fp in cache[keys, count] if fp != row['fp']]
                floor.append(dict(label=label, stats=keys, minimum=peers[count-1] if len(peers) >= count else 0))
            out[row['fp']] = floor
    return out


def verdict(level, rolls, floor):
    if not floor:
        return ''
    remaining = 5 - level // 3
    margins = []
    for use in floor:
        keys = set(use['stats'])
        good = sum(rolls.get(k, 0) for k in keys)
        # A three-line drop can reveal a useful fourth stat. With four lines,
        # future levels can only hit existing stats.
        maximum = good + (remaining if good or len(rolls) == 3 else 0)
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


def evaluate(rows, cfg, names):
    if not cfg.get('autoUpgrade'):
        return
    floors = benchmarks(rows, cfg, names)
    for row in rows:
        if row['fp'] not in floors or protected(row, cfg):
            continue
        floor = floors[row['fp']]
        row['upgradeFloor'] = floor
        reason = verdict(row['level'], row['rolls'], floor)
        if not reason:
            continue
        row['decision'] = '弃置候选'
        row['reasons'] = []
        row['outgrown'] = True
        old = row.pop('upgrade', None)
        if old and old['reason'] in row['explanation']:
            row['explanation'].remove(old['reason'])
        row['explanation'].append(reason)
