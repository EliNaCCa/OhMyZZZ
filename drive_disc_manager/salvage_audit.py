"""Reconcile a native dismantle receipt against the next complete, live scan."""
from collections import Counter, defaultdict
from time import time
from uuid import uuid4

from .store import digest, fingerprint, inventory, read, save


GRADE_FILTERS = {'B': {'B'}, 'A及以下': {'A', 'B'}}
SCOPE_UNAVAILABLE = '宿主未提供实际选盘明细，OhMyZZZ 已停在可逆标记阶段。'


def needs_review(receipt):
    if receipt.get('status') in ('pending', 'native_completed', 'needs_review', 'legacy_unverifiable'):
        return True
    # Older versions closed even ambiguous receipts. Reopen those on upgrade.
    if receipt.get('status') == 'reconciled' and 'resolution' not in receipt:
        states = receipt.get('observations', {})
        return (not states or any(s not in ('已不在库存', '在库且未标弃置') for s in states)
                or any(e.get('beforeMultiplicity') != 1 or
                       (e.get('source'), e.get('after')) == ('内置低品级范围', '在库且未标弃置')
                       for e in receipt.get('entries', []))
                or receipt.get('beforeTotal', 0) - receipt.get('afterTotal', 0) != states.get('已不在库存', 0))
    return False


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
        elif rank in GRADE_FILTERS[grade]:
            # Scanner lock/location fields cannot establish the native selection.
            source = '低品级可能入选'
        else:
            unresolved_grades += int(rank not in ('S','A','B'))
            continue
        entries.append(dict(fp=fp, set=disc['setKey'], slot=disc['slotKey'], main=disc['mainStatKey'],
                            level=disc['level'], source=source, beforeMultiplicity=counts[fp]))
    return dict(batch=uuid4().hex, started=time(), inventory=report['inventory'], rules=report.get('rules'),
                configuration=report['configuration'], selector='全选已弃置', nativeGrade=grade,
                beforeTotal=len(discs), entries=entries, unresolvedGrades=unresolved_grades, status='not_submitted')


def record(root, receipt):
    # Keep each intent even if a later login starts a different batch.
    save(root/'dismantle-result.json', receipt)
    save(root/'dismantle-history'/(receipt['batch']+'.json'), receipt)


def reconcile(root, fresh):
    previous = read(root/'dismantle-result.json', {})
    if not needs_review(previous):
        return
    discs, counts = inventory(fresh)
    previous.update(observedAt=time(), afterInventory=digest(fresh), afterTotal=len(discs))
    if not previous.get('entries'):
        # v0.1.2 only saved a native result. Its scope cannot be inferred.
        previous.setdefault('batch', uuid4().hex)
        previous.update(status='legacy_unverifiable', resolution='旧版未保存逐盘清单，请人工核对')
        record(root, previous)
        return
    by_fp = defaultdict(list)
    for disc in discs:
        by_fp[fingerprint(disc)].append(disc)
    states = Counter()
    for entry in previous['entries']:
        fp = entry['fp']
        if counts[fp] > 1 or entry['beforeMultiplicity'] != 1:
            state = '重复属性待核'
        elif not counts[fp]:
            state = '已不在库存'
        else:
            mark = by_fp[fp][0].get('markState')
            state = {'marked':'仍标弃置','unmarked':'在库且未标弃置'}.get(mark,'标记待核')
            if state == '在库且未标弃置' and entry['source'] == '内置低品级范围':
                state = '仍在低品级范围'
        entry['after'] = state
        states[state] += 1
    previous.setdefault('nativeStatus', previous['status'])
    resolved = (not (set(states) - {'已不在库存', '在库且未标弃置'})
                and previous['beforeTotal'] - len(discs) == states['已不在库存'])
    previous.update(status='reconciled' if resolved else 'needs_review', observations=dict(states),
                    resolution='已核对库存差额和现存标记' if resolved else '确认结果待核，本批不会自动重试')
    record(root, previous)


def close_reviewed(root, batch, after_inventory):
    from .maintenance import exclusive
    with exclusive(root.parent/'_maintenance'):
        receipt = read(root/'dismantle-result.json', {})
        if (not batch or not after_inventory or not needs_review(receipt) or receipt.get('batch') != batch
                or receipt.get('afterInventory') != after_inventory or digest(read(root/'inventory.json')) != after_inventory):
            raise ValueError('请先用本账号的新扫描对账，再核对这批记录')
        record(root, dict(receipt, status='reviewed', reviewedAt=time(), resolution='玩家已核对，按下轮实况重新规划'))
