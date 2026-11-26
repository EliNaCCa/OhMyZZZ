"""Values and sources used at planning time, shared across the inventory report."""
from .subscriptions import effective, field_tabs


def rule_book(rows, rules, overrides, conflicts):
    keys = {key for row in rows for key, _, _ in row['ruleUses']}
    book = {}
    for key in sorted(keys):
        remote = rules.get('fields', {}).get(key)
        local = overrides.get(key) or {}
        conflict = conflicts.get(key)
        book[key] = dict(remote=remote, mode=local.get('mode', 'inherit'), local=local.get('values'),
                         active=effective(remote, local), old=conflict['old'] if conflict else None,
                         conflict=bool(conflict), tabs=field_tabs(rules, key),
                         sourceCells=rules.get('sourceCells', {}).get(key, {}),
                         formerSource=key in rules.get('formerOrigins', {}))
    return book


def teams_for_disc(report, row):
    teams = report.get('traceTeams', {})
    return [(name, teams.get(name, {}), result) for name, result in report.get('assignments', {}).items()
            if not result.get('error') and row.get('buildId', row['fp']) in result.get('ids', ())]


def probe_trace(record, cfg, rules, wiki, known_owned):
    """Explain the last verified observation without fabricating an inventory or allocation."""
    from .optimizer import STATS
    from .planner import assess_rules
    from .roster import roster_catalog, compile_roster
    from .store import equipment_names, equipment_keys, equipment_key, fingerprint, is_pinned, pin_keys
    from .subscriptions import current_conflicts
    if record.get('status') != 'verified':
        raise ValueError('需要先读一张盘')
    disc = record['disc']
    names = equipment_names(wiki) | cfg.get('aliases', {})
    reverse = equipment_keys(names)
    values = disc['observedValues']
    reasons = []
    if disc['lockState'] == 'locked':
        reasons.append('读取时游戏内已锁定')
    if cfg['protect15'] and disc['level'] == 15:
        reasons.append('满级盘（+15）')
    rolls = {}
    if disc['rarity'] == 'S':
        for key, quantity in values['subs'].items():
            step = STATS.get(key, (None, None))[1]
            if step and 1 <= round(quantity/step) <= 6 and abs(round(quantity/step)*step-quantity) < 1e-6:
                rolls[key] = round(quantity/step)
    row = dict(fp=disc['fingerprint'], set=equipment_key(disc['setKey'], names, reverse), slot=str(disc['slotKey']),
               level=disc['level'], main=next(iter(values['main'])), observedValues=values, rolls=rolls,
               reasons=reasons, recommendations=[], review=[], explanation=[], ruleUses=[],
               decision='单盘观察', marked=disc['markState']=='marked')
    # Older probes omitted the raw rolls. Only recover their aliases on an exact hash match.
    original = disc if 'substats' in disc else dict(disc, mainStatKey=row['main'],
                    substats=[dict(key=key, upgrades=n) for key, n in rolls.items()])
    if fingerprint(original) == row['fp']:
        row['pinKeys'] = pin_keys(original, names, reverse, row['fp'])
    if is_pinned(row, cfg['pins']):
        reasons.append('手动保护')
    if 'ownerState' in disc:
        row.update(ownerState=disc['ownerState'], location=disc.get('location', ''))
    if row['marked']:
        row['reasons'] = ['读取时游戏已标记弃置']
    else:
        if disc.get('ownerState') == 'worn' and disc.get('location'):
            reasons.append('已装备 · '+disc['location'])
        if disc.get('ownerState') == 'unknown':
            row['review'].append('穿戴者头像待复核')
        if disc['rarity'] == 'S' and len(rolls) == len(values['subs']):
            from .reserve_policy import assess_growth
            assess_growth(row, cfg)
        fields = rules.get('fields', {})
        catalog = roster_catalog({'discs':[disc]}, fields, cfg, wiki, known_owned)
        assess_rules(row, cfg, fields, reverse, *compile_roster(catalog, fields, cfg['overrides'], reverse))
    return row, dict(rules=rules.get('version'), traceRules=rule_book([row], rules, cfg['overrides'],
                                                                     current_conflicts(rules,cfg['overrides'])))
