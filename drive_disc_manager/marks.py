"""Current game marks win over both batch history and automatic keep suggestions."""

def marker_for(history, fp, uid):
    entry = history.get(fp, {})
    return {} if uid and entry.get('uid') and uid != entry['uid'] else entry


def owns_spare(entry, values, *, uid=None):
    receipt = entry.get('spareLock') if isinstance(entry, dict) else None
    return bool(values and isinstance(receipt, dict) and entry.get('state') == 'locked' and
                (uid is None or entry.get('uid') == uid) and
                receipt.get('values') == values and receipt.get('origin') == 'verified-spare-lock-v1')


def receipts_by_uid(history):
    """One receipt per physical disc, even if the scanner uses a translated set name."""
    result = {}
    for entry in history.values():
        uid = entry.get('uid')
        if uid:
            result[uid] = {} if uid in result else entry
    return result


def scanned_identities(discs, names):
    from collections import Counter
    from .store import equipment_key, equipment_keys, fingerprint
    reverse = equipment_keys(names)
    keys = {raw:equipment_key(raw, names, reverse) for raw in {d['setKey'] for d in discs}}
    identities = Counter(fingerprint(dict(d, setKey=keys[d['setKey']])) for d in discs)
    counts = {fingerprint(d):identities[fingerprint(dict(d, setKey=keys[d['setKey']]))] for d in discs}
    return keys, counts

def mark_action(live_state, previous, wants_trash):
    if live_state == 'marked':
        return 'already-discarded'
    if live_state != 'unmarked':
        return 'unknown'
    if previous in ('marked','already-discarded','manual-keep'):
        return 'manual-keep'
    return 'mark' if wants_trash else 'keep'


def reconcile_scan(root, inventory, names=None):
    from .store import digest, fingerprint, inventory as validate_inventory, read, save, roll_base
    from .stat_reading import checked_values
    import time
    history = read(root/'markers.json', {})
    _, counts = scanned_identities(inventory['discs'], names or {})
    base = roll_base(inventory)
    now = time.time()
    # A later lookalike cannot inherit an absent or ambiguous disc's automatic lock.
    absent = []
    for fp, entry in history.items():
        if 'spareLock' in entry and counts.get(fp) != 1:
            entry.pop('spareLock')
        if fp not in counts and entry.get('state') in ('pending', 'released') and entry.get('values'):
            absent.append(entry)
    if absent:
        # A translated set name can change the fingerprint. Even a different set
        # with the same numeric stats is enough to defer this absence inference.
        discs, _ = validate_inventory(inventory)
        measured = [checked_values(d, base) for d in discs]
        if all(measured):
            signatures = {digest(values) for values in measured}
            for entry in absent:
                if digest(entry['values']) not in signatures:
                    entry.update(previousState=entry['state'], state='absent-after-rescan', observedAt=now)
    for disc in inventory['discs']:
        fp = fingerprint(disc)
        live = disc.get('markState', 'unknown')
        uid = disc.get('exportUid')
        entry = marker_for(history, fp, uid)
        identity = {'uid':uid} if uid else {}
        lock = disc.get('lockState')
        clear_controls = (lock in ('locked','unlocked') and disc.get('lock') is (lock == 'locked')
                          and live in ('marked','unmarked') and disc.get('trash') is (live == 'marked'))
        if 'spareLock' in entry:
            same = owns_spare(entry, checked_values(disc, base))
            if not (same and clear_controls and lock == 'locked' and live == 'unmarked'):
                entry.pop('spareLock')
                if same and clear_controls and live == 'unmarked' and lock == 'unlocked':
                    entry.update(state='manual-unlock', time=now, values=checked_values(disc, base))
        if counts[fp] != 1 or live == 'unknown':
            continue
        previous = entry.get('state')
        if previous in ('pending', 'released'):
            values = checked_values(disc, base)
            if not (values and clear_controls):
                continue
            if entry.get('values') != values:
                # Old pending records lack numeric evidence. Retire to protection only.
                if 'values' in entry or previous == 'released':
                    continue
                recovered = 'manual-unlock'
            elif previous == 'released' or entry.get('intended') in ('release', 'lock'):
                recovered = 'manual-unlock'
            elif entry.get('intended') == 'mark':
                recovered = 'manual-keep'
            else:
                continue
            state = 'already-discarded' if live == 'marked' else recovered
            history[fp] = dict(state=state, time=now, values=values, **identity)
            continue
        action = mark_action(live, previous, False)
        if action in ('already-discarded','manual-keep') and action != previous:
            history[fp] = dict(state=action,time=now, **identity)
        if action == 'manual-keep' and clear_controls and not history[fp].get('values'):
            values = checked_values(disc, base)
            if values:
                history[fp]['values'] = values
    save(root/'markers.json', history)
    return {fp for fp,entry in history.items() if entry['state']=='manual-keep'}


def clear_manual_exception(root, row, inventory_digest, names):
    from .maintenance import exclusive
    from .stat_reading import checked_values
    from .store import digest, fingerprint, read, roll_base, save
    with exclusive(root.parent/'_maintenance'):
        snapshot = read(root/'inventory.json')
        history = read(root/'markers.json', {})
        fp = row['fp']
        entry = marker_for(history, fp, row.get('exportUid'))
        _, counts = scanned_identities(snapshot['discs'], names)
        if digest(snapshot) != inventory_digest or counts.get(fp) != 1:
            raise ValueError('库存已变化或有重复盘，请重新计算')
        disc = next(d for d in snapshot['discs'] if fingerprint(d) == fp)
        values = checked_values(disc, roll_base(snapshot))
        expected = 'manual-unlock' if row.get('manualUnlock') else 'manual-keep' if row.get('manualKeep') else None
        if (not expected or entry.get('state') != expected or not values or values != row.get('observedValues')
                or entry.get('values') != values or disc.get('markState') != 'unmarked' or disc.get('trash') is not False):
            raise ValueError('当前盘与手动保护记录不一致，请重新扫描')
        del history[fp]
        save(root/'markers.json', history)
