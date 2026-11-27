"""Rank spare discs after the planner has identified gear and rule coverage."""
from collections import defaultdict


DEFAULT_STATS = ('crit_', 'crit_dmg_', 'atk_')
PURPOSE_STATS = {'偏暴击': ('crit_',), '偏暴伤': ('crit_dmg_',)}
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


def spare_uses(row, limits):
    """Use character preferences only for the inherited general-purpose stat list."""
    for purpose, (_, count, stats) in limits.items():
        if count == 0:
            continue
        if purpose == '通用' and stats is DEFAULT_STATS and row.get('roleStats'):
            for role, preferred in row['roleStats'].items():
                yield role, count, preferred, False
        else:
            # apply_reserves only assigns named uses to discs with a matching substat.
            yield purpose, count, stats, purpose != '通用'


def max_useful_rolls(level, rolls, stats):
    keys = set(stats)
    good = sum(rolls.get(k, 0) for k in keys)
    remaining = 5 - level // 3
    if len(rolls) == 3:
        # The fourth line must be a different stat. Only subsequent tiers
        # can improve an existing line.
        if keys.difference(rolls):
            good += 1
        remaining -= 1
    return good + remaining if good else 0


def usable_stats(row, stats):
    from .optimizer import STATS
    return tuple(sorted(k for k in set(stats) if k != row['main'] and STATS.get(k, (None, None))[1]))


def upgrade_advice(row, limits, floor=()):
    """Rate one S disc's next three-level step from its own stats and matching uses."""
    growth = row.get('development')
    if (not growth or row['level'] >= 15 or row['marked'] or row['review']
            or not row['recommendations']):
        return None
    targets = []
    for purpose, _, stats, _ in spare_uses(row, limits):
        keys = usable_stats(row, stats)
        if any(use['label'] == purpose and tuple(use['stats']) == keys and
               max_useful_rolls(row['level'], row['rolls'], keys) < use['minimum'] for use in floor):
            continue
        targets.append((purpose, sum(row['rolls'].get(k, 0) for k in keys), keys))
    if not targets:
        return None
    use, good, keys = max(targets, key=lambda item: item[1])
    # Reveal a fourth line on a promising three-liner. On developed pieces require
    # more useful rolls before spending materials on the next tier.
    minimum = (1 if len(row['rolls']) == 3 else 2) + row['level'] // 6
    if good < minimum:
        return None
    target = 3 * (row['level'] // 3 + 1)
    return dict(target=target, purpose=use, rolls=good, stats=list(keys),
                reason=f'{use}有效副词条 {good} 档，建议试到 +{target}')


def rule_selectors(rule):
    return {k: str(rule[k]) for k in SELECTORS if rule.get(k) not in (None, '', '*')}


def refresh_upgrade(row, cfg, floor=()):
    old = row.pop('upgrade', None)
    if old and old['reason'] in row['explanation']:
        row['explanation'].remove(old['reason'])
    if row['decision'] == '保留' and not row['ambiguous']:
        limits, _ = matching_rules(row, cfg)
        advice = upgrade_advice(row, limits, floor)
        if advice:
            row['upgrade'] = advice
            row['explanation'].append(advice['reason'])


def rule_stats(rule):
    return rule.get('stats', PURPOSE_STATS.get(rule.get('purpose'), DEFAULT_STATS))


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
            limits[purpose] = rank, quota(rule['count']), rule_stats(rule)
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
                    fixed.add(row.get('buildId', row['fp']))
            elif keep_lock and not row['reasons']:
                row['reasons'].append('插件备用锁')
        by_purpose = []
        selected = defaultdict(int)
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
                fixed_first = row.get('buildId', row['fp']) in fixed
                if purpose == '通用' and stats is DEFAULT_STATS:
                    best_role = max(((score, row['development']['upgrades'] * row['roleHits'][agent])
                                     for agent, score in row.get('roleRolls', {}).items()), default=(rolls, growth))
                    return (fixed_first, bool(row['recommendations']), best_role, *base_rank)
                return (fixed_first, *base_rank)
            matching = (row for row in eligible if purpose == '通用' or any(stat in row['rolls'] for stat in stats))
            picks = sorted(matching, key=rank, reverse=True)[:count]
            for row in picks:
                row['reasons'].append('备用 · ' + purpose)
                selected[row.get('buildId', row['fp'])] += 1
            by_purpose.append(dict(purpose=purpose, count=count, covered=len(picks)))
        fixed_extra = fixed.difference(selected)
        for row in eligible:
            if row.get('buildId', row['fp']) in fixed_extra:
                row['reasons'].append('插件备用锁')
            elif row.get('buildId', row['fp']) not in selected:
                row['explanation'].append('超出同类备用名额')
        report.append(dict(**criteria, trimRecommended=trim, eligible=len(eligible),
                           kept=len(selected) + len(fixed_extra), fixedExtra=len(fixed_extra), purposes=by_purpose,
                           shared=sum(uses > 1 for uses in selected.values())))
    return report


def kept_coverage(coverage, rows):
    """Count the spare slots still filled after game checks and potential decisions."""
    groups = {}
    for previous in coverage:
        key = tuple(previous[k] for k in SELECTORS)
        purposes = [dict(use, covered=0) for use in previous['purposes']]
        groups[key] = dict(previous, kept=0, fixedExtra=0, shared=0, purposes=purposes)
    for row in rows:
        if row['decision'] != '保留' or row['review']:
            continue
        purposes = {reason.removeprefix('备用 · ') for reason in row['reasons'] if reason.startswith('备用 · ')}
        fixed = not purposes and '插件备用锁' in row['reasons']
        if not purposes and not fixed:
            continue
        group = groups[tuple(row[k] for k in SELECTORS)]
        group['kept'] += 1
        group['fixedExtra'] += int(fixed)
        group['shared'] += int(len(purposes) > 1)
        for use in group['purposes']:
            use['covered'] += int(use['purpose'] in purposes)
    return list(groups.values())


def plan_reserves(rows, cfg, reverse, *, stats_only):
    """Apply spare quotas and trial potential to rows with their independent keep reasons."""
    cfg = dict(cfg, reserve_rules=[normalized_rule(rule, reverse) for rule in cfg['reserve_rules']])
    coverage = apply_reserves(rows, cfg)
    limits_by_group = {}
    for row in rows:
        if row['marked']:
            continue
        row['decision'] = '保留' if row['reasons'] else ('待复核' if row['review'] else '弃置候选')
        if row['decision'] == '保留' and row['level'] < 15:
            key = (row['set'], row['slot'], row['main'])
            if key not in limits_by_group:
                limits_by_group[key], _ = matching_rules(row, cfg)
            advice = upgrade_advice(row, limits_by_group[key])
            if advice:
                row['upgrade'] = advice
                row['explanation'].append(advice['reason'])
        if stats_only and row['decision'] == '弃置候选':
            row['checkForDiscard'] = True
            row['decision'] = '待复核'
            row['explanation'].append('超出留盘名额，穿戴和标记待游戏内核对')
        if row.get('managedSpareLock') and row['decision'] == '弃置候选':
            row['explanation'].append('新保留盘锁定后，解除本插件的旧备用锁')
    from .potential import evaluate
    evaluate(rows, cfg)
    if stats_only:
        for row in rows:
            if row.get('outgrown'):
                row.update(checkForDiscard=True, decision='待复核')
    return kept_coverage(coverage, rows) if cfg.get('autoUpgrade') else coverage


def quota_impact(report, scope, cfg, reverse):
    """Replay the same quota and potential decisions over the matching groups."""
    scope = rule_selectors(scope)
    rows = [row for row in report['discs'] if not row['marked'] and all(row[k] == v for k,v in scope.items())]
    if any('keptBeforeReserves' not in row for row in rows):
        return None
    prepared = []
    for row in rows:
        reasons = []
        if row['keptBeforeReserves']:
            generated = {'插件备用锁', '试强化', '+3 胚子留作备用', *row['recommendations']}
            reasons = [reason for reason in row['reasons'] if reason not in generated and not reason.startswith('备用 · ')]
        updated = dict(row, reasons=reasons, explanation=[])
        for previous in ('upgrade', 'upgradeFloor', 'outgrown', 'checkForDiscard'):
            updated.pop(previous, None)
        prepared.append(updated)
    coverage = plan_reserves(prepared, cfg, reverse, stats_only=any('buildId' in row for row in rows))
    before = sum(row['decision'] == '保留' for row in rows)
    after = sum(row['decision'] == '保留' for row in prepared)
    potential = sum(row['reasons'] in (['试强化'], ['+3 胚子留作备用']) for row in prepared)
    new_candidates = new_game_checks = 0
    for old, new in zip(rows, prepared, strict=True):
        if new.get('checkForDiscard') and not old.get('checkForDiscard'):
            new_game_checks += 1
        elif new['decision'] == '弃置候选' and old['decision'] != '弃置候选':
            new_candidates += 1
    by_purpose = defaultdict(int)
    for group in coverage:
        for use in group['purposes']:
            if use['count'] != 0:
                by_purpose[use['purpose']] += use['covered']
    return dict(total=len(rows), before=before, after=after, potential=potential, newCandidates=new_candidates, newGameChecks=new_game_checks,
                spares=sum(group['kept'] for group in coverage), groups=len(coverage),
                byPurpose=dict(by_purpose), shared=sum(group['shared'] for group in coverage))
