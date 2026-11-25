"""Reconstruct the direct damage index of the pinned optimizer v2.3.31.1.

Use its final integer ATK/HP/DEF, canonical set counts and the six returned
discs. Check the reconstructed fields against its public, rounded response.
"""
from collections import defaultdict
from decimal import Decimal, ROUND_FLOOR

from .optimizer import number


ELEMENTS = ('FIRE', 'ICE', 'ELECTRIC', 'PHYSICAL', 'ETHER', 'WIND', 'LUMIFLUX')
PAIR_DAMAGE = {
    '极地重金属': 'ICE', '囚徒手记': 'ICE', '炎狱重金属': 'FIRE', '雷暴重金属': 'ELECTRIC',
    '獠牙重金属': 'PHYSICAL', '沧浪行歌': 'PHYSICAL', '混沌重金属': 'ETHER',
    '流光咏叹': 'ETHER', '拂晓行纪': 'ETHER', '呼啸沙龙': 'WIND',
}


def direct_score(request, result):
    totals = defaultdict(Decimal)
    for group in ('extraStats', 'combatExtraStats'):
        for key, value in (request.get(group) or {}).items():
            amount = number(value)
            if abs(amount) > Decimal('1e-9'):
                totals[key.strip()] += amount
    for disc in result['discs']:
        for stat in [disc['mainStat'], *disc['subStats']]:
            totals[stat['type']] += number(stat['value'])

    sets = result['setSummary']
    element = request.get('characterElement', '').strip().upper()
    if element == 'LUMEN':
        element = 'LUMIFLUX'
    for name, count in sets.items():
        if count < 2:
            continue
        if name in PAIR_DAMAGE:
            totals[PAIR_DAMAGE[name]+'_DMG'] += 10
        if name == '折枝剑歌':
            totals['CRIT_DMG'] += 16
        if name == '啄木鸟电音':
            totals['CRIT_RATE'] += 8
        if count < 4:
            continue
        if name == '云岿如我':
            totals['CRIT_RATE'] += 12
            totals['SHEER_DMG_BONUS'] += 10
        if name == '呼啸沙龙':
            totals['ELEMENT_DMG'] += 18
        if name == '拂晓行纪' and element == 'ETHER':
            totals['CRIT_DMG'] += 30
        if name == '荆棘玫瑰':
            defense = number(result['finalDefense'])
            totals['CRIT_RATE'] += 16 if defense >= 1800 else 8 if defense >= 1000 else 0
            totals['ELEMENT_DMG'] += 15

    if '维琳娜' in request['characterName'].replace('·', '').replace(' ', ''):
        # This passive uses the INITIAL panel energy, excluding combat buffs.
        energy_pct = number((request.get('extraStats') or {}).get('ENERGY_REGEN', 0))
        energy_pct += sum(number(s['value']) for d in result['discs'] for s in [d['mainStat'], *d['subStats']] if s['type'] == 'ENERGY_REGEN')
        energy_pct += 20 * sum(sets.get(name, 0) >= 2 for name in ('摇摆爵士', '月光骑士颂'))
        energy = number(request.get('baseEnergyRegen', 0)) * (1 + energy_pct / 100)
        if energy > Decimal('1.2'):
            steps = ((energy - Decimal('1.2') + Decimal('1e-9')) * 100).to_integral_value(rounding=ROUND_FLOOR)
            totals['VELINA_CORE_DMG_BONUS'] += min(35, steps * Decimal('.21'))

    def crit(base, extra, key, default):
        return (number(request.get(base, 0)) or default) + number(request.get(extra, 0)) + totals[key]

    rate = min(100, crit('baseCritRate', 'extraCritRate', 'CRIT_RATE', 5))
    damage = crit('baseCritDmg', 'extraCritDmg', 'CRIT_DMG', 50)
    multiplier = 1 + max(0, rate) * max(0, damage) / 10000
    bonus = totals['ELEMENT_DMG'] + totals['VELINA_CORE_DMG_BONUS']
    bonus += sum(totals[e+'_DMG'] for e in ((element,) if element in ELEMENTS else ELEMENTS))
    metric = number(result['combatFinalAttack'])  # upstream floors the in-game value
    rupture = request['roleSystem'].upper() == 'RUPTURE'
    if rupture:
        ratio = number(request.get('hpToSheerRatio', 0)) or Decimal('.1')
        def game_floor(value):
            return (value + Decimal('1e-9')).to_integral_value(rounding=ROUND_FLOOR)
        metric = (game_floor(metric * Decimal('.3')) + game_floor(number(result['combatFinalHp']) * ratio)
                  + totals['SHEER_FORCE'] + totals['SHEER_FORCE_FLAT'])
    score = metric * multiplier * (1 + bonus / 100)
    if rupture:
        score *= 1 + totals['SHEER_DMG_BONUS'] / 100

    for field, value, places in [('critRate', rate, 3), ('critDmg', damage, 3), ('outputScore', score, 4)]:
        # Allow half a displayed unit, including binary64 representation at a rounding boundary.
        if abs(number(result[field]) - value) > Decimal('0.5').scaleb(-places) + Decimal('1e-9'):
            raise ValueError('本地输出复算与配装器的 '+field+' 不符，请复核当前角色模型')
    return dict(outputScore=float(score), rawOutput=str(score), critRate=float(rate), rawCritRate=str(rate),
                metric='贯穿力' if rupture else '攻击力', metricValue=float(metric), damageBonus=float(bonus),
                critDamage=float(damage), sheerBonus=float(totals['SHEER_DMG_BONUS']) if rupture else 0)
