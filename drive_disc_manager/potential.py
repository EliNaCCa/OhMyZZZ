"""Compare the best a disc can still roll with the weakest spare for each use."""
from collections import defaultdict

from .optimizer import STATS
from .reserve_policy import SELECTORS, matching_rules, spare_uses, upgrade_advice


def group_key(row):
    return tuple(row[k] for k in SELECTORS)


def benchmarks(rows, cfg):
    groups = defaultdict(list)
    for row in rows:
        personal_lock = row.get('scannedLock') == 'locked' and not row.get('managedSpareLock')
        if (not row['marked'] and not row['review'] and not row['ambiguous'] and row.get('development')
                and not row.get('location') and not row.get('manualUnlock') and not row.get('manualKeep')
                and row.get('observedValues') and not personal_lock and '手动保护' not in row['reasons']
                and '已保存配装用盘' not in row['reasons']
                and not any(r.startswith('配队 · ') for r in row['reasons'])):
            groups[group_key(row)].append(row)
    out = {}
    for group in groups.values():
        limits, _ = matching_rules(group[0], cfg)
        cache = {}
        for row in group:
            floor = []
            for label, count, stats in spare_uses(row, limits):
                if count is None:
                    floor = []
                    break
                keys = tuple(sorted(k for k in set(stats) if k != row['main'] and STATS.get(k, (None, None))[1]))
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
        if len(rolls) == 3:
            # The first tier adds a distinct stat. Only later tiers can improve it.
            useful_reveal = bool(keys.difference(rolls))
            maximum = good + int(useful_reveal)
            if good or useful_reveal:
                maximum += remaining - 1
        else:
            maximum = good + (remaining if good else 0)
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
    if not cfg.get('autoUpgrade'):
        return
    floors = benchmarks(rows, cfg)
    for row in rows:
        if row['fp'] not in floors or protected(row, cfg):
            continue
        floor = floors[row['fp']]
        row['upgradeFloor'] = floor
        reason = verdict(row['level'], row['rolls'], floor)
        if not reason:
            if (row['level'] < 15 and row['decision'] != '保留' and not row['reasons'] and floor
                    and '超出同类备用名额' in row['explanation'] and row['recommendations']):
                limits, _ = matching_rules(row, cfg)
                advice = upgrade_advice(row, limits)
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
