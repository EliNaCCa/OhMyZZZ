"""Rank spare discs after the planner has identified gear and rule coverage."""
from collections import defaultdict


DEFAULT_STATS = ('crit_', 'crit_dmg_', 'atk_')
SELECTORS = ('set', 'slot', 'main')


def development(level, rolls):
    """Normalize S-rank history into original lines and remaining upgrade events."""
    spent = level // 3
    visible = len(rolls)
    initial = sum(rolls.values()) - spent
    if initial not in (3, 4) or visible != min(initial + spent, 4):
        return None
    return dict(initial=initial, remaining=5-spent, upgrades=5-spent-(4-visible))


def assess_growth(row, cfg):
    growth = development(row['level'], row['rolls'])
    if growth is None:
        row['review'].append('强化档数与等级不符')
        return
    row['development'] = growth
    row['explanation'].append(f"初始 {growth['initial']} 词条 · 剩余 {growth['remaining']} 次强化机会")
    if cfg.get('protectNaturalCrit', True) and growth['initial'] == 4 and {'crit_', 'crit_dmg_'} <= row['rolls'].keys():
        row['reasons'].append('四词条双暴胚子')


def quota(value):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError('备用数量应为非负整数或 null（不限）')
    return value


def rule_selectors(rule):
    return {k: str(rule[k]) for k in SELECTORS if rule.get(k) not in (None, '', '*')}


def matching_rules(group, cfg):
    limits = {'通用': (-1, quota(cfg['reserve']), DEFAULT_STATS)}
    trim = (-1, cfg.get('trimRecommended', False))
    for rule in cfg['reserve_rules']:
        selectors = rule_selectors(rule)
        if any(group.get(k) != v for k, v in selectors.items()):
            continue
        rank = len(selectors)
        if 'trimRecommended' in rule and rank >= trim[0]:
            trim = rank, rule['trimRecommended']
        purpose = rule.get('purpose') or '通用'
        if purpose not in limits or rank >= limits[purpose][0]:
            limits[purpose] = rank, quota(rule['count']), rule.get('stats', DEFAULT_STATS)
    return limits, trim[1]


def normalized_rule(rule, reverse):
    if 'set' not in rule:
        return rule
    return dict(rule, set=reverse.get(rule['set'], rule['set']))


def exact_purpose(rule, group, purpose, reverse):
    return ((rule.get('purpose') or '通用') == purpose and
            rule_selectors(normalized_rule(rule, reverse)) == rule_selectors(group))


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
        fixed = set()
        for row in group:
            row['keptBeforeReserves'] = bool(row['reasons'])
            if not trim:
                row['reasons'].extend(row['recommendations'])
            keep_lock = row.get('managedSpareLock') and not cfg.get('rotateSpareLocks')
            if not row['reasons'] and not row['review']:
                eligible.append(row)
                if keep_lock:
                    fixed.add(row['fp'])
            elif keep_lock and not row['reasons']:
                row['reasons'].append('插件备用锁')
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
                # Keep an equally good spare already locked by the manager.
                # Use the same tie break for translated and original set names.
                stable_key = (row.get('pinKeys') or [row['fp']])[0]
                base_rank = (rolls, growth, hits, bool(row.get('managedSpareLock')), stable_key)
                fixed_first = row['fp'] in fixed
                if purpose == '通用' and stats is DEFAULT_STATS:
                    best_role = max(((score, row['development']['upgrades'] * row['roleHits'][agent])
                                     for agent, score in row.get('roleRolls', {}).items()), default=(rolls, growth))
                    return (fixed_first, bool(row['recommendations']), best_role, *base_rank)
                return (fixed_first, *base_rank)
            matching = (row for row in eligible if purpose == '通用' or any(stat in row['rolls'] for stat in stats))
            picks = sorted(matching, key=rank, reverse=True)[:count]
            for row in picks:
                row['reasons'].append('备用 · ' + purpose)
                selected.add(row['fp'])
            by_purpose.append(dict(purpose=purpose, count=count, covered=len(picks)))
        fixed_extra = fixed - selected
        for row in eligible:
            if row['fp'] in fixed_extra:
                row['reasons'].append('插件备用锁')
            elif row['fp'] not in selected:
                row['explanation'].append('超出同类备用名额')
        report.append(dict(**criteria, trimRecommended=trim, eligible=len(eligible),
                           kept=len(selected) + len(fixed_extra), fixedExtra=len(fixed_extra), purposes=by_purpose))
    return report


def quota_impact(report, scope, cfg, reverse):
    """Replay only spare selection for the matching groups in this saved preview."""
    scope = rule_selectors(scope)
    rows = [row for row in report['discs'] if not row['marked'] and all(row[k] == v for k,v in scope.items())]
    if any('keptBeforeReserves' not in row for row in rows):
        return None
    prepared = []
    for row in rows:
        independent = row['keptBeforeReserves']
        if independent and row.get('managedSpareLock') and '插件备用锁' in row['reasons']:
            # Older previews counted the manager's own lock as an independent reason.
            independent = any(r != '插件备用锁' and r not in row['recommendations'] for r in row['reasons'])
        prepared.append(dict(row, reasons=['独立保留'] if independent else [], explanation=[]))
    normalized = [normalized_rule(rule,reverse) for rule in cfg['reserve_rules']]
    coverage = apply_reserves(prepared, dict(cfg,reserve_rules=normalized))
    before = sum(row['decision'] == '保留' for row in rows)
    after = sum(bool(row['reasons']) for row in prepared)
    new_candidates = sum(old['decision'] != '弃置候选' and not new['reasons'] and not new['review']
                         for old,new in zip(rows, prepared, strict=True))
    return dict(total=len(rows), before=before, after=after, newCandidates=new_candidates,
                spares=sum(group['kept'] for group in coverage), groups=len(coverage))
