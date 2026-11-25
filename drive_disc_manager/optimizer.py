"""Use the optimizer's existing team inputs, combat critRate and outputScore directly."""
import http.client
import json
from decimal import Decimal
from urllib.parse import urlsplit
from .store import fingerprint

# S-rank values from the optimizer's rollValue / ocrDefaultMainStatValue.
STATS = {'hp':('HP_FLAT',112,2200), 'atk':('ATK_FLAT',19,316), 'def':('DEF_FLAT',15,184),
         'atk_':('ATK_PERCENT',3,30), 'hp_':('HP_PERCENT',3,30), 'def_':('DEF_PERCENT',4.8,48),
         'crit_':('CRIT_RATE',2.4,24), 'crit_dmg_':('CRIT_DMG',4.8,48), 'anomProf':('ANOMALY_PROFICIENCY',9,92),
         'pen':('PEN_FLAT',9,None), 'pen_':('PEN_RATIO',None,24), 'impact':('IMPACT',None,18),
         'energyRegen_':('ENERGY_REGEN',None,60), 'anomMas_':('ANOMALY_MASTERY',None,30),
         **{e+'_dmg_':(e.upper()+'_DMG',None,30) for e in ('physical','electric','fire','ice','ether','wind','lumiflux')}}


def as_disc(d, names):
    if d['rarity'] != 'S' or d['setKey'] not in names:
        return None
    main = STATS.get(d['mainStatKey'])
    if not main or main[2] is None:
        return None
    value = d.get('mainStatValue', main[2] if d['level']==15 else None)
    if value is None or len(d['substats']) < 3:
        return None
    subs = []
    for s in d['substats']:
        stat = STATS.get(s['key'])
        if not stat or stat[1] is None or s['key']==d['mainStatKey']:
            return None
        subs.append(dict(type=stat[0], value=round(stat[1]*(1+s['upgrades']), 4)))
    return dict(id=fingerprint(d), setName=names[d['setKey']], slot=int(d['slotKey']), level=d['level'],
                rarity='S', mainStat=dict(type=main[0], value=value),subStats=subs,
                equippedBy=d.get('location',''),locked=bool(d.get('lock')),discarded=bool(d.get('trash')))


def loopback_json(url, route, payload=None):
    uri = urlsplit(url)
    if uri.scheme != 'http' or uri.hostname not in ('127.0.0.1','localhost','::1') or not uri.port or uri.path not in ('','/') or uri.username or uri.password or uri.query or uri.fragment:
        raise ValueError('填写配装器窗口显示的本机 HTTP 地址和端口')
    # Direct loopback connection. Never proxy or follow redirects with private inventory.
    con = http.client.HTTPConnection(uri.hostname, uri.port, timeout=60)
    try:
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


def number(v):
    n = Decimal(str(v))
    if not n.is_finite():
        raise ValueError('配装数值异常')
    return n


def prefer(candidates, tolerance_pct, current):
    tol = number(tolerance_pct)/100
    if not 0 <= tol <= 1:
        raise ValueError('输出容差应在 0–100% 之间')
    def key(c):
        return number(c['outputScore']), sum(i in current for i in c['ids']), tuple(c['ids'])
    best = max(candidates, key=key)
    if key(best)[0] <= 0:
        raise ValueError('缺少正的预期输出')
    full = [c for c in candidates if number(c['critRate']) >= 100]
    cap = max(full,key=key) if full else None
    chosen = cap if cap and key(cap)[0] >= key(best)[0]*(1-tol) else best
    return dict(chosen, bestOutput=best['outputScore'], relativeLossPct=float(100*(1-key(chosen)[0]/key(best)[0])))


def optimize_profile(url, profile, discs, current, request=call):
    if not discs:
        raise ValueError('缺少适用的驱动盘')
    args = dict(profile['request'])
    if not args.get('roleSystem') or not args.get('characterName'):
        raise ValueError('配队需要配装器角色与职业配置')
    direct = args['roleSystem'].upper() in ('ATTACK','STRONG','RUPTURE')
    args.update(discs=discs, excludeDiscarded=True, topN=24, maxCombinations=profile.get('searchLimit',200000))
    original_args = dict(args)
    goals = [0,profile.get('fullCritPanelTarget',args.get('targetCritRate') or 100)] if direct else [args.get('targetCritRate',0)]
    by_id = {d['id']: d for d in discs}
    results = {}
    searches = [(args, g) for g in dict.fromkeys(goals)]
    if len(current)==6 and current <= by_id.keys():
        searches.append((dict(original_args, discs=[by_id[i] for i in sorted(current)]), 0 if direct else goals[0]))
    for inputs, goal in searches:
        response = request(url, dict(inputs,targetCritRate=goal))
        for r in response['results']:
            ids = [d['id'] for d in sorted(r['discs'], key=lambda d:d['slot'])]
            if len(ids)!=6 or [by_id[i]['slot'] if i in by_id else 0 for i in ids] != list(range(1,7)):
                raise ValueError('配装器结果与本轮库存不一致')
            for item in r['discs']:
                source = by_id[item['id']]
                if any(item.get(k) != source[k] for k in ('slot','mainStat','setName','subStats')):
                    raise ValueError('配装器修正过属性，请复核实物')
            if not direct and tuple(ids) not in results:
                return dict(ids=ids,reason='沿用该职业的配装器排名', score=r['score'])
            results[tuple(ids)] = dict(ids=ids, outputScore=r['outputScore'],critRate=r['critRate'])
    if not results:
        raise ValueError('该组条件没有完整的六盘方案')
    return prefer(results.values(),profile.get('tolerancePct',0.5),current)
