"""Reconcile a native dismantle receipt against the next complete, live scan."""
from collections import Counter, defaultdict
from time import time
from uuid import uuid4

from .store import digest, fingerprint, inventory, read, save


GRADE_FILTERS = {'B': {'B'}, 'A及以下': {'A', 'B'}}


def prepare_batch(z, report, observed, grade):
    discs, counts = inventory(z)
    if report['inventory'] != digest(z) or grade not in GRADE_FILTERS:
        raise ValueError('拆解品级或库存已变化，需重新预览')
    entries = []
    unresolved_grades = 0
    for disc in discs:
        fp = fingerprint(disc)
        mark = observed.get(fp, disc.get('markState')) if counts[fp]==1 else disc.get('markState')
        if mark not in ('marked','unmarked'):
            raise ValueError('存在未读清的弃置标记，已停在可逆标记阶段')
        rank = disc.get('observedGrade')
        if mark == 'marked':
            source = '游戏原有弃置' if disc.get('markState') == 'marked' else '本轮观察到弃置'
        elif rank in GRADE_FILTERS[grade] and not disc.get('location') and not disc.get('lock'):
            source = '内置低品级范围'
        else:
            unresolved_grades += int(rank not in ('S','A','B'))
            continue
        entries.append(dict(fp=fp, set=disc['setKey'], slot=disc['slotKey'], main=disc['mainStatKey'],
                            level=disc['level'], source=source, beforeMultiplicity=counts[fp]))
    return dict(batch=uuid4().hex, started=time(), inventory=report['inventory'], rules=report.get('rules'),
                configuration=report['configuration'], selector='全选已弃置', nativeGrade=grade,
                beforeTotal=len(discs), entries=entries, unresolvedGrades=unresolved_grades, status='pending')


def record(root, receipt):
    # Keep each intent even if a later login starts a different batch.
    save(root/'dismantle-history'/(receipt['batch']+'.json'), receipt)
    save(root/'dismantle-result.json', receipt)


def reconcile(root, fresh):
    previous = read(root/'dismantle-result.json', {})
    if previous.get('status') not in ('pending','native_completed'):
        return
    discs, counts = inventory(fresh)
    if 'entries' not in previous:
        # v0.1.2 only saved a native result. Its scope cannot be inferred.
        previous.update(batch=uuid4().hex, status='legacy_unverifiable', observedAt=time())
        record(root, previous)
        return
    by_fp = defaultdict(list)
    for disc in discs:
        by_fp[fingerprint(disc)].append(disc)
    states = Counter()
    for entry in previous['entries']:
        fp = entry['fp']
        if not counts[fp]:
            state = '已不在库存'
        elif counts[fp] != 1 or entry['beforeMultiplicity'] != 1:
            state = '重复属性待核'
        else:
            mark = by_fp[fp][0].get('markState')
            state = {'marked':'仍标弃置','unmarked':'在库且未标弃置'}.get(mark,'标记待核')
        entry['after'] = state
        states[state] += 1
    previous.update(status='reconciled', nativeStatus=previous['status'], observedAt=time(),
                    afterInventory=digest(fresh), afterTotal=len(discs), observations=dict(states))
    record(root, previous)
