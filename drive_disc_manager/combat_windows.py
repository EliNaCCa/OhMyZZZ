"""Apply explicitly external window buffs once to the optimizer's imported combat stats."""
from .optimizer import number


DIRECT_ROLES = {'ATTACK', 'STRONG', 'RUPTURE'}
WINDOW_STATS = ('CRIT_RATE', 'CRIT_DMG', 'ATK_PERCENT', 'ATK_FLAT', 'HP_PERCENT', 'HP_FLAT',
                'ELEMENT_DMG', 'FIRE_DMG', 'ICE_DMG', 'ELECTRIC_DMG', 'ETHER_DMG', 'PHYSICAL_DMG',
                'WIND_DMG', 'LUMIFLUX_DMG', 'SHEER_DMG_BONUS')


def squad_ranks(profile):
    def rank(value):
        if type(value) is not int or not 0 <= value <= 6:
            raise ValueError('影画可选 0 到 6 影')
        return value

    members = profile.get('squad', [])
    if not isinstance(members, list) or len(members) > 2:
        raise ValueError('最多可选两名队友')
    ranks = {'$self': rank(profile.get('mindscape', 0))}
    for row in members:
        name = row.get('agent') if isinstance(row, dict) else None
        if not isinstance(name, str) or not name.strip() or name != name.strip() or name in ranks:
            raise ValueError('请选择不同的队友')
        if name == profile.get('request', {}).get('characterName'):
            raise ValueError('当前代理人不能同时作为队友')
        ranks[name] = rank(row.get('mindscape', 0))
    return ranks


def bound_mode(buff, ranks):
    mode = buff.get('mode')
    if mode not in ('additional', 'included', 'inactive'):
        raise ValueError('请选择增益的计算方式')
    member = buff.get('member')
    if member is None:
        return mode
    minimum = buff.get('requiredMindscape', 0)
    if not isinstance(member, str) or not member.strip() or type(minimum) is not int or not 0 <= minimum <= 6:
        raise ValueError('请核对增益的来源角色和所需影画')
    if ranks.get(member, -1) >= minimum:
        return mode
    return 'inactive' if mode in ('included', 'inactive') else 'off'


def window_requests(profile):
    ranks = squad_ranks(profile)
    windows = profile.get('windows', [])
    if not isinstance(windows, list):
        raise ValueError('窗口应为列表')
    if not windows or profile.get('windowsEnabled') is False:
        return []
    base = profile['request']
    if base.get('roleSystem', '').upper() not in DIRECT_ROLES:
        raise ValueError('输出窗口适用于强攻和命破的直伤方案')
    prepared = []
    names = set()
    for row in windows:
        name = row['name'].strip()
        if not name or name in names:
            raise ValueError('每个窗口需要独立的名称')
        names.add(name)
        weight = number(row['weight'])
        if weight <= 0:
            raise ValueError('窗口输出占比需要大于零')
        stats = dict(base.get('combatExtraStats') or {})
        seen = set()
        inactive = {}
        for buff in row.get('buffs', []):
            source, stat = buff['source'].strip(), buff['stat']
            value = number(buff['value'])
            identity = (buff.get('member'), source.casefold(), stat)
            mode = bound_mode(buff, ranks)
            if not source or stat not in WINDOW_STATS or identity in seen:
                raise ValueError('请核对窗口增益的来源、词条及重复项')
            seen.add(identity)
            if mode == 'additional':
                stats[stat] = float(number(stats.get(stat, 0)) + value)
            elif mode == 'inactive':
                baseline = number((base.get('combatExtraStats') or {}).get(stat, 0))
                inactive[stat] = inactive.get(stat, 0) + value
                if value < 0 or inactive[stat] > baseline:
                    raise ValueError('关闭已计入增益时，数值应在导入的同项实战加成内')
                stats[stat] = float(number(stats.get(stat, 0)) - value)
        prepared.append((name, weight, dict(base, combatExtraStats=stats)))
    if profile.get('mainWindow', '') not in names:
        raise ValueError('请选择判断满暴的主要窗口')
    return prepared


def buff_evidence(profile):
    ranks = squad_ranks(profile)
    return {row['name'].strip(): [dict(buff, appliedMode=bound_mode(buff, ranks)) for buff in row.get('buffs', [])]
            for row in profile.get('windows', [])}


def combine(window_rows, main, evidence):
    weight = sum(row['weight'] for row in window_rows)
    score = sum(row['weight'] * number(row.get('rawOutput', row['outputScore'])) for row in window_rows) / weight
    primary = next(row for row in window_rows if row['name'] == main)
    return dict(ids=primary['ids'], critRate=primary['critRate'], rawCritRate=primary['rawCritRate'], outputScore=float(score), rawOutput=str(score),
                uncappedCritRate=primary['uncappedCritRate'], overflowCritRate=primary['overflowCritRate'],
                mainWindow=main, windows=[dict(name=row['name'], share=float(row['weight']/weight), buffs=evidence[row['name']],
                    outputScore=row['outputScore'], rawOutput=row['rawOutput'], critRate=row['critRate'], rawCritRate=row['rawCritRate'], panelCritRate=row.get('panelCritRate'),
                    uncappedCritRate=row['uncappedCritRate'], overflowCritRate=row['overflowCritRate'],
                    metric=row['metric'], metricValue=row['metricValue'], critDamage=row['critDamage'], damageBonus=row['damageBonus'], sheerBonus=row['sheerBonus'])
                    for row in window_rows])
