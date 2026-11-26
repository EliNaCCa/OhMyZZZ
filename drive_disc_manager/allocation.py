"""Assign complete builds to simultaneous profiles in player priority order."""
from .optimizer import NoBuildCandidates, effective_crit_rate, number, select_candidate


def reusable_assignments(report, allocation_key, groups, pool):
    """Reuse a successful UI preview only if all six slots and group claims still match."""
    if not report or not allocation_key or report.get('allocationKey') != allocation_key or report.get('issues'):
        return None
    saved = report.get('assignments')
    names = {profile['name'] for members in groups.values() for profile, _ in members}
    if not isinstance(saved, dict) or saved.keys() != names:
        return None
    slots = {disc['id']: disc['slot'] for disc in pool}
    for members in groups.values():
        claimed = set()
        for profile, _ in members:
            result = saved[profile['name']]
            ids = result.get('ids') if isinstance(result, dict) and not result.get('error') else None
            if not isinstance(ids, list) or not all(isinstance(fp, str) for fp in ids):
                return None
            if [slots.get(fp) for fp in ids] != list(range(1, 7)) or not claimed.isdisjoint(ids):
                return None
            claimed.update(ids)
    return saved


def group_conflict(profiles):
    owners = {}
    for profile in profiles:
        if not profile.get('enabled', True) or not profile.get('simultaneous'):
            continue
        for source, actor in [('key', profile.get('agentKey')), ('name', (profile.get('request') or {}).get('characterName'))]:
            if not actor:
                continue
            key = (profile['simultaneous'], source, actor.strip().casefold())
            if key in owners:
                return f"{owners[key]} 和 {profile['name']} 是同一角色的方案，请分到不同出场组或设为互斥方案"
            owners[key] = profile['name']
    return ''


def group_indices(profiles, index):
    group = profiles[index].get('simultaneous')
    return [i for i, profile in enumerate(profiles) if group and profile.get('simultaneous') == group]


def arrange_profile(profiles, index, group, rank):
    """Move only same-group members between their existing rows; keep other groups stable."""
    updated = profiles.copy()
    updated[index] = dict(updated[index], simultaneous=group.strip())
    indices = group_indices(updated, index)
    if indices:
        if not 1 <= rank <= len(indices):
            raise ValueError('组内优先级超出范围')
        ordered = [updated[i] for i in indices if i != index]
        ordered.insert(rank - 1, updated[index])
        for i, profile in zip(indices, ordered):
            updated[i] = profile
    conflict = group_conflict(updated[i] for i in indices)
    if conflict:
        raise ValueError(conflict)
    return updated


def same_priority(left, right, current):
    if len(current.intersection(left['ids'])) != len(current.intersection(right['ids'])):
        return False
    if 'outputScore' in left:
        def priority(row):
            return number(row.get('rawOutput', row['outputScore'])), effective_crit_rate(row) == 100
        return priority(left) == priority(right)
    if 'roleOrder' in left:
        from .role_panel import compare_order
        return compare_order(left['roleOrder'], right['roleOrder']) == 0
    return left['ids'] == right['ids']


def assign_group(members, pool, url, fetch, max_nodes=160):
    # Search the available inventory at each step. Multiple branches may reach
    # the same set of claimed discs and reuse the cached optimizer response.
    cache = {}
    if not members:
        return []
    nodes = queries = 0
    restricted_searches = set()
    extra_queries = 0
    highest_seen = {}
    last_problem = ''
    limited = False
    def search(index, prefixes):
        nonlocal nodes, limited
        profile, current = members[index]
        def candidates(excluded):
            nonlocal queries, extra_queries, last_problem, limited
            key = index, excluded
            if key not in cache:
                if excluded:
                    # Keep one restricted lookup for each later role, even when
                    # many tied prefixes compete for the extra search budget.
                    if index in restricted_searches:
                        if extra_queries >= 16:
                            limited = True
                            return []
                        extra_queries += 1
                    restricted_searches.add(index)
                queries += 1
                available = [disc for disc in pool if disc['id'] not in excluded]
                try:
                    cache[key] = fetch(url, profile, available, current)
                    if cache[key] and 'outputScore' in cache[key][0]:
                        best = max(number(c.get('rawOutput', c['outputScore'])) for c in cache[key])
                        highest_seen[index] = max(highest_seen.get(index, best), best)
                except NoBuildCandidates as exc:
                    last_problem = f"{profile['name']} · {exc}"
                    cache[key] = []
            return cache[key]
        original = candidates(frozenset())
        options, paths = {}, {}
        # Prefixes are tied on every higher-priority role's preferences. Compare
        # the next role's options across those prefixes before choosing disc IDs.
        for claimed, prefix in prefixes:
            if nodes >= max_nodes - (len(members) - index - 1):
                limited = True
                break
            nodes += 1
            restricted = candidates(claimed) if claimed else []
            feasible = {tuple(c['ids']):c for c in (*original, *restricted) if claimed.isdisjoint(c['ids'])}
            outputs = (number(c.get('rawOutput', c['outputScore'])) for c in feasible.values() if 'outputScore' in c)
            best = max(outputs, default=None)
            for ids, candidate in feasible.items():
                options.setdefault(ids, candidate)
                paths.setdefault(ids, []).append((claimed, prefix, len(feasible), best))
        backtracked = False
        branch_limit = max(1, max_nodes - nodes - (len(members) - index - 2))
        while options:
            preferred = select_candidate(list(options.values()), profile, current)
            first = tuple(preferred['ids'])
            ties = [first, *(ids for ids, row in options.items() if ids != first and same_priority(row, preferred, current))]
            following = {}
            truncated = False
            for ids in ties:
                candidate = options.pop(ids)
                for claimed, prefix, available, best in paths.pop(ids):
                    # The remaining roles only depend on which physical discs are claimed.
                    after = claimed.union(ids)
                    if after in following:
                        continue
                    if len(following) >= branch_limit:
                        limited = truncated = True
                        break
                    choice = dict(candidate, availableBuilds=available)
                    if 'outputScore' in preferred:
                        for key in ('reason','bestOutput','rawBestOutput','relativeLossPct','bestNearFullOutput','bestFullOutput'):
                            choice[key] = preferred[key]
                        choice['effectiveCritRate'] = float(effective_crit_rate(candidate))
                        choice['rawAvailableBestOutput'] = str(best)
                        score = number(candidate.get('rawOutput', candidate['outputScore']))
                        choice['availableLossPct'] = float(100*(best-score)/best)
                    if ids != first:
                        choice['reason'] = '优先方案同分，按后续角色需求分盘 · '+choice.get('reason','')
                    if backtracked:
                        choice['reason'] = '为同时出场的角色凑齐六盘 · '+choice.get('reason','')
                    lineup = prefix + [(profile, choice)]
                    if index + 1 == len(members):
                        return lineup
                    following[after] = (after, lineup)
                if truncated:
                    break
            result = search(index+1, following.values())
            if result is not None:
                return result
            if truncated:
                break
            if limited and nodes >= max_nodes - (len(members) - index - 1):
                break
            backtracked = True
        return None

    result = search(0, [(frozenset(), [])])
    if result is None:
        reason = '达到本轮联合分配搜索上限，配队待复核' if limited else last_problem or '在搜索范围内没有找到全员六盘不重复的组合'
        raise ValueError(reason)
    for index, (profile, choice) in enumerate(result):
        original = cache.get((index, frozenset()), [])
        if original:
            baseline = select_candidate(original,profile,members[index][1])
            if baseline.get('wornOutput') is not None:
                choice['wornOutput'] = baseline['wornOutput']
            if baseline.get('currentPanel') and 'panel' in choice:
                choice['currentPanel'] = baseline['currentPanel']
            if choice['ids'] != baseline['ids']:
                claimed_by = [(p['name'], slot) for p, previous in result[:index]
                              for slot, fp in enumerate(baseline['ids'], 1) if fp in previous['ids']]
                if claimed_by:
                    detail = '、'.join(f'{name} 占用 {slot} 号位' for name, slot in claimed_by)
                    choice['reason'] = '同时出场组协调让盘 · '+detail+' · '+choice.get('reason','')
                elif index < len(result)-1:
                    choice['reason'] = '同时出场组协调让盘 · '+choice.get('reason','')
        if index in highest_seen:
            best = highest_seen[index]
            choice['rawBestOutput'] = str(best)
            choice['bestOutput'] = float(best)
            score = number(choice.get('rawOutput', choice['outputScore']))
            choice['relativeLossPct'] = float(100*(best-score)/best)
        choice['search'] = dict(candidateBuilds=len(original), availableBuilds=choice.pop('availableBuilds'),
                                calls=queries, visited=nodes, limited=limited)
        if original and original[0].get('optimizerScope'):
            choice['search']['initialScope'] = original[0]['optimizerScope']
        if 'panel' in choice:
            from .role_panel import goal_rows, MODE_NAMES
            request = profile['request']
            mode = (request.get('mode') or '').strip().upper()
            choice['panelMode'] = MODE_NAMES.get(mode, mode or '配装器默认排序')
            choice['panelRows'] = goal_rows(request,choice['panel'],choice['currentPanel'])
    return result
