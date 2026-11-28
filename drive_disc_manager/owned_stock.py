"""Rank completed discs, then compare a growing disc's remaining rolls to those picks."""
from collections import defaultdict

from .reserve_policy import DEFAULT_STATS, SELECTORS, matching_rules, max_useful_rolls, usable_stats


EMBRYO = '备用 · 未强化胚子'
TRIAL = '试强化'
BLANKET = {'满级盘（+15）', '四词条双暴胚子'}


def describe_stock(row, owned, all_sets):
    from .roster import agent_id, identifier
    agents = row.setdefault('ownedUses', {})
    by_key = {a['key']:a for a in owned}
    for hint in row['recommendations']:
        if hint.startswith(('角色套装 · ', '角色攻略 · ')):
            key = agent_id(hint.split(' · ', 1)[1])
            if key in by_key:
                agents.setdefault(key, dict(label=by_key[key]['label'], stats=[]))
    if not owned:
        row['review'].append('尚未读到已选代理人')
    if any(not role['stats'] for role in agents.values()):
        row['review'].append('适用代理人的副词条规则待补齐')
    if agents or row['level'] != 0:
        return
    fixed = row['slot'] in ('1', '2', '3')
    choices = [preferred for _, _, slots, preferred, _, _ in all_sets.get(identifier(row['set']), ())
               if fixed or any(row['main'] in stats for _, stats, _ in slots.get(row['slot'], ()))]
    if row['recommendations']:
        choices.extend((DEFAULT_STATS, ('anomProf', 'atk_', 'pen')))
    row['embryoScore'] = max((score(row, keys) for keys in choices), default=0)


def stock_count(base, users):
    if not users:
        return 0
    return None if base is None else (base * (users + 1) + 1) // 2


def score(row, stats):
    return sum(row['rolls'].get(stat, 0) for stat in stats)


def stable_key(row):
    return row['level'], tuple(sorted(row['rolls'].items())), row['id']


def floors_and_picks(ready, uses, limit):
    """Give each agent a first pick when space permits, then fill shared slots by score."""
    picked, floors = set(), {label:None for label in uses}
    def pick(labels):
        candidates = [(max(score(row, uses[label]) for label in labels),
                       bool(row['reasons'] or row.get('managedSpareLock')), stable_key(row))
                      for row in ready if row['id'] not in picked]
        if not candidates:
            return 0
        points, _, (_, _, ident) = max(candidates)
        if points:
            picked.add(ident)
        return points

    if limit == 0 or not uses:
        return picked, []
    if limit is None:
        picked = {row['id'] for row in ready if any(score(row, keys) for keys in uses.values())}
        floors = dict.fromkeys(uses, 0)
    else:
        if len(uses) <= limit:
            for label in sorted(uses):
                floors[label] = pick([label])
        while len(picked) < limit:
            points = pick(uses)
            floors = {label:min(points, old) if old is not None else points for label, old in floors.items()}
            if not points:
                break
    return picked, [dict(label=label, stats=list(keys), minimum=(floors[label] or 0) + 1)
                    for label, keys in uses.items()]


def trial_result(row, floor):
    """The minimum is one better than an incumbent, or one useful roll for an empty slot."""
    level = row['level']
    chances = [(max_useful_rolls(level, row['rolls'], use['stats']), use) for use in floor]
    winning = [(maximum, use) for maximum, use in chances if maximum >= use['minimum']]
    if not winning:
        if not chances:
            return None, '这类盘没有备用名额'
        maximum, use = max(chances, key=lambda item:item[0] - item[1]['minimum'])
        return None, f"{use['label']}最多 {maximum} 档有效词条，入选需要 {use['minimum']} 档"
    if level == 15:
        return None, ''
    maximum, use = max(winning, key=lambda item:(score(row, item[1]['stats']), item[0]-item[1]['minimum']))
    target = (level // 3 + 1) * 3
    return dict(target=target, purpose=use['label'], stats=use['stats'], rolls=score(row, use['stats']),
                reason=f"{use['label']}最多 {maximum} 档，入选需要 {use['minimum']} 档，试到 +{target}"), ''


def apply_stock(rows, cfg, *, stats_only=False):
    groups = defaultdict(list)
    embryos, coverage = [], []
    finished = 0
    for row in rows:
        if row['marked']:
            continue
        row['reasons'] = [r for r in row.get('baseReasons', row['reasons']) if r not in BLANKET]
        row['baseReasons'] = list(row['reasons'])
        for key in ('upgradeFloor', 'upgrade', 'outgrown', 'checkForDiscard'):
            row.pop(key, None)
        row.setdefault('baseExplanation', list(row['explanation']))
        row['explanation'] = list(row['baseExplanation'])
        row['keptBeforeReserves'] = bool(row['reasons'])
        row['decision'] = '保留' if row['reasons'] else '待复核' if row['review'] else '弃置候选'
        if row.get('managedSpareLock') and not cfg.get('rotateSpareLocks'):
            row['reasons'].append('插件备用锁')
            row['decision'] = '保留'
        groups[tuple(row[k] for k in SELECTORS)].append(row)
    for key, group in groups.items():
        criteria = dict(zip(SELECTORS, key))
        agents = group[0]['ownedUses']
        usable = [row for row in group if not row['review'] and row.get('development')]
        ready = [row for row in usable if row['level'] == 15]
        picks, purposes, floors = defaultdict(list), [], []
        for purpose, (_, base, stats) in matching_rules(criteria, cfg)[0].items():
            count = stock_count(base, len(agents))
            if purpose == '通用' and stats is DEFAULT_STATS:
                uses = {role['label']:usable_stats(group[0], role['stats']) for role in agents.values()}
            else:
                uses = {purpose:usable_stats(group[0], stats)} if agents else {}
            chosen, thresholds = floors_and_picks(ready, uses, count)
            floors.extend(thresholds)
            for ident in chosen:
                picks[ident].append('备用 · '+purpose)
            purposes.append(dict(purpose=purpose, count=count, covered=len(chosen)))
        for row in usable:
            if row['id'] in picks:
                row['reasons'].extend(picks[row['id']]); row['decision'] = '保留'
            if row['level'] == 15:
                if agents and row['decision'] == '保留' and (row.get('location') or row['id'] in picks or
                        any(score(row, role['stats']) for role in agents.values())):
                    finished += 1
                elif not row['reasons']:
                    row['explanation'].append('超出同类成品名额' if agents else '当前代理人用不上')
                continue
            if row['baseReasons']:
                continue
            if agents:
                advice, reason = trial_result(row, floors)
                row['upgradeFloor'] = floors
                if advice:
                    row.update(decision='保留', upgrade=advice)
                    row['reasons'].append(TRIAL)
                    row['explanation'].append(advice['reason'])
                elif not row['reasons']:
                    row['outgrown'] = True
                    row['explanation'].append(reason)
            elif row['level'] == 0 and row.get('embryoScore', 0) >= 2:
                embryos.append(row)
            elif not row['reasons']:
                row['explanation'].append('当前代理人用不上')
        coverage.append(dict(**criteria, agents=sorted(a['label'] for a in agents.values()), trimRecommended=True,
                             eligible=len(ready), kept=len(picks), fixedExtra=0, purposes=purposes,
                             shared=sum(len(v) > 1 for v in picks.values())))
    limit = finished // 3
    for index, row in enumerate(sorted(embryos, key=lambda r:(r['embryoScore'], len(r['rolls']),
                                        bool(r.get('managedSpareLock')), stable_key(r)), reverse=True)):
        if index < limit:
            row['decision'] = '保留'; row['reasons'].append(EMBRYO)
        row['explanation'].append(f'+0 胚子名额 {limit} 张（可用满级盘 {finished} 张）')
    if stats_only:
        for group in groups.values():
            for row in group:
                if row['decision'] == '弃置候选':
                    row.update(checkForDiscard=True, decision='待复核')
    return coverage
