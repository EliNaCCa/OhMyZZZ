import csv
import io
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPException
from .maintenance import exclusive
from .store import config, digest, read, save

GUIDE = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vTj2PaPq6Py_1B5fsOPj_Moc-tN_7mut7fICczI6lz1njyEIAInTnfB7lAraX4pYCRGNbaHGlIbFZ90/pub'
MATRIX = 'https://docs.google.com/spreadsheets/d/1ZIZa4zg0zkkPIP2TrHQaMw9XzjJLvO2iOpUAyREDFwY/export?format=csv&gid=133245023'
BUILDS_GID = '1031525415'
ELEMENTS = {'physical','electric','fire','ice','ether','wind','lumiflux'}
STATS = {'ATK':'atk_', 'HP':'hp_', 'DEF':'def_', 'CRIT Rate':'crit_', 'CRIT DMG':'crit_dmg_',
         'Anomaly Proficiency':'anomProf', 'PEN Ratio':'pen_', 'Anomaly Mastery':'anomMas_',
         'Impact':'impact','Energy Regen':'energyRegen_', **{e.title()+' DMG':e+'_dmg_' for e in ELEMENTS}}

SLOT_EXTRA = {'4': ('crit_', 'crit_dmg_', 'anomProf'),
              '5': tuple(e+'_dmg_' for e in ('physical','fire','ice','electric','ether','wind','lumiflux')) + ('pen_',),
              '6': ('impact', 'energyRegen_', 'anomMas_')}
SLOT_MAIN = {'1': ('hp',), '2': ('atk',), '3': ('def',),
             **{slot: ('hp_', 'atk_', 'def_', *extras) for slot, extras in SLOT_EXTRA.items()}}


def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            size = response.headers.get('Content-Length')
            raw = response.read(4_000_001)
    except HTTPException as exc:
        raise ValueError('订阅连接中断 · '+str(exc)) from exc
    if len(raw) > 4_000_000:
        raise ValueError('来源超过大小上限')
    if size is not None and int(size) != len(raw):
        raise ValueError('订阅下载不完整')
    return raw.decode('utf-8-sig')


def matrix_fields(raw, note=None):
    fields, seen_slots, columns = {}, set(), {}
    for number, row in enumerate(csv.reader(io.StringIO(raw), strict=True), 1):
        name = row[0].strip() if row else ''
        if not name:
            continue
        if name in ('Disc 4','Disc 5','Disc 6'):
            slot = name[-1]
            if slot in seen_slots:
                raise ValueError('主词条表重复表头')
            seen_slots.add(slot)
            columns = {}
            headings = row
            for i, cell in enumerate(row[1:], 1):
                if not cell.strip():
                    break
                if cell.strip() not in STATS:
                    raise ValueError('未知主词条表头 '+cell)
                columns[i] = STATS[cell.strip()]
            common = {'hp_', 'def_', 'atk_'}
            required = {'4': common | {'crit_', 'crit_dmg_', 'anomProf'},
                        '5': common | {'pen_', 'physical_dmg_', 'fire_dmg_', 'ice_dmg_', 'electric_dmg_', 'ether_dmg_'},
                        '6': common | {'anomMas_', 'impact', 'energyRegen_'}}[slot]
            actual = set(columns.values())
            optional = {e+'_dmg_' for e in ELEMENTS} if slot == '5' else set()
            if len(columns) != len(actual) or not required <= actual <= required | optional:
                raise ValueError(f'{slot} 号主词条表缺列、重复或错位')
            continue
        if not columns:
            raise ValueError('缺少表头')
        if len(row) <= max(columns):
            raise ValueError(f'{slot} 号主词条表数据行缺列 · {name}')
        key = 'matrix/'+name.replace(' ','')+'/'+slot
        if key in fields:
            raise ValueError('重复套装行 '+name)
        accepted, known = [], []
        if note:
            note(key, number, 0, '套装', row[0])
        for i, stat in columns.items():
            if note:
                note(key, number, i, headings[i], row[i])
            value = row[i].strip()
            if not value:
                continue
            if not value.isdecimal():
                raise ValueError('非数字推荐数 '+name)
            known.append(stat)
            if int(value) > 0:
                accepted.append(stat)
        fields[key] = sorted(accepted)
        fields[key+'/known'] = sorted(known)
    if seen_slots != {'4','5','6'} or not all(any(k.endswith('/'+s) for k in fields) for s in seen_slots):
        raise ValueError('清理表缺失号位')
    return fields


_GUIDE_SLOT = re.compile(r'(.+?)(?<![A-Za-z])(?:Disc|Dics)\s*([456])[¹²³⁴⁵]*(?!\w)', re.I)


def guide_fields(raw, note=None):
    from .guide_sets import UPDATED
    rows = [(n, r+['']*40) for n, r in enumerate(csv.reader(io.StringIO(raw), strict=True), 1)
            if any(cell.strip() for cell in r)]
    result, agent, cols = {}, '', {}
    for i, (number, row) in enumerate(rows):
        if UPDATED.match(row[1].strip()):
            if agent and not cols:
                raise ValueError('角色表缺少词条表头 · '+agent)
            if not i:
                raise ValueError('角色栏为空')
            agent = rows[i-1][1][1].strip()
            cols = {}
            if not agent:
                raise ValueError('角色栏为空')
        if not agent:
            continue
        if 'Main Stats' in row or 'Sub Stats' in row:
            if row.count('Main Stats') != 1 or row.count('Sub Stats') != 1:
                raise ValueError('角色词条表头缺失或重复 · '+agent)
            cols = {'main':row.index('Main Stats'), 'subs':row.index('Sub Stats')}
            continue
        if not cols:
            continue
        field = 'guide/'+agent+'/'
        for m in _GUIDE_SLOT.finditer(row[cols['main']]):
            stat = m[1].strip()
            if stat:
                result.setdefault(field+'slot'+m[2], []).append(stat)
                if note:
                    note(field+'slot'+m[2], number, cols['main'], 'Main Stats', row[cols['main']])
        sub = row[cols['subs']].strip()
        if sub == 'Baseline Stats':
            cols['subs_done'] = True
        elif sub and not cols.get('subs_done'):
            result.setdefault(field+'subs', []).append(sub)
            if note:
                note(field+'subs', number, cols['subs'], 'Sub Stats', row[cols['subs']])
    if agent and not cols:
        raise ValueError('角色表缺少词条表头 · '+agent)
    if not result:
        raise ValueError('角色表结构未知')
    return result


def build_fields(raw, note=None):
    records = csv.reader(io.StringIO(raw), strict=True)
    columns = next(records, [])
    required = {'Name', 'Disc 4', 'Disc 5', 'Disc 6', 'Substats', '4pc_0', 'All 2pc_0'}
    if not required.issubset(columns) or len(columns) != len(set(columns)):
        raise ValueError('Prydwen Builds 的角色、套装或词条表头发生变化')
    groups = {key: [col for col in columns if re.fullmatch(pattern, col)]
              for key, pattern in [('sets4', r'4pc_[0-9]+'), ('sets2', r'All 2pc_[0-9]+')]}
    mapped = {**groups, 'subs':['Substats'], **{'slot'+s:['Disc '+s] for s in '456'}}
    last_required = max(columns.index(col) for col in required.union(*groups.values()))
    result = {}
    for number, row in enumerate(records, 2):
        if not any(row):
            continue
        if not last_required < len(row) <= len(columns):
            raise ValueError(f'Prydwen Builds 第 {number} 行列数不符')
        record = dict(zip(columns, row))
        name = (record['Name'] or '').strip()
        prefix = 'build/' + name + '/'
        if not name or '/' in name or prefix + 'sets4' in result:
            raise ValueError('Prydwen Builds 角色名缺失或重复 · '+name)
        for group, fields in groups.items():
            result[prefix + group] = sorted({s.strip().replace(' ', '') for col in fields
                for s in (record[col] or '').split(',') if s.strip()})
        for slot in '456':
            raw_stats = (record['Disc '+slot] or '').strip()
            result[prefix+'slot'+slot] = [raw_stats] if raw_stats else []
        subs = (record['Substats'] or '').strip()
        result[prefix+'subs'] = [subs] if subs else []
        if note:
            for field, headers in mapped.items():
                for col in headers:
                    note(prefix+field, number, columns.index(col), col, record[col])
    if not result:
        raise ValueError('Prydwen Builds 缺少角色记录')
    return result


def sources():
    from .guide_sets import reference_icons, role_icons, match_icons
    index = fetch(GUIDE+'html')
    catalog = dict(re.findall(r'items[.]push[(][{]name:\s*"([^"]+)"[^}]*?gid:\s*"([0-9]+)"', index))
    tabs = [(name,gid) for name,gid in catalog.items() if name.lower() in ELEMENTS]
    if not {'Physical','Fire','Ice','Electric','Ether','Drive Disc Guide'}.issubset(catalog):
        raise ValueError('攻略表页签变化')
    evidence = {}

    def record_cells(tab, url):
        def note(key, row, column, heading, value):
            group = evidence.setdefault(key, {}).setdefault(tab, {'url':url, 'cells':[]})
            letters = ''
            index = column + 1
            while index:
                index, digit = divmod(index - 1, 26)
                letters = chr(65 + digit) + letters
            cell = [letters+str(row), heading, value]
            if cell not in group['cells']:
                group['cells'].append(cell)
        return note

    sheet = MATRIX.split('/export?')[0]+'/edit#gid='
    fields = matrix_fields(fetch(MATRIX), record_cells('Set+Main', sheet+'133245023'))
    reference_gid = catalog['Drive Disc Guide']
    html_url = GUIDE+'html/sheet?headers=false&gid='
    refs = reference_icons(fetch(html_url+reference_gid), {k.split('/')[1] for k in fields})
    origins = {key:['Set+Main'] for key in fields}
    builds = build_fields(fetch(MATRIX.rsplit('=',1)[0]+'='+BUILDS_GID),
                          record_cells('Prydwen Builds', sheet+BUILDS_GID))
    fields.update(builds)
    origins.update({key:['Prydwen Builds'] for key in builds})
    def role_tab(entry):
        _, gid = entry
        raw = fetch(GUIDE+'?single=true&output=csv&gid='+gid)
        return raw, role_icons(raw, fetch(html_url+gid))

    pictures = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for (tab, gid), (data, icons) in zip(tabs, pool.map(role_tab, tabs), strict=True):
            note = record_cells(tab, GUIDE+'html?gid='+gid+'&single=true')
            for key, value in guide_fields(data, note).items():
                fields[key] = sorted(set(fields.get(key, [])+value))
                origins.setdefault(key, []).append(tab)
            for actor, entries in icons.items():
                pictures.append(('guide/'+actor+'/sets4', tab, note, entries))
    recognized = match_icons(refs, {url for _,_,_,entries in pictures for _,_,url,_ in entries})
    for key, tab, note, entries in pictures:
        active = set(fields.get(key, []))
        for row, col, url, caption in entries:
            names = recognized[url]
            active.update(names)
            note(key, row, col, '四件套图标', ' / '.join(names))
            if caption:
                note(key, caption[0], caption[1], '适用场合', caption[2])
        fields[key] = sorted(active)
        origins.setdefault(key, []).append(tab)
    return fields, dict(tabs, **{'Drive Disc Guide':reference_gid, 'Prydwen Builds':BUILDS_GID, 'Set+Main':'133245023'}), origins, evidence


def effective(remote, override=None):
    if not override or override['mode'] == 'inherit':
        return remote
    local = set(override['values'])
    if override['mode'] == 'add':
        local.update(remote or [])
    elif override['mode'] != 'only':
        raise ValueError('字段覆盖模式无效')
    return sorted(local)


def stat_field(key):
    parts = key.split('/')
    if len(parts) == 3 and parts[0] in ('guide', 'build') and parts[2] in ('subs', 'slot4', 'slot5', 'slot6'):
        return parts[2]
    return None


def accepted_terms(key, values):
    field = stat_field(key)
    if field:
        from .roster import stat_parts
        stats, unknown = stat_parts(field, values or ())
        return stats | unknown
    return set(values or ())


def conflict_view(change, override, key=''):
    if not override or override['mode'] == 'inherit':
        return None
    old, remote = change['old'], change['remote']
    before, after = accepted_terms(key, old), accepted_terms(key, remote)
    local = dict(mode=override['mode'], values=sorted(set(override['values'])))
    values = accepted_terms(key, local['values'])
    # Compare the upstream delta with the player's intent. Preserve only disagreements.
    removed = (before - after) & values
    rejected = (after - before) - values if local['mode'] == 'only' else set()
    disputed = removed | rejected
    old_known, new_known = change.get('oldKnown'), change.get('remoteKnown')
    cell_changes = {}
    if old_known is not None and new_known is not None:
        # A numeric zero is an explicit decision even when the positive list is unchanged.
        old_zero = set(old_known) - before
        new_zero = set(new_known) - after
        disputed |= (new_zero - old_zero) & values

        def state(stat, positive, zero, source):
            if source is None:
                return 'missing'
            if stat in positive:
                return 'recommended'
            return 'zero' if stat in zero else 'blank'

        cell_changes = {stat: [state(stat, before, old_zero, old), state(stat, after, new_zero, remote)]
                        for stat in sorted(disputed)}
    if not disputed:
        return None
    identity = [change.get('event', change['version']), old, remote, local]
    if cell_changes:
        identity.append(cell_changes)
    token = digest(identity)
    if override.get('acknowledged') == token:
        return None
    return dict(change, fieldKey=key, local=local, active=effective(remote, local), token=token,
                cellChanges=cell_changes)


def current_conflicts(snapshot, overrides):
    pending = {}
    for key, change in snapshot.get('conflicts', {}).items():
        conflict = conflict_view(change, overrides.get(key), key)
        if conflict:
            pending[key] = conflict
    return pending


def field_tabs(snapshot, key):
    return snapshot.get('origins', {}).get(key) or snapshot.get('formerOrigins', {}).get(key, [])


def merge(previous, fields, overrides, tabs, origins=None, evidence=None):
    old = previous.get('fields', {})
    origins = origins or {}
    # A deleted field remains editable locally, including after acknowledging a conflict.
    former = {key:known for key in overrides if key not in fields and (known := field_tabs(previous, key))}
    evidence = dict(evidence or {})
    for key in former:
        if key in previous.get('sourceCells', {}):
            evidence[key] = previous['sourceCells'][key]
    rev = digest([fields, tabs, origins, former, evidence])
    pending = current_conflicts(previous, overrides)
    for key, override in overrides.items():
        before, after = old.get(key), fields.get(key)
        # Missing /known on a legacy row means we cannot tell blank from zero.
        old_known = old.get(key+'/known') if before is not None else []
        new_known = fields.get(key+'/known') if after is not None else []
        matrix = key.startswith('matrix/') and old_known is not None and new_known is not None
        if not old or (before == after and (not matrix or old_known == new_known)):
            continue
        # Compare to the start of an outstanding disagreement even after intervening revisions.
        outstanding = pending.pop(key, None)
        baseline = outstanding['old'] if outstanding else before
        change = dict(old=baseline, remote=after, version=rev, event=time.time_ns())
        if matrix:
            change['oldKnown'] = (outstanding or {}).get('oldKnown', old_known)
            change['remoteKnown'] = new_known
            if outstanding and before == after:
                # Keep an open dialog valid when the edit is in an unrelated cell.
                change['event'] = outstanding.get('event', outstanding['version'])
        conflict = conflict_view(change, override, key)
        if conflict:
            pending[key] = conflict
    return dict(fields=fields, version=rev, tabs=tabs, origins=origins, formerOrigins=former, sourceCells=evidence,
                updated=time.time(), conflicts=pending)


def can_merge(conflict):
    return bool(conflict and conflict['local']['mode'] == 'only'
                and accepted_terms(conflict.get('fieldKey', ''), conflict['remote'])
                - accepted_terms(conflict.get('fieldKey', ''), conflict['active']))


def resolve_conflict(root, key, seen, action):
    with exclusive(root/'_rules'):
        cfg = config(root)
        rules = read(root/'rules.json')
        actual = current_conflicts(rules, cfg['overrides']).get(key)
        if not actual or actual['token'] != seen['token']:
            raise ValueError('该字段或本地覆盖已更新，请重新选择')
        if action == 'remote':
            cfg['overrides'].pop(key)
        elif action == 'local':
            cfg['overrides'][key]['acknowledged'] = actual['token']
        elif action == 'union' and can_merge(actual):
            # Retain only the player's own values. Future upstream recommendations can still expire.
            override = dict(mode='add', values=actual['local']['values'])
            conflict = conflict_view(actual, override, key)
            if conflict:
                override['acknowledged'] = conflict['token']
            cfg['overrides'][key] = override
        else:
            raise ValueError('此分歧无法合并')
        # Choice and acknowledgement are committed together. A reload derives the pending list.
        save(root/'config.json', cfg)


def save_field_override(root, key, *, seen_remote, seen_local, override):
    """Apply a field edit against the values that the player actually saw."""
    with exclusive(root/'_rules'):
        cfg = config(root)
        fields = read(root/'rules.json', {}).get('fields', {})
        if fields.get(key) != seen_remote or cfg['overrides'].get(key) != seen_local:
            raise ValueError('编辑期间该字段已更新，请查看最新值后再保存。')
        # Checkbox order is presentational. Keep the acknowledgement on the saved rule.
        if override is None and seen_local is None:
            return False
        if (override and seen_local and override['mode'] == seen_local['mode']
                and set(override['values']) == set(seen_local['values'])):
            return False
        if override is None:
            cfg['overrides'].pop(key, None)
        else:
            cfg['overrides'][key] = override
        save(root/'config.json', cfg)
        return True


def save_config(root, value, expected):
    with exclusive(root/'_rules'):
        if digest(config(root)) != expected:
            raise ValueError('账号配置已更新，请重新打开设置后编辑')
        save(root/'config.json', value)


def save_config_draft(root, base, edited):
    """Save only top-level settings changed in the editor, including deletions."""
    from .validation import validate
    missing = object()
    with exclusive(root/'_rules'):
        current = config(root)
        for key in base.keys() | edited.keys():
            before, after = base.get(key, missing), edited.get(key, missing)
            if before == after:
                continue
            latest = current.get(key, missing)
            if latest != before and latest != after:
                raise ValueError(f'「{key}」已在其他设置中修改。请核对已保存的配置后再编辑。')
            if after is missing:
                current.pop(key, None)
            else:
                current[key] = after
        current.pop('afterEarly', None)
        validate(current)
        save(root/'config.json', current)


def sync(root):
    # Fetch outside the brief write lock. An older download must not replace a newer snapshot.
    start = read(root/'rules.json', {}).get('updated')
    fields, tabs, *metadata = sources()
    with exclusive(root/'_rules'):
        previous = read(root/'rules.json', {})
        if previous.get('updated') != start:
            return previous
        new = merge(previous, fields, config(root)['overrides'], tabs, *metadata)
        save(root/'rules.json', new)
        return new
