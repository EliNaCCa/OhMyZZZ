"""Use the optimizer's team inputs and candidate search; compare direct builds locally."""
import http.client
import json
from collections import Counter
from decimal import Decimal, InvalidOperation
from math import prod
from urllib.parse import urlsplit
from .store import fingerprint

# S-rank values from the optimizer's rollValue / ocrDefaultMainStatValue.
STATS = {'hp':('HP_FLAT',112,2200), 'atk':('ATK_FLAT',19,316), 'def':('DEF_FLAT',15,184),
         'atk_':('ATK_PERCENT',3,30), 'hp_':('HP_PERCENT',3,30), 'def_':('DEF_PERCENT',4.8,48),
         'crit_':('CRIT_RATE',2.4,24), 'crit_dmg_':('CRIT_DMG',4.8,48), 'anomProf':('ANOMALY_PROFICIENCY',9,92),
         'pen':('PEN_FLAT',9,None), 'pen_':('PEN_RATIO',None,24), 'impact':('IMPACT',None,18),
         'energyRegen_':('ENERGY_REGEN',None,60), 'anomMas_':('ANOMALY_MASTERY',None,30),
         **{e+'_dmg_':(e.upper()+'_DMG',None,30) for e in ('physical','electric','fire','ice','ether','wind','lumiflux')}}


def as_disc(d, names, roll_base=0):
    from .reserve_policy import development
    from .stat_reading import valid_slot_main
    if d['rarity'] != 'S' or d['setKey'] not in names or not valid_slot_main(d):
        return None
    main = STATS.get(d['mainStatKey'])
    if not main or main[2] is None:
        return None
    value = d.get('mainStatValue', main[2] if d['level']==15 else None)
    observed = None
    if 'observedValues' in d:
        from .stat_reading import checked_values
        observed = checked_values(d, roll_base)
        if not observed:
            return None
        value = observed['main'][d['mainStatKey']]
    if type(value) not in (int, float) or not 0 < value <= main[2] or (d['level'] == 15 and value != main[2]):
        return None
    rolls = {s['key']:1 + s['upgrades'] - roll_base for s in d['substats']}
    if development(d['level'], rolls) is None:
        return None
    subs = []
    for s in d['substats']:
        stat = STATS.get(s['key'])
        if not stat or stat[1] is None or s['key']==d['mainStatKey']:
            return None
        quantity = observed['subs'][s['key']] if observed else round(stat[1] * rolls[s['key']], 4)
        subs.append(dict(type=stat[0], value=quantity))
    return dict(id=fingerprint(d), setName=names[d['setKey']], slot=int(d['slotKey']), level=d['level'],
                rarity='S', mainStat=dict(type=main[0], value=value),subStats=subs,
                equippedBy=d.get('location',''),locked=bool(d.get('lock')),discarded=bool(d.get('trash')))


def loopback_json(url, route, payload=None):
    from .engine import Endpoint
    managed = isinstance(url, Endpoint)
    uri = urlsplit(url.url if managed else url)
    if uri.scheme != 'http' or uri.hostname not in ('127.0.0.1','localhost','::1') or not uri.port or uri.path not in ('','/') or uri.username or uri.password or uri.query or uri.fragment:
        raise ValueError('填写配装器窗口显示的本机 HTTP 地址和端口')
    # Direct loopback connection. Never proxy or follow redirects with private inventory.
    con = http.client.HTTPConnection(uri.hostname, uri.port, timeout=60)
    try:
        if managed:
            con.connect()
            url.check_connection(con)
        if payload is None:
            con.request('GET',route)
        else:
            con.request('POST',route,json.dumps(payload).encode(),{'Content-Type':'application/json'})
        response = con.getresponse()
        if response.status != 200:
            raise ValueError(f'本地配装器 HTTP {response.status}')
        raw = response.read(16_000_001)
        if len(raw) > 16_000_000:
            raise ValueError('配装器返回超出上限')
        return json.loads(raw)
    finally:
        con.close()


def call(url, payload):
    data = loopback_json(url, '/api/optimize', payload)
    if data.get('canceled') or not isinstance(data.get('results'),list):
        raise ValueError('配装器中断或结果格式不匹配')
    return data


def saved_targets(url):
    targets = loopback_json(url, '/api/character-targets')
    state = loopback_json(url, '/api/state')
    effects = state.get('setEffects')
    if not isinstance(effects, list):
        raise ValueError('配装器套装效果表不可用')
    choices = []
    for target in targets.get('plans') or []:
        request = target.get('request') or {}
        if not request.get('characterName') or not request.get('roleSystem'):
            continue
        if (target.get('ui') or {}).get('occupiedPolicy') == 'AVOID_OCCUPIED' and state.get('discClaims'):
            # Upstream ids belong to a different inventory. Importing them by row
            # number would make an occupied disc available to a second agent.
            occupied = True
        else:
            occupied = False
        request = {k:v for k,v in request.items() if k not in ('discs','setEffects')}
        choices.append(dict(agent=target.get('characterName') or request['characterName'],
                            request=dict(request,setEffects=effects), sourceId=target.get('id'),
                            requiresClaimMapping=occupied))
    return choices


def refreshed_profile(profile, choices, *, wiki=None):
    """Refresh one imported snapshot without rebinding other teams sharing its origin."""
    source_id = profile.get('sourceId')
    matches = [choice for choice in choices if source_id and choice.get('sourceId') == source_id]
    if len(matches) != 1:
        raise ValueError('未找到唯一的原配装器目标。可将当前配装器目标保存为新方案。')
    source = matches[0]
    original, updated = profile['request'], source['request']
    from .roster import same_agent
    if (not same_agent(wiki or {}, updated['characterName'], original['characterName'])
            or not same_agent(wiki or {}, source['agent'], updated['characterName'])):
        raise ValueError('此目标对应的角色已变化，请另存为新方案。')
    if source['requiresClaimMapping']:
        raise ValueError('原目标要求避让配装器占用盘，请先核对占用记录。')
    refreshed = dict(profile, request=updated)
    from .combat_windows import window_requests
    window_requests(refreshed)
    changed = [key for key in sorted(original.keys() | updated.keys())
               if key not in original or key not in updated or original[key] != updated[key]]
    return refreshed, changed


def number(v):
    try:
        n = Decimal(str(v))
    except InvalidOperation as exc:
        raise ValueError('缺少有效的配装数值') from exc
    if not n.is_finite():
        raise ValueError('配装数值异常')
    return n


NEAR_CRIT_GAP = Decimal('2.4')


def prefer(candidates, tolerance_pct, current):
    candidates = tuple(candidates)
    tol = number(tolerance_pct)/100
    if not 0 <= tol <= 1:
        raise ValueError('输出容差应在 0–100% 之间')
    def key(c):
        return number(c.get('rawOutput', c['outputScore'])), sum(i in current for i in c['ids']), tuple(c['ids'])
    best = max(candidates, key=key)
    if key(best)[0] <= 0:
        raise ValueError('缺少正的预期输出')
    # outputScore already includes the optimizer's capped crit expectation.
    # A rate within one roll of 100 qualifies; it still deals its real damage.
    near = [c for c in candidates if number(c.get('rawCritRate', c['critRate'])) >= 100 - NEAR_CRIT_GAP]
    cap = max(near,key=key) if near else None
    chosen = cap if cap and key(cap)[0] >= key(best)[0]*(1-tol) else best
    if cap is None:
        reason = '候选暴击率均低于 97.6%，选最高输出'
    elif chosen is cap and key(chosen)[0] == key(best)[0]:
        reason = '实战暴击达 97.6%，且输出最高'
    elif chosen is cap:
        reason = '实战暴击达 97.6%，输出差距在容差内'
    else:
        reason = '近满暴方案超出输出容差，选最高输出'
    full = [c for c in near if number(c.get('rawCritRate', c['critRate'])) >= 100]
    return dict(chosen, bestOutput=best['outputScore'], rawBestOutput=str(key(best)[0]), relativeLossPct=float(100*(key(best)[0]-key(chosen)[0])/key(best)[0]),
                reason=reason, effectiveCritRate=float(min(100,number(chosen['critRate']))),
                bestNearFullOutput=cap['outputScore'] if cap else None,
                bestFullOutput=max(full,key=key)['outputScore'] if full else None)


class NoBuildCandidates(ValueError):
    """This inventory subset cannot supply the requested six slots."""


def result_ids(result, sources):
    pieces = sorted(result['discs'], key=lambda disc:disc['slot'])
    ids = [disc['id'] for disc in pieces]
    if len(ids) != 6 or [sources[i]['slot'] if i in sources else 0 for i in ids] != list(range(1,7)):
        raise ValueError('配装器结果与本轮库存不一致')
    for disc in pieces:
        if any(disc.get(key) != sources[disc['id']][key] for key in ('slot','mainStat','setName','subStats')):
            raise ValueError('配装器修正过属性，请复核实物')
    return tuple(ids)


def current_panel(url, args, current, sources, request):
    from .role_panel import observed_panel, baseline_request
    if len(current) != 6 or not current <= sources.keys():
        return {}
    worn = {i: sources[i] for i in sorted(current)}
    if {d['slot'] for d in worn.values()} != set(range(1, 7)):
        return {}
    measured = request(url, baseline_request(args, list(worn.values())))
    if not measured['results']:
        return {}
    reading = measured['results'][0]
    result_ids(reading, worn)
    return observed_panel(args, reading)


def role_candidates(url, args, discs, current, request):
    from .role_panel import ROLE_FIELDS, observed_panel, goal_rows
    role = args['roleSystem'].strip().upper()
    sources = {d['id']:d for d in discs}
    response = request(url, args)
    builds = []
    worn_panel = {}
    for result in response['results']:
        ids = result_ids(result, sources)
        panel = observed_panel(args,result)
        if set(ids) == current:
            worn_panel = panel
        builds.append(dict(ids=list(ids), panel=panel, reason=result.get('reason') or '沿用配装器排名', score=result['score']))
    if not worn_panel:
        worn_panel = current_panel(url, args, current, sources, request)
    if not builds or role not in ROLE_FIELDS:
        reason = f'职业 {role} 的模型待核' if role not in ROLE_FIELDS else '没有符合配队条件的六盘组合'
        diagnostic = worn_panel
        subject = '当前装备'
        if not diagnostic and response.get('nearMisses'):
            reference = response['nearMisses'][0]
            result_ids(reference,sources)
            diagnostic = observed_panel(args,reference)
            subject = '接近目标的候选'
        if not diagnostic and builds:
            diagnostic = builds[0]['panel']
            subject = '候选面板'
        metrics = goal_rows(args,diagnostic,{})
        gaps = [f'{label} {value:g} / 目标 {goal:g}' for label, _, goal, _, value, _, _ in metrics if goal]
        if not gaps and role not in ROLE_FIELDS:
            gaps = [f'{label} {value:g}' for label, _, _, _, value, _, _ in metrics]
        if gaps:
            reason += ' · '+subject+' · '+'、'.join(gaps)
        raise NoBuildCandidates(reason)
    for build in builds:
        build['currentPanel'] = worn_panel
    return builds


def _window_pool(url, profile, discs, current, request, exact=False):
    if not discs:
        raise NoBuildCandidates('缺少适用的驱动盘')
    args = dict(profile['request'])
    if not args.get('roleSystem') or not args.get('characterName'):
        raise ValueError('配队需要配装器角色与职业配置')
    direct = args['roleSystem'].upper() in ('ATTACK','STRONG','RUPTURE')
    counts = Counter(d['slot'] for d in discs)
    combinations = prod(counts[slot] for slot in range(1, 7))
    # The pinned API accepts up to 200 results. Return every eligible combination
    # for small pools, even when the optimizer's own ranking differs from ours.
    limit = max(24, combinations) if direct and combinations <= 200 else 24
    args.update(discs=discs, excludeDiscarded=True, topN=limit, maxCombinations=profile.get('searchLimit',200000))
    if not direct:
        return role_candidates(url, args, discs, current, request)
    original_args = dict(args)
    # Upstream filters on PANEL rate, while the personal preference uses combat rate.
    estimated_panel = max(0, min(100, 100 - float((args.get('combatExtraStats') or {}).get('CRIT_RATE', 0))))
    goals = [0, profile.get('fullCritPanelTarget', estimated_panel), args.get('targetCritRate') or 0]
    if exact:
        goals = [0]
    by_id = {d['id']: d for d in discs}
    results = {}
    searches = [(args, g) for g in dict.fromkeys(goals)]
    if len(current)==6 and current <= by_id.keys():
        searches.append((dict(original_args, discs=[by_id[i] for i in sorted(current)]), 0))
    for inputs, goal in searches:
        response = request(url, dict(inputs,targetCritRate=goal))
        for r in response['results']:
            ids = result_ids(r, {d['id']: d for d in inputs['discs']} if inputs is not args else by_id)
            if ids not in results:
                from .direct_score import direct_score
                try:
                    score = direct_score(inputs, r)
                except (ValueError, KeyError) as exc:
                    from .role_panel import observed_panel
                    try:
                        panel = observed_panel(original_args, r)
                        before = panel if set(ids) == current else current_panel(url, original_args, current, by_id, request)
                    except (ValueError, KeyError) as panel_error:
                        raise ValueError(f'{exc} · 无法核实面板 · {panel_error}') from exc
                    from .role_panel import goal_rows
                    rows = goal_rows(original_args, panel, before)
                    changes = [f'{label} {old:g} → {new:g}' if old is not None else f'{label} {new:g}'
                               for label, _, _, old, new, _, _ in rows]
                    subject = '当前 → 候选' if before else '候选面板'
                    raise ValueError(f'{exc} · {subject} · '+ '、'.join(changes)) from exc
                results[ids] = dict(ids=list(ids), panelCritRate=r.get('panelCritRate'), **score)
    if not results:
        raise NoBuildCandidates('该组条件没有完整的六盘方案')
    return list(results.values())


def candidate_pool(url, profile, discs, current, request=call):
    # Keep the upstream counts of unrestricted inventory queries. An evaluation
    # of the current six discs (or a single multi-window candidate) is separate.
    evaluations = []
    def measured_request(endpoint, params):
        response = request(endpoint, params)
        if params.get('discs') is discs:
            searched = response.get('searchedCombinations')
            if type(searched) is int and searched >= 0:
                evaluations.append((searched, len(response['results']), params['topN']))
        return response

    builds = _profile_candidates(url, profile, discs, current, measured_request)
    if builds and 'rawOutput' in builds[0] and len(current) == 6:
        worn = next((row for row in builds if set(row['ids']) == current), None)
        if worn is None:
            worn = worn_output(url, profile, discs, current, request)
        if worn is not None:
            for build in builds:
                build['wornOutput'] = worn['rawOutput']
    if evaluations:
        scope = dict(submitted=len(discs), calls=len(evaluations),
                     evaluated=sum(searched for searched, _, _ in evaluations),
                     atReturnLimit=any(returned >= limit for _, returned, limit in evaluations))
        for build in builds:
            build['optimizerScope'] = scope
    return builds


def worn_output(url, profile, discs, current, request):
    from .role_panel import baseline_request
    pieces = [disc for disc in discs if disc['id'] in current]
    if len(pieces) != 6 or {disc['slot'] for disc in pieces} != set(range(1, 7)):
        return None
    # A build can be worn even though it does not meet the new target. Measure
    # these six discs in the same combat windows, for display only.
    measured_profile = dict(profile, request=baseline_request(profile['request'], pieces), fullCritPanelTarget=0)
    try:
        measured = _profile_candidates(url, measured_profile, pieces, set(), request)
    except NoBuildCandidates:
        return None
    return next((row for row in measured if set(row['ids']) == current), None)


def _profile_candidates(url, profile, discs, current, request):
    from .combat_windows import window_requests, combine
    windows = window_requests(profile)
    if not windows:
        return _window_pool(url, profile, discs, current, request)

    by_id = {d['id']: d for d in discs}
    pools = []
    ids_to_check = {}
    for name, weight, args in windows:
        configured = dict(profile, request=args)
        rows = _window_pool(url, configured, discs, current, request)
        indexed = {tuple(row['ids']):row for row in rows}
        ids_to_check.update(dict.fromkeys(indexed))
        pools.append((name, weight, configured, indexed))

    combined = []
    for ids in ids_to_check:
        rows = []
        for name, weight, configured, indexed in pools:
            row = indexed.get(ids)
            if row is None:
                # Score the identical six discs even if they fell below this window's top N.
                try:
                    exact = _window_pool(url, configured, [by_id[i] for i in ids], set(), request, exact=True)
                except NoBuildCandidates:
                    break
                row = next(r for r in exact if tuple(r['ids']) == ids)
            rows.append(dict(row, name=name, weight=weight))
        if len(rows) == len(pools):
            combined.append(combine(rows, profile['mainWindow']))
    if not combined:
        raise NoBuildCandidates('各窗口的配装条件没有共同的六盘方案')
    return combined


def select_candidate(pool, profile, current):
    if 'outputScore' in pool[0]:
        return prefer(pool, profile.get('tolerancePct', 0.5), current)
    return dict(pool[0])


def optimize_profile(url, profile, discs, current, request=call):
    pool = candidate_pool(url, profile, discs, current, request)
    return select_candidate(pool, profile, current)
