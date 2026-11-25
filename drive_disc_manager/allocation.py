"""Assign complete builds to simultaneous profiles in player priority order."""
from .optimizer import NoBuildCandidates, number, select_candidate


def ranked_options(pool, profile, current):
    remaining = list(pool)
    while remaining:
        chosen = select_candidate(remaining, profile, current)
        yield chosen
        remaining = [candidate for candidate in remaining if candidate['ids'] != chosen['ids']]


def assign_group(members, pool, url, fetch, max_nodes=160):
    # Re-query with claimed discs excluded when the full-inventory shortlist
    # cannot finish a group. Cached requests are bounded separately from nodes.
    cache = {}
    nodes = queries = 0
    last_problem = ''
    def search(index, claimed):
        nonlocal nodes, queries, last_problem
        if index == len(members):
            return []
        if nodes >= max_nodes:
            return None
        nodes += 1
        profile, current = members[index]
        def candidates(excluded):
            nonlocal queries, last_problem
            key = index, excluded
            if key not in cache:
                if excluded and queries >= len(members) + 16:
                    return []
                queries += 1
                available = [disc for disc in pool if disc['id'] not in excluded]
                try:
                    cache[key] = fetch(url, profile, available, current)
                except NoBuildCandidates as exc:
                    last_problem = f"{profile['name']} · {exc}"
                    cache[key] = []
            return cache[key]
        seen = set()
        # The unrestricted shortlist exposes tradeoffs for earlier team members.
        # The restricted request uncovers combinations outside that shortlist.
        for excluded in dict.fromkeys([frozenset(), claimed]):
            choices = [c for c in candidates(excluded) if tuple(c['ids']) not in seen and claimed.isdisjoint(c['ids'])]
            for choice in ranked_options(choices, profile, current):
                if nodes >= max_nodes:
                    break
                seen.add(tuple(choice['ids']))
                suffix = search(index+1, claimed.union(choice['ids']))
                if suffix is not None:
                    return [(profile, choice)]+suffix
        return None

    result = search(0, frozenset())
    if result is None:
        reason = last_problem or '在搜索范围内没有找到全员六盘不重复的组合'
        raise ValueError(reason)
    for index, (profile, choice) in enumerate(result):
        original = cache.get((index, frozenset()), [])
        if original:
            baseline = select_candidate(original,profile,members[index][1])
            if choice['ids'] != baseline['ids']:
                choice['reason'] = '同时出场组协调让盘 · '+choice.get('reason','')
            if 'bestOutput' in baseline:
                best = max(number(baseline['bestOutput']), number(choice['bestOutput']))
                choice['bestOutput'] = float(best)
                choice['relativeLossPct'] = float(100*(1-number(choice['outputScore'])/best))
        choice['search'] = dict(candidateBuilds=len(original), calls=queries, visited=nodes)
    return result
