"""Panel metrics and target semantics of the local optimizer v2.3.31.1."""
from math import isfinite


# The result names refer to the agent-details panel, including its base attributes.
FIELDS = {
    'ATK': ('攻击力', 'targetFinalAttack', 'finalAttack'),
    'HP': ('生命值', 'targetFinalHp', 'finalHp'),
    'DEF': ('防御力', 'targetFinalDefense', 'finalDefense'),
    'CRIT_RATE': ('暴击率', 'targetCritRate', 'panelCritRate'),
    'CRIT_DMG': ('暴击伤害', 'targetCritDmg', 'panelCritDmg'),
    'IMPACT': ('冲击力', 'targetImpact', 'panelImpact'),
    'ENERGY_REGEN': ('能量自动回复', 'targetEnergyRegen', 'panelEnergyRegen'),
    'ANOMALY_PROFICIENCY': ('异常精通', 'targetAnomalyProficiency', None),
    'ANOMALY_MASTERY': ('异常掌控', 'targetAnomalyMastery', 'panelAnomalyMastery'),
}
ROLE_FIELDS = {
    'SUPPORT': ('ATK', 'ENERGY_REGEN'), 'STUN': ('IMPACT', 'ENERGY_REGEN'),
    'ANOMALY': ('ANOMALY_PROFICIENCY', 'ANOMALY_MASTERY', 'ATK'),
    'DEFENSE': ('DEF', 'HP', 'ENERGY_REGEN'), 'ARMORER': ('DEF', 'CRIT_RATE'),
}
MODE_NAMES = {'UTILITY_BALANCE':'综合辅助指标', 'ANOMALY_AP':'优先异常精通', 'ANOMALY_ATK':'优先面板攻击',
              'ANOMALY_WORDS':'优先异常有效词条', 'STRICT_TARGET':'严格指标', 'STRICT_TARGETS':'严格指标',
              'MAX_WORDS':'优先有效词条', 'MAX_CD':'优先暴击伤害'}

DIRECT_FIELDS = ('ATK', 'CRIT_RATE', 'CRIT_DMG')


def target_value(request, key):
    def read(field):
        value = request.get(field)
        if value is None:
            return 0
        if type(value) not in (int, float) or not isfinite(value):
            raise ValueError('配装器的' + FIELDS[key][0] + '目标数值无效')
        return value

    value = read(FIELDS[key][1])
    if value <= 0 and key in ('ATK', 'CRIT_DMG'):
        fallback = 'minFinalAttack' if key == 'ATK' else 'minPanelCritDmg'
        value = read(fallback)
    return value if value > 0 else None


def target_kind(request, key, target):
    mode = (request.get('mode') or '').strip().upper()
    if not target:
        return '面板参考'
    if mode in ('STRICT_TARGET', 'STRICT_TARGETS'):
        kind = (request.get('targetTypes') or {}).get(key, 'MINIMUM')
        if key in ('ANOMALY_MASTERY','ENERGY_REGEN','IMPACT'):
            kind = 'MINIMUM'
        priority = (request.get('targetPriorities') or {}).get(key)
        if priority not in range(1,7):
            priority = 6
        return f"优先级 {priority} · "+{'CLOSE':'贴近目标','CAP':'收益上限'}.get(str(kind).upper(),'最低目标')
    if key == 'CRIT_RATE':
        return '目标 ±2.4 个百分点'
    if key == 'ANOMALY_PROFICIENCY':
        return '下限宽容 9 点'
    if key in ('ATK','HP','DEF') and (request['roleSystem'].upper() in ('SUPPORT','STUN','DEFENSE') or mode=='UTILITY_BALANCE'):
        return f'目标 ±{max(1,target * .025):g}'
    return '面板下限'


def observed_panel(request, result):
    role = request['roleSystem'].strip().upper()
    if (result.get('roleSystem') or '').strip().upper() != role:
        raise ValueError('配装器返回的职业与方案不符')
    defaults = ROLE_FIELDS.get(role, ('ATK', 'HP', 'DEF'))
    if role in ('ATTACK', 'STRONG', 'RUPTURE'):
        defaults = DIRECT_FIELDS + (('HP',) if role == 'RUPTURE' else ())
    keys = dict.fromkeys([*(k for k in FIELDS if target_value(request,k)), *defaults])
    panel = {}
    for key in keys:
        label, _, api = FIELDS[key]
        if api:
            value = result.get(api)
        else:
            stats = result.get('stats')
            value = stats.get('ANOMALY_PROFICIENCY', 0) if isinstance(stats,dict) else None
        if type(value) not in (float,int) or not isfinite(value):
            raise ValueError('配装器缺少有效的面板'+label)
        panel[key] = value
    return panel


def baseline_request(request, discs):
    # Preserve the character, set effects and buffs. Only measure this exact set
    # of worn pieces, including when it doesn't meet the new build requirements.
    payload = dict(request, discs=discs, setPattern='', required4Set='', required2Set='', required2Sets=[],
                   slotAllowedMainStats={}, topN=1, minPanelCritDmg=0, minFinalAttack=0, minSheerForce=0)
    for _, field, _ in FIELDS.values():
        payload[field] = 0
    return payload


def goal_rows(request, selected, current):
    rows = []
    for key, value in selected.items():
        goal = target_value(request,key)
        before = current.get(key)
        rows.append((FIELDS[key][0], target_kind(request,key,goal), goal, before, value,
                     round(value-before,4) if before is not None else None,
                     round(value-goal,4) if goal is not None else None))
    return rows
