"""Assign complete builds to simultaneous profiles in player priority order."""
from .optimizer import NoBuildCandidates, number, select_candidate


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


def ranked_options(pool, profile, current):
    remaining = list(pool)
    while remaining:
        chosen = select_candidate(remaining, profile, current)
        yield chosen
        remaining = [candidate for candidate in remaining if candidate['ids'] != chosen['ids']]


def assign_group(members, pool, url, fetch, max_nodes=160):
    # Search the available inventory at each step. Multiple branches may reach
    # the same set of claimed discs and reuse the cached optimizer response.
    cache = {}
    nodes = queries = 0
    last_problem = ''
    limited = False
    def search(index, claimed):
        nonlocal nodes, queries, last_problem, limited
        if index == len(members):
            return []
        if nodes >= max_nodes:
            limited = True
            return None
        nodes += 1
        profile, current = members[index]
        def candidates(excluded):
            nonlocal queries, last_problem, limited
            key = index, excluded
            if key not in cache:
                if excluded and queries >= len(members) + 16:
                    limited = True
                    return []
                queries += 1
                available = [disc for disc in pool if disc['id'] not in excluded]
                try:
                    cache[key] = fetch(url, profile, available, current)
                except NoBuildCandidates as exc:
                    last_problem = f"{profile['name']} · {exc}"
                    cache[key] = []
            return cache[key]
        original = candidates(frozenset())
        restricted = candidates(claimed) if claimed else []
        # For utility agents prefer the optimizer's order for the actual remaining
        # inventory. Direct builds compare all known feasible options by raw output.
        choices = {}
        for candidate in (*restricted, *original):
            if claimed.isdisjoint(candidate['ids']):
                choices.setdefault(tuple(candidate['ids']), candidate)
        for choice in ranked_options(choices.values(), profile, current):
            suffix = search(index+1, claimed.union(choice['ids']))
            if suffix is not None:
                choice['availableBuilds'] = len(choices)
                return [(profile, choice)]+suffix
            if nodes >= max_nodes:
                limited = True
                break
        return None

    result = search(0, frozenset())
    if result is None:
        reason = '达到本轮联合分配搜索上限，配队待复核' if limited else last_problem or '在搜索范围内没有找到全员六盘不重复的组合'
        raise ValueError(reason)
    for index, (profile, choice) in enumerate(result):
        original = cache.get((index, frozenset()), [])
        if original:
            baseline = select_candidate(original,profile,members[index][1])
            if choice['ids'] != baseline['ids']:
                claimed_by = [(p['name'], slot) for p, previous in result[:index]
                              for slot, fp in enumerate(baseline['ids'], 1) if fp in previous['ids']]
                if claimed_by:
                    detail = '、'.join(f'{name} 占用 {slot} 号位' for name, slot in claimed_by)
                    choice['reason'] = '同时出场组协调让盘 · '+detail+' · '+choice.get('reason','')
                elif index < len(result)-1:
                    choice['reason'] = '同时出场组协调让盘 · '+choice.get('reason','')
            if 'bestOutput' in baseline:
                best = max(number(baseline['rawBestOutput']), number(choice['rawBestOutput']))
                choice['rawBestOutput'] = str(best)
                choice['bestOutput'] = float(best)
                score = number(choice.get('rawOutput', choice['outputScore']))
                choice['relativeLossPct'] = float(100*(best-score)/best)
        choice['search'] = dict(candidateBuilds=len(original), availableBuilds=choice.pop('availableBuilds'),
                                calls=queries, visited=nodes, limited=limited)
    return result
