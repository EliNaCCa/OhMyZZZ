"""Apply explicitly external window buffs once to the optimizer's imported combat stats."""
from .optimizer import number


DIRECT_ROLES = {'ATTACK', 'STRONG', 'RUPTURE'}
WINDOW_STATS = ('CRIT_RATE', 'CRIT_DMG', 'ATK_PERCENT', 'ATK_FLAT', 'HP_PERCENT', 'HP_FLAT',
                'ELEMENT_DMG', 'FIRE_DMG', 'ICE_DMG', 'ELECTRIC_DMG', 'ETHER_DMG', 'PHYSICAL_DMG',
                'WIND_DMG', 'LUMIFLUX_DMG', 'SHEER_DMG_BONUS')


def window_requests(profile):
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
            if not source or stat not in WINDOW_STATS or (source.casefold(), stat) in seen:
                raise ValueError('请核对窗口增益的来源、词条及重复项')
            seen.add((source.casefold(), stat))
            mode = buff.get('mode')
            if mode == 'additional':
                stats[stat] = float(number(stats.get(stat, 0)) + value)
            elif mode == 'inactive':
                baseline = number((base.get('combatExtraStats') or {}).get(stat, 0))
                inactive[stat] = inactive.get(stat, 0) + value
                if value < 0 or inactive[stat] > baseline:
                    raise ValueError('关闭已计入增益时，数值应在导入的同项实战加成内')
                stats[stat] = float(number(stats.get(stat, 0)) - value)
            elif mode != 'included':
                raise ValueError('请指定增益是否已计入配装器，以及本窗口是否生效')
        prepared.append((name, weight, dict(base, combatExtraStats=stats)))
    if profile.get('mainWindow', '') not in names:
        raise ValueError('请选择判断近满暴的主要窗口')
    return prepared


def combine(window_rows, main):
    weight = sum(row['weight'] for row in window_rows)
    score = sum(row['weight'] * number(row.get('rawOutput', row['outputScore'])) for row in window_rows) / weight
    primary = next(row for row in window_rows if row['name'] == main)
    return dict(ids=primary['ids'], critRate=primary['critRate'], rawCritRate=primary['rawCritRate'], outputScore=float(score), rawOutput=str(score),
                uncappedCritRate=primary['uncappedCritRate'], overflowCritRate=primary['overflowCritRate'],
                mainWindow=main, windows=[dict(name=row['name'], share=float(row['weight']/weight),
                    outputScore=row['outputScore'], rawOutput=row['rawOutput'], critRate=row['critRate'], panelCritRate=row.get('panelCritRate'),
                    uncappedCritRate=row['uncappedCritRate'], overflowCritRate=row['overflowCritRate'],
                    metric=row['metric'], metricValue=row['metricValue'], critDamage=row['critDamage'], damageBonus=row['damageBonus'], sheerBonus=row['sheerBonus'])
                    for row in window_rows])
