"""Join explicitly identified scanner characters, build sets and guide recommendations."""
import re
from collections import defaultdict

from .subscriptions import ELEMENTS, STATS, effective


def identifier(value):
    return ''.join(c for c in value.casefold() if c.isalnum())


# Sheet labels describe distinct agents, including alternate forms of Billy and Anby.
# Join only a whole known name or alias, never a first-name substring.
_NAMES = {
    'Alice': ['Alice Thymefield'], 'Anby': ['Anby Demara'], 'Anby: Soldier 0': ['Soldier 0 - Anby'],
    'Anton': ['Anton Ivanov'], 'Ben': ['Ben Bigger'], 'Billy': ['Billy Kid'],
    'Billy - Starlight': ['Starlight - Billy Kid'], 'Burnice': ['Burnice White'],
    'Caesar': ['Caesar King'], 'Claret': ['Claret Flint'], 'Corin': ['Corin Wickes'],
    'Ellen': ['Ellen Joe'], 'Evelyn': ['Evelyn Chevalier'], 'Grace': ['Grace Howard'],
    'Harumasa': ['Asaba Harumasa'], 'Hugo': ['Hugo Vlad'], 'Jane Doe': ['Jane'],
    'Koleda': ['Koleda Belobog'], 'Lucia': ['Lucia Elowen'],
    'Lucy': ['Luciana Auxesis Theodoro de Montefio (Lucy)'], 'Lycaon': ['Von Lycaon'],
    'Manato': ['Komano Manato'], 'Miyabi': ['Hoshimi Miyabi'], 'Nekomata': ['Nekomiya Mana'],
    'Nicole': ['Nicole Demara'], 'Norma': ['Norma Hollowell'], 'Orphie & Magus': ['Orphie Magnusson & Magus'],
    'Phoenix': ['Phoenix Reffaella'], 'Piper': ['Piper Wheel'], 'Pulchra': ['Pulchra Fellini'],
    'Remielle': ['Remielle Dan'], 'Rina': ['Alexandrina Sebastiane (Rina)'], 'Roxy': ['Roxy Ifrita Pryce'],
    'Seed': ['Flora (Seed)'], 'Seth': ['Seth Lowell'], 'Sigrid': ["Sigrid de L'Azur"],
    'Soldier 11': ['Soldier 11 (Harin)'], 'Velina': ['Velina Airgid'], 'Vivian': ['Vivian Banshee'],
    'Yanagi': ['Tsukishiro Yanagi'], 'Yidhari': ['Yidhari Murphy'], 'Yuzuha': ['Ukinami Yuzuha'],
}
ALIASES = {identifier(alias): identifier(name) for name, aliases in _NAMES.items() for alias in aliases}


def agent_id(name):
    key = identifier(name)
    return ALIASES.get(key, key)


def scanner_agent_key(wiki, name):
    """Match a whole upstream name to the scanner's physical owner key."""
    requested = agent_id(name)
    if not requested:
        return ''
    matches = []
    for uid, agent in wiki.get('character', {}).items():
        aliases = (uid, agent.get('code'), agent.get('EN'), agent.get('CHS'))
        if any(isinstance(alias, str) and agent_id(alias) == requested for alias in aliases):
            key = agent.get('code') or (agent.get('EN') or '').replace(' ', '')
            matches.append(key)
    return matches[0] if len(matches) == 1 else ''


def same_agent(wiki, first, second):
    if first == second:
        return True
    key = scanner_agent_key(wiki, first)
    return bool(key and key == scanner_agent_key(wiki, second))


def roster_catalog(z, fields, cfg, wiki, known_owned=()):
    """A stable list for both settings and planning. Unknown identities remain visible."""
    entries = {}
    def entry(name):
        key = agent_id(name)
        return entries.setdefault(key, dict(key=key, name=name, label=name, guides=[], builds=[], owned=False))

    for field in sorted(fields.keys() | cfg.get('overrides', {}).keys()):
        parts = field.split('/')
        if len(parts) != 3 or parts[0] not in ('build', 'guide'):
            continue
        name = parts[1]
        item = entry(name)
        bucket = item['builds' if parts[0] == 'build' else 'guides']
        if name not in bucket:
            bucket.append(name)
        if parts[0] == 'build':
            item.update(name=name, label=name)

    scan_keys = {}
    for uid, agent in wiki.get('character', {}).items():
        name = agent.get('EN')
        if not isinstance(name,str) or not name:
            continue
        item = entry(name)
        item['label'] = agent.get('CHS') or item['name']
        for alias in (uid, name, agent.get('code'), agent.get('CHS')):
            if isinstance(alias,str) and alias:
                scan_keys[identifier(alias)] = item['key']

    from .store import observed_agents
    for raw in [*known_owned, *observed_agents(z)]:
        if isinstance(raw,str) and raw.strip():
            resolved = scan_keys.get(identifier(raw))
            item = entries[resolved] if resolved else entry(raw)
            item['owned'] = True

    future = set(cfg.get('futureAgents', []))
    excluded = set(cfg.get('excludedAgents', []))
    for key in future | excluded:
        if key not in entries:
            entry(key)
    for key, item in entries.items():
        item['future'] = key in future
        item['selected'] = item['future'] or (cfg.get('includeOwned', True) and item['owned'] and key not in excluded)
    return sorted(entries.values(), key=lambda i:(not i['selected'], not i['owned'], i['name'].casefold()))


_MAIN = {identifier(key):value for key,value in STATS.items()}
_MAIN.update(cr='crit_', cd='crit_dmg_', cdmg='crit_dmg_', ap='anomProf', am='anomMas_', er='energyRegen_',
             pen='pen_', elec='electric_dmg_', phys='physical_dmg_')
_MAIN.update({e:e+'_dmg_' for e in ELEMENTS})


_SUB = {
    'cr':'crit_', 'critrate':'crit_', 'cd':'crit_dmg_', 'cdmg':'crit_dmg_', 'critdmg':'crit_dmg_',
    'ap':'anomProf', 'anomalyproficiency':'anomProf', 'anomalyprofiency':'anomProf',
    'pen':'pen', 'flatpen':'pen', 'atk':'atk', 'atk%':'atk_', 'flatatk':'atk',
    'hp':'hp', 'hp%':'hp_', 'flathp':'hp', 'def':'def', 'def%':'def_', 'flatdef':'def',
}
_FOOTNOTES = str.maketrans('', '', '¹²³⁴⁵')


def stat_parts(field, values):
    """Return the stats this role field accepts plus the exact unrecognized terms."""
    stats, unknown = set(), set()
    substats = field == 'subs'
    aliases = _SUB if substats else _MAIN
    for value in values:
        if substats:
            value = re.sub(r'^\s*(?:CRIT|Anomaly)\s*:\s*', '', value, flags=re.I)
        for part in re.split(r'[,><=/|\n]+', value):
            raw = part.strip()
            if not raw:
                continue
            if raw in aliases.values():
                stats.add(raw)
                continue
            token = raw.translate(_FOOTNOTES)
            if substats:
                token = re.sub(r'\([^()]*\)', '', token).casefold().replace(' ', '')
                stat = aliases.get(token) or aliases.get(token.rstrip('%'))
            else:
                stat = aliases.get(identifier(token))
            if stat:
                stats.add(stat)
            else:
                unknown.add(raw)
    return stats, unknown


def parse_main(values):
    stats, unknown = stat_parts('main', values)
    return stats, bool(unknown) or not stats


def parse_substats(values):
    return stat_parts('subs', values)[0]


def compile_roster(catalog, fields, overrides, set_aliases=None):
    aliases = set_aliases or {}
    def set_id(name):
        return identifier(aliases.get(name, name))

    by_set, unresolved, excluded = defaultdict(list), [], defaultdict(list)
    for item in catalog:
        if not item['selected']:
            continue
        sets = defaultdict(list)
        slot_rules = defaultdict(list)
        preferred = set()
        sub_fields = []
        unknown_substats = {}
        all_set_fields_excluded = bool(item['builds'])
        for source, names in [('build',item['builds']), ('guide',item['guides'])]:
            for name in names:
                prefix = f'{source}/{name}/'
                sub_field = prefix+'subs'
                values = effective(fields.get(sub_field), overrides.get(sub_field)) or []
                substats, unknown = stat_parts('subs', values)
                if unknown:
                    unknown_substats[sub_field] = sorted(unknown)
                preferred.update(substats)
                if substats:
                    sub_fields.append(sub_field)
                if source == 'build':
                    for group in ('sets4','sets2'):
                        field = prefix+group
                        accepted = effective(fields.get(field), overrides.get(field)) or []
                        if overrides.get(field, {}).get('mode') != 'only' or accepted:
                            all_set_fields_excluded = False
                        accepted_ids = {set_id(value) for value in accepted}
                        for key in sorted(accepted_ids):
                            sets[key].append(field)
                        for removed in {set_id(v) for v in fields.get(field, [])} - accepted_ids:
                            excluded[removed].append((item['label'],field))
                for slot in '456':
                    field = prefix+'slot'+slot
                    values = effective(fields.get(field),overrides.get(field)) or []
                    stats, uncertain = parse_main(values)
                    if not values and overrides.get(field, {}).get('mode') == 'only':
                        uncertain = False
                    slot_rules[slot].append((field,stats,uncertain))
        if not sets and not all_set_fields_excluded:
            unresolved.append(item['label'])
        for key, set_fields in sets.items():
            by_set[key].append((item['label'],set_fields,slot_rules,preferred,sub_fields,unknown_substats))
    return by_set, unresolved, excluded


def explain(row, by_set, unresolved, excluded):
    fixed = {'1':'hp','2':'atk','3':'def'}.get(row['slot']) == row['main']
    rules = row['ruleUses']
    key = identifier(row['set'])
    for label, field in excluded.get(key, ()):
        rules.append((field, label, '个人规则排除了这套盘'))
    for label, set_fields, slots, preferred, sub_fields, unknown_substats in by_set.get(key, ()):
        matched = [field for field, stats, _ in slots.get(row['slot'], ()) if row['main'] in stats]
        rules.extend((field, label, '角色使用此套装') for field in set_fields)
        for field, stats, uncertain in slots.get(row['slot'], ()):
            outcome = '主属性符合' if row['main'] in stats else ('主属性待核' if uncertain else '主属性不符')
            rules.append((field, label, outcome))
        if fixed or matched:
            evidence = '、'.join([*set_fields,*matched])
            row['recommendations'].append(f'角色留盘 · {label} · {row["main"]} · {evidence}')
            for field, terms in unknown_substats.items():
                note = '角色副词条待核 · '+label+' · '+'、'.join(terms)
                row['review'].append(note)
                rules.append((field, label, note))
            if preferred:
                rolls = sum(row['rolls'].get(stat,0) for stat in preferred)
                row.setdefault('roleRolls',{})[label] = rolls
                row.setdefault('roleHits',{})[label] = len(preferred.intersection(row['rolls']))
                row['explanation'].append(f'{label}适用副词条合计 {rolls} 档')
                rules.extend((field, label, '用于备用盘副词条排序') for field in sub_fields)
        elif any(unknown for _, _, unknown in slots.get(row['slot'], ())):
            row['review'].append('角色主词条待核 · '+label)
    if unresolved:
        row['review'].append('角色套装待核 · '+'、'.join(unresolved))
