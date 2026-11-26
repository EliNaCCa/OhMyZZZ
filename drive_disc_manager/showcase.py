"""Match Enka's public showcase against this account's CN inventory export."""
import http.client
import json
import re
import time
from collections import Counter

from .store import fingerprint


PROPERTIES = {11102:'hp_', 11103:'hp', 12102:'atk_', 12103:'atk', 12202:'impact',
              13102:'def_', 13103:'def', 20103:'crit_', 21103:'crit_dmg_', 23103:'pen_',
              23203:'pen', 30502:'energyRegen_', 31203:'anomProf', 31402:'anomMas_',
              31503:'physical_dmg_', 31603:'fire_dmg_', 31703:'ice_dmg_', 31803:'electric_dmg_',
              31903:'ether_dmg_', 32303:'wind_dmg_'}
# Suit IDs newer than inventory_scan's translation dictionary.
EXTRA_SETS = {33800:'BunnyinWonderland', 33900:'WutheringSalon', 34000:'TheSkyAblaze',
              34100:'FeatheredFate', 34200:'ThornedRose'}


def valid_uid(value):
    return isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{7,11}', value) is not None


def fetch(uid):
    if not valid_uid(uid):
        raise ValueError('请填写游戏内的 UID')
    conn = http.client.HTTPSConnection('enka.network', timeout=8)
    try:
        conn.request('GET', '/api/zzz/uid/' + uid, headers={'Accept':'application/json', 'User-Agent':'OhMyZZZ/0.3 (drive disc showcase)'})
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError(f'展柜读取失败（HTTP {response.status}）')
        data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError('角色展柜数据过大')
        return json.loads(data)
    finally:
        conn.close()


def matched_piece(entry, discs, wiki):
    equipment = entry['Equipment']
    uid = str(equipment['Uid'])
    disc = discs.get(uid)
    if not disc:
        return None
    template = int(equipment['Id'])
    suit = template // 100 * 100
    rank = {2:'B', 3:'A', 4:'S'}.get(template % 100 // 10)
    slot = template % 10
    label = wiki.get('equipment', {}).get(str(suit), {}).get('EN', {})
    label = label.get('name') if isinstance(label, dict) else label
    set_key = EXTRA_SETS.get(suit) or (label or '').replace(' ', '')
    mains = equipment.get('MainPropertyList', equipment.get('MainStatList'))
    subs = equipment['RandomPropertyList']
    if len(mains) != 1 or not isinstance(subs, list):
        return None
    main = PROPERTIES.get(mains[0]['PropertyId'])
    rolls = [(PROPERTIES[s['PropertyId']], s['PropertyLevel']) for s in subs]
    expected = [(s['key'], s['upgrades']) for s in disc['substats']]
    if (entry['Slot'] == slot and disc['slotKey'] == str(slot) and disc['rarity'] == rank and
            disc['level'] == equipment['Level'] and disc['setKey'] == set_key and disc['mainStatKey'] == main
            and sorted(rolls) == sorted(expected)):
        return uid


def visible_avatars(payload, uid):
    if not isinstance(payload, dict) or str(payload.get('uid')) != uid:
        raise ValueError('展柜 UID 与设置不符')
    avatars = payload.get('PlayerInfo', {}).get('ShowcaseDetail', {}).get('AvatarList', [])
    if not isinstance(avatars, list):
        raise ValueError('展柜角色列表有误')
    return [a for a in avatars if isinstance(a, dict) and not a.get('IsHidden')
            and re.fullmatch(r'[1-9][0-9]*', str(a.get('Id')))]


def training(avatars):
    result = {}
    for avatar in avatars:
        weapon = avatar.get('Weapon')
        phase = weapon.get('UpgradeLevel') if isinstance(weapon, dict) and weapon.get('Id') else None
        fields = [('level', avatar.get('Level'), 1, 60), ('mindscape', avatar.get('TalentLevel'), 0, 6),
                  ('engineRank', phase, 1, 5)]
        result[str(avatar['Id'])] = {name: value for name, value, low, high in fields
                                     if type(value) is int and low <= value <= high}
    return result


def match(payload, uid, discs, wiki):
    avatars = visible_avatars(payload, uid)
    by_id = {d['id']: d for d in discs}
    owners, slots = {}, set()
    seen_ids, seen_slots = Counter(), Counter()
    for avatar in avatars:
        actor = str(avatar['Id'])
        for entry in avatar.get('EquippedList', []):
            try:
                piece = str(entry['Equipment']['Uid'])
                pair = actor, int(entry['Slot'])
                seen_ids[piece] += 1
                seen_slots[pair] += 1
                if matched_piece(entry, by_id, wiki):
                    owners[piece] = actor
                    slots.add((piece, pair))
            except (KeyError, TypeError, ValueError, IndexError):
                continue
    return {piece: owners[piece] for piece, pair in slots if seen_ids[piece] == seen_slots[pair] == 1}


def read_showcase(uid, discs, wiki, previous):
    # A changed export must be matched again, even while Enka's cache is warm.
    now = int(time.time())
    same = previous.get('uid') == uid and previous.get('until', 0) > now
    if same:
        signatures = previous.get('signatures', {})
        owners = previous.get('owners', {})
        valid = {d['id']:owners[d['id']] for d in discs if d['id'] in owners and signatures.get(d['id']) == fingerprint(d)}
        return dict(previous, owners=valid)
    result = dict(uid=uid, at=now, until=now+60, owners={})
    try:
        payload = fetch(uid)
        result['training'] = training(visible_avatars(payload, uid))
        result['owners'] = match(payload, uid, discs, wiki)
        result['until'] = now + max(60, int(payload.get('ttl', 60)))
        result['signatures'] = {d['id']:fingerprint(d) for d in discs if d['id'] in result['owners']}
    except (OSError, http.client.HTTPException, KeyError, TypeError, ValueError, AttributeError) as exc:
        result['note'] = str(exc) if isinstance(exc, ValueError) and str(exc).startswith('展柜') else '暂时读不到角色展柜'
    return result


def refresh_training(root, uid, check_account):
    from .store import roster_showcase, save
    previous = roster_showcase(root, {'showcaseUid': uid})
    # Older disc-only caches can have a valid TTL without any character data.
    result = read_showcase(uid, [], {}, previous if 'training' in previous else {})
    check_account()
    if result.get('note'):
        raise ValueError(result['note'])
    cache = {key: result[key] for key in ('uid', 'at', 'until', 'training')}
    save(root/'showcase-agents.json', cache)
    return len(cache['training'])
