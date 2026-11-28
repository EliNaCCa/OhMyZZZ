"""Read stat values from the same OCR rows as their labels and roll badges."""
import re
import unicodedata
from decimal import Decimal


MAIN_STAT_NAMES = {'hp_':'生命值百分比', 'atk_':'攻击力百分比', 'def_':'防御力百分比',
                   'crit_':'暴击率', 'crit_dmg_':'暴击伤害', 'anomProf':'异常精通', 'pen_':'穿透率',
                   'impact':'冲击力', 'energyRegen_':'能量自动回复', 'anomMas_':'异常掌控',
                   'physical_dmg_':'物理伤害', 'fire_dmg_':'火属性伤害', 'ice_dmg_':'冰属性伤害',
                   'electric_dmg_':'电属性伤害', 'ether_dmg_':'以太伤害', 'wind_dmg_':'风属性伤害',
                   'lumiflux_dmg_':'流光伤害'}


def compact(text):
    return ''.join(unicodedata.normalize('NFKC', str(text)).split()).casefold()


def format_stats(items, *, signed=False):
    labels = {'hp':'生命值','atk':'攻击力','def':'防御力','crit':'暴击率','crit_dmg':'暴击伤害',
              'anomProf':'异常精通','anomMas':'异常掌控','impact':'冲击力','energyRegen':'能量自动回复',
              'pen':'穿透值','pen_':'穿透率','physical_dmg':'物理伤害','ice_dmg':'冰属性伤害',
              'fire_dmg':'火属性伤害','electric_dmg':'电属性伤害','ether_dmg':'以太伤害',
              'wind_dmg':'风属性伤害','lumiflux_dmg':'流光伤害'}
    parts = []
    for key, value in items.items():
        label = labels.get(key) or labels.get(key.rstrip('_'), key)
        unit = '%' if key.endswith('_') or key == 'impact' else ''
        amount = f'{value:+g}' if signed else f'{value:g}'
        parts.append(f'{label} {amount}{unit}')
    return '、'.join(parts)


def display_values(values):
    if not values:
        return '词条数值待核 · 可用 OhMyZZZ 重新扫描'
    return '主属性　' + format_stats(values['main']) + '　｜　副属性　' + format_stats(values['subs'])


def read_row(row, labels):
    if len(row) not in (2, 3) or any(part[4] < .85 for part in row):
        return None
    row.sort(key=lambda part:part[1])
    label = re.fullmatch(r'(.+?)(?:[+]([1-5]))?', row[0][3])
    value = re.fullmatch(r'([0-9]{1,5}(?:[.][0-9]{1,2})?)(%?)', row[-1][3])
    if not label or not value or row[-1][1] <= row[0][2]:
        return None
    upgrade = int(label[2] or 0)
    if len(row) == 3:
        badge = re.fullmatch(r'[+]([1-5])', row[1][3])
        if upgrade or not badge:
            return None
        upgrade = int(badge[1])
    key = labels.get(label[1])
    percent = bool(value[2])
    if key and key.rstrip('_') in ('hp','atk','def'):
        key = key.rstrip('_') + ('_' if percent else '')
    if not key or percent != (key.endswith('_') or key == 'impact'):
        return None
    return key, Decimal(value[1]), upgrade


def valid_slot_main(disc):
    from .subscriptions import SLOT_MAIN
    return disc['mainStatKey'] in SLOT_MAIN.get(str(disc['slotKey']), ())


ELEMENT_LABELS = [('物理','physical'),('火','fire'),('冰','ice'),('电','electric'),('以太','ether'),('风','wind'),('流光','lumiflux')]


def label_maps(parser):
    main = {compact(k):v for k,v in parser.MAIN_STAT_MAP.items()}
    # The game prints "以太伤害加成" on slot 5. The scanner's own parser only
    # substring-matches these labels, so it recognises a disc this check must also accept.
    main.update({k+s:v+'_dmg_' for k, v in ELEMENT_LABELS for s in ('属性伤害加成', '伤害加成')})
    return main, {compact(k):v for k,v in parser.SUB_STAT_MAP.items()}


def measure(items, disc, parser):
    from .optimizer import STATS
    if not valid_slot_main(disc):
        return None
    entries = []
    for item in items:
        pos = item.get('position')
        if not pos or len(pos) != 4:
            return None
        left, top, right, bottom = map(int, pos)
        entries.append(((top + bottom) / 2, left, right, compact(item['text']), item.get('confidence', 0)))
    mains = [y for y, _, _, t, c in entries if t in ('主属性','mainstat') and c >= .85]
    headers = [y for y, _, _, t, c in entries if t in ('副属性','sub-stats','substats') and c >= .85]
    if len(mains) != 1 or len(headers) != 1 or mains[0] >= headers[0]:
        return None
    main_map, sub_map = label_maps(parser)
    rows = []
    for entry in sorted(entries):
        if entry[0] <= mains[0] or entry[0] == headers[0]:
            continue
        if not rows or entry[0] - rows[-1][0][0] > 12:
            rows.append([])
        rows[-1].append(entry)
    main, subs = {}, {}
    for row in rows:
        is_main = row[0][0] < headers[0]
        if not is_main and row[0][3] in ('套装效果','套装属性','seteffect','seteffects'):
            break
        parsed = read_row(row, main_map if is_main else sub_map)
        if not parsed:
            return None
        key, qty, upgrade = parsed
        if key not in STATS or qty <= 0:
            return None
        if is_main:
            maximum = STATS[key][2]
            if main or upgrade or key != disc['mainStatKey'] or maximum is None or qty > maximum:
                return None
            if disc['rarity'] == 'S':
                # Levels are not always multiples of three: a disc may be raised one level at a time.
                from .optimizer import exported_s_main
                if qty != Decimal(str(exported_s_main(key, disc['level']))):
                    return None
            main[key] = float(qty)
        else:
            rolls = 1 + upgrade
            if key in subs or {'key':key, 'upgrades':rolls} not in disc['substats']:
                return None
            step = STATS[key][1]
            if disc['rarity'] == 'S' and (not step or qty != Decimal(str(step)) * rolls):
                return None
            subs[key] = float(qty)
    if not main or not subs or len(subs) != len(disc['substats']):
        return None
    return {'main':main, 'subs':subs}


def checked_values(disc, roll_base):
    """Validate snapshot evidence before using it in a plan or matching a live disc."""
    from .optimizer import STATS
    if not valid_slot_main(disc):
        return None
    values = disc.get('observedValues')
    if not isinstance(values, dict) or not isinstance(values.get('main'), dict) or not isinstance(values.get('subs'), dict):
        return None
    main, subs = values['main'], values['subs']
    key = disc['mainStatKey']
    if set(main) != {key} or set(subs) != {s['key'] for s in disc['substats']} or key in subs:
        return None
    if any(type(v) not in (int, float) or not 0 < v <= 99999 for v in (*main.values(), *subs.values())):
        return None
    maximum = STATS.get(key, (None, None, None))[2]
    if maximum is None or main[key] > maximum or (disc['rarity'] == 'S' and disc['level'] == 15 and main[key] != maximum):
        return None
    for sub in disc['substats']:
        stat = sub['key']
        step = STATS.get(stat, (None, None))[1]
        if disc['rarity'] == 'S' and (not step or subs[stat] != round(step * (1 + sub['upgrades'] - roll_base), 4)):
            return None
    return {'main':main, 'subs':subs}
