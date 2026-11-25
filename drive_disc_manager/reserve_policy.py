"""Rank spare discs after the planner has identified gear and rule coverage."""
from collections import defaultdict


DEFAULT_STATS = ('crit_', 'crit_dmg_', 'atk_')
SELECTORS = ('set', 'slot', 'main')


def development(row):
    """Normalize S-rank history into original lines and remaining upgrade events."""
    spent = row['level'] // 3
    visible = len(row['rolls'])
    initial = sum(row['rolls'].values()) - spent
    if initial not in (3, 4) or visible != min(initial + spent, 4):
        return None
    return dict(initial=initial, remaining=5-spent, upgrades=5-spent-(4-visible))


def quota(value):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError('备用数量应为非负整数或 null（不限）')
    return value


def matching_rules(group, cfg):
    limits = {'通用': (-1, quota(cfg['reserve']), DEFAULT_STATS)}
    trim = (-1, cfg.get('trimRecommended', False))
    for rule in cfg['reserve_rules']:
        selectors = {k: str(rule[k]) for k in SELECTORS if rule.get(k) not in (None, '', '*')}
        if any(group[k] != v for k, v in selectors.items()):
            continue
        rank = len(selectors)
        if 'trimRecommended' in rule and rank >= trim[0]:
            trim = rank, rule['trimRecommended']
        purpose = rule.get('purpose') or '通用'
        if purpose not in limits or rank >= limits[purpose][0]:
            limits[purpose] = rank, quota(rule['count']), rule.get('stats', DEFAULT_STATS)
    return limits, trim[1]


def apply_reserves(rows, cfg):
    groups = defaultdict(list)
    for row in rows:
        if not row['marked']:
            groups[tuple(row[key] for key in SELECTORS)].append(row)
    report = []
    for key, group in groups.items():
        criteria = dict(zip(SELECTORS, key))
        limits, trim = matching_rules(criteria, cfg)
        eligible = []
        for row in group:
            if not trim:
                row['reasons'].extend(row['recommendations'])
            if not row['reasons'] and not row['review']:
                eligible.append(row)
        by_purpose = []
        selected = set()
        for purpose, (_, count, stats) in limits.items():
            def rank(row):
                rolls = sum(row['rolls'].get(stat, 0) for stat in stats)
                # For the inherited general quota, use the best matching role. A
                # player's explicit stat list still ranks by their chosen stats.
                hits = sum(stat in row['rolls'] for stat in stats)
                # The first event on a three-line piece reveals its fourth stat.
                # Only count future upgrades to stats already visible as a tiebreak.
                growth = row['development']['upgrades'] * hits
                base_rank = (rolls, growth, hits, row['fp'])
                if purpose == '通用' and stats is DEFAULT_STATS:
                    best_role = max(((score, row['development']['upgrades'] * row['roleHits'][agent])
                                     for agent, score in row.get('roleRolls', {}).items()), default=(rolls, growth))
                    return (bool(row['recommendations']), best_role, *base_rank)
                return base_rank
            matching = (row for row in eligible if purpose == '通用' or any(stat in row['rolls'] for stat in stats))
            picks = sorted(matching, key=rank, reverse=True)[:count]
            for row in picks:
                row['reasons'].append('备用 · ' + purpose)
                selected.add(row['fp'])
            by_purpose.append(dict(purpose=purpose, count=count, covered=len(picks)))
        for row in eligible:
            if row['fp'] not in selected:
                row['explanation'].append('超出同类备用名额')
        report.append(dict(**criteria, trimRecommended=trim, eligible=len(eligible),
                           kept=len(selected), purposes=by_purpose))
    return report
