"""Changes between the confirmed lineup and its fresh warehouse scan."""
from .disc_owner import owner_label
from .stat_reading import format_stats


def changed_values(values, other):
    return format_stats({key:value for key, value in values.items() if value != other.get(key)}) or '—'


def changed_slots(before, after, sets, agents):
    previous = {(t['profile'], t['agent'], t['slot']): t for t in before}
    rows = []
    for target in after:
        key = (target['profile'], target['agent'], target['slot'])
        old = previous[key]
        slot = f"{target['profile']} · {target['slot']} 号位"
        start = len(rows)
        for field, label in [('set', '套装'), ('rarity', '品级'), ('level', '强化等级')]:
            first, second = old[field], target[field]
            if field == 'set':
                first, second = sets.get(first, first), sets.get(second, second)
            if first != second:
                rows.append((slot, label, str(first), str(second)))
        for group, label in [('main', '主属性'), ('subs', '副属性')]:
            a, b = old['observedValues'][group], target['observedValues'][group]
            if a != b:
                rows.append((slot, label, changed_values(a, b), changed_values(b, a)))
        if old['selectionFp'] != target['selectionFp'] and len(rows) == start:
            rows.append((slot, '目标盘', '原方案用盘', '新方案选中了另一张盘'))
        if (old['location'], old['ownerState']) != (target['location'], target['ownerState']):
            first, second = owner_label(old), owner_label(target)
            rows.append((slot, '装备者', agents.get(first, first), agents.get(second, second)))
    return rows
