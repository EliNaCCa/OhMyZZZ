"""Stable local acquisition numbers, oldest first. Network UIDs identify physical discs."""
from collections import Counter, defaultdict
import json

from .store import read, save


FILE = 'cn-disc-order.json'
SCAN = 'acquisition-scan.json'


def sync(root, snapshot):
    path = root/FILE
    state = read(path, {})
    source = snapshot['exportFile']
    items = {d['id']:d for d in snapshot['discs']}
    if (state.get('source') != source or (state.get('live') and items.keys().isdisjoint(state['live']))):
        state = dict(source=source, high=0, live={}, removed=[])
    stamp = snapshot['exportWrittenNs']
    if state.get('stamp') == stamp and state.get('exportStamp') == snapshot.get('exportStamp') and state['live'].keys() == items.keys():
        return state
    old = state['live']
    added = items.keys() - old.keys()
    gone = old.keys() - items.keys()
    high = state['high'] + len(added)
    removed = []
    for uid in sorted(gone):
        item = old[uid]
        number = item.get('number')
        rank = None
        if number:
            # Unknown numbers in the same batch can straddle this disc.
            cohort = item['end']
            if all(i.get('number') for i in old.values() if i['end'] == cohort):
                rank = 1 + sum(i['end'] > cohort or (i['end'] == cohort and i['number'] > number) for i in old.values())
        removed.append(dict(uid=uid, **item, lastRank=rank))
    live = {}
    for uid,d in items.items():
        item = old.get(uid) or dict(end=high, size=len(added))
        if uid in added and len(added) == 1:
            item['number'] = high
        live[uid] = dict(item, set=d['setKey'], slot=d['slotKey'], level=d['level'])
    state.update(high=high, stamp=stamp, exportStamp=snapshot.get('exportStamp'), live=live)
    if gone or added:
        state['removed'] = removed
    save(path, state)
    return state


def bind_scan(root, snapshot, names):
    from .inventory_comparison import attribute_key
    from .store import equipment_keys
    record = read(root/SCAN, {})
    if record.get('account') != root.name or record.get('total') != len(snapshot['discs']):
        return
    reverse = equipment_keys(names)
    by_key = defaultdict(list)
    for d in snapshot['discs']:
        by_key[attribute_key(d, 1, names, reverse)].append(d)
    scan_keys = {int(rank):attribute_key(d, 1, names, reverse) for rank,d in record['rows'].items()}
    if Counter(scan_keys.values()) != Counter({k:len(ds) for k,ds in by_key.items()}):
        return
    ranks = {by_key[key][0]['id']:rank for rank,key in scan_keys.items() if len(by_key[key]) == 1}
    remember_ranks(root, snapshot, ranks)


def observed_ranks(run, total):
    from .store import fingerprint
    pages = defaultdict(dict)
    with (run/'rows.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            d = json.loads(line)
            if 'orderedCell' in d:
                page, offset = d['orderedCell']
                pages[page][offset] = d
    rows = {}
    for page,cells in sorted(pages.items()):
        if not rows and page == 0:
            start = 1
        else:
            # Consecutive grid pages overlap. A distinct adjacent pair fixes the new page origin.
            pairs = defaultdict(list)
            for rank in rows:
                if rank+1 in rows:
                    pairs[(fingerprint(rows[rank]),fingerprint(rows[rank+1]))].append(rank)
            offsets = set()
            for off in cells:
                if off+1 in cells:
                    pair = (fingerprint(cells[off]), fingerprint(cells[off+1]))
                    if pair[0] != pair[1] and len(pairs[pair]) == 1:
                        offsets.add(pairs[pair][0] - off)
            if len(offsets) != 1:
                continue
            start = offsets.pop()
        if any(not 1 <= start+off <= total or (start+off in rows and
               fingerprint(rows[start+off]) != fingerprint(d)) for off,d in cells.items()):
            continue
        rows.update({start+off:d for off,d in cells.items()})
    return rows


def finish_scan(root, run, total, rows=None):
    rows = observed_ranks(run, total) if rows is None else rows
    done = set(rows) == set(range(1, total+1))
    metadata = dict(account=root.name, total=total, ordered=len(rows), status='complete' if done else 'partial')
    record = dict(metadata, rows={rank:dict(d, orderNumber=total-rank+1) for rank,d in rows.items()})
    save(run/'ordered.json',record)
    if done:
        save(root/SCAN, record)
        from .cn_export import FILE as CN, same_export
        from .store import config, equipment_names
        from .manager_app import wiki_path
        from pathlib import Path
        cn = read(root/CN, {})
        if cn and Path(cn['exportFile']).is_file() and same_export(Path(cn['exportFile']), Path(cn['exportFile']).stat(), cn):
            bind_scan(root, cn, equipment_names(read(wiki_path(), {})) | config(root).get('aliases', {}))
    return metadata


class PageRanks:
    """Anchor overlapping warehouse pages to unique discs already seen at a known rank."""
    def __init__(self, total):
        self.total = total
        self.pages = defaultdict(dict)

    def see(self, sample, uid):
        if uid is not None and 'orderedCell' in sample:
            page, offset = sample['orderedCell']
            self.pages[page][offset] = uid

    def ranks(self):
        known = {}
        for page, cells in sorted(self.pages.items()):
            starts = {known[uid] - offset for offset, uid in cells.items() if uid in known}
            if page == 0:
                starts.add(1)
            if len(starts) != 1:
                continue
            start = starts.pop()
            candidates = {uid:start+offset for offset,uid in cells.items()}
            if len(candidates) != len(cells) or any(r < 1 or r > self.total for r in candidates.values()):
                continue
            if len(set((known | candidates).values())) != len(known | candidates):
                continue
            known.update(candidates)
        return known


def remember_ranks(root, snapshot, ranks):
    state = sync(root, snapshot)
    live = state['live']
    counts = Counter(i['end'] for i in live.values())
    newer = 0
    starts = {}
    for end in sorted(counts, reverse=True):
        starts[end] = newer
        newer += counts[end]
    proposed = {}
    for uid, rank in ranks.items():
        item = live[uid]
        end = item['end']
        within = rank - starts[end]
        if counts[end] != item['size'] or not 1 <= within <= counts[end]:
            continue
        number = end - within + 1
        if item.get('number', number) != number:
            return  # The observed order conflicts with an existing anchor.
        proposed[uid] = number
    numbers = [proposed.get(uid, item.get('number')) for uid,item in live.items()]
    known = [n for n in numbers if n is not None]
    if len(known) != len(set(known)):
        return
    if any(live[uid].get('number') != n for uid,n in proposed.items()):
        for uid,n in proposed.items():
            live[uid]['number'] = n
        save(root/FILE, state)
