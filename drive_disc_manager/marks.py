"""Current game marks win over both batch history and automatic keep suggestions."""

def mark_action(live_state, previous, wants_trash):
    if live_state == 'marked':
        return 'already-discarded'
    if live_state != 'unmarked':
        return 'unknown'
    if previous in ('marked','already-discarded','manual-keep'):
        return 'manual-keep'
    return 'mark' if wants_trash else 'keep'


def reconcile_scan(root, inventory):
    from collections import Counter
    from .store import fingerprint, read, save
    import time
    history = read(root/'markers.json', {})
    counts = Counter(fingerprint(d) for d in inventory['discs'])
    now = time.time()
    for disc in inventory['discs']:
        fp = fingerprint(disc)
        live = disc.get('markState', 'unknown')
        if counts[fp] != 1 or live == 'unknown':
            continue
        previous = history.get(fp, {}).get('state')
        if previous == 'pending':
            expected = history[fp].get('intended')
            if expected == 'mark':
                previous = 'marked'
            elif expected == 'lock':
                # A fresh live lock check precedes any subsequent click.
                history[fp] = dict(state='verify-lock',time=now)
        action = mark_action(live, previous, False)
        if action in ('already-discarded','manual-keep') and action != previous:
            history[fp] = dict(state=action,time=now)
        elif history.get(fp,{}).get('state')=='pending' and previous=='marked':
            history[fp] = dict(state=action,time=now)
    save(root/'markers.json', history)
    return {fp for fp,entry in history.items() if entry['state']=='manual-keep'}
