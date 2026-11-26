"""Reconcile a native dismantle receipt against the next complete, live scan."""
from collections import Counter, defaultdict
from time import time
from uuid import uuid4

from .stat_reading import checked_values
from .store import digest, fingerprint, inventory, read, roll_base, save


GRADE_FILTERS = {'B': {'B'}, 'A及以下': {'A', 'B'}}


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


def disc_shape(disc):
    # Set names and optional numeric fields can change between scanner dictionaries.
    return digest([str(disc['slotKey']), disc['level'], disc['mainStatKey'], sorted(s['key'] for s in disc['substats'])])


def prepare_batch(z, report, observed, grade, *, include_marked=True, before_marking=False):
    discs, counts = inventory(z)
    if report['inventory'] != digest(z) or grade not in GRADE_FILTERS:
        raise ValueError('拆解品级或库存已变化，需重新预览')
    entries = []
    unresolved_grades = 0
    unread_marks = 0
    planned = set()
    if before_marking and include_marked:
        planned = {row['fp'] for row in report.get('discs', []) if row.get('decision') == '弃置候选'}
    candidates = {}
    for disc in discs:
        fp = fingerprint(disc)
        mark = observed.get(fp, disc.get('markState')) if counts[fp]==1 else disc.get('markState')
        if include_marked and mark not in ('marked','unmarked'):
            if not before_marking:
                raise ValueError('存在未读清的弃置标记，已停在可逆标记阶段')
            unread_marks += 1
        rank = disc.get('observedGrade')
        if include_marked and mark == 'marked':
            source = '游戏原有弃置' if disc.get('markState') == 'marked' else '本轮观察到弃置'
        elif rank in GRADE_FILTERS[grade]:
            # Scanner lock/location fields cannot establish the native selection.
            source = '低品级可能入选'
        else:
            unresolved_grades += int(rank not in ('S','A','B'))
            if fp not in planned or counts[fp] != 1:
                continue
            source = None
        entry = dict(fp=fp, set=disc['setKey'], slot=disc['slotKey'], main=disc['mainStatKey'],
                     level=disc['level'], beforeMultiplicity=counts[fp], shape=disc_shape(disc))
        if source:
            entries.append(dict(entry, source=source))
        else:
            candidates[fp] = entry
    receipt = dict(batch=uuid4().hex, started=time(), inventory=report['inventory'], rules=report.get('rules'),
                configuration=report['configuration'], selector='全选已弃置' if include_marked else '仅按品级', nativeGrade=grade,
                beforeTotal=len(discs), beforeCounts=dict(counts), entries=entries,
                unresolvedGrades=unresolved_grades, status='not_submitted')
    if before_marking:
        receipt.update(markCandidates=candidates, unreadMarks=unread_marks)
    return receipt


def record(root, receipt):
    # Keep each intent even if a later login starts a different batch.
    save(root/'dismantle-result.json', receipt)
    save(root/'dismantle-history'/(receipt['batch']+'.json'), receipt)


def native_run(home, account):
    """Read the native task's persisted per-account record, including headless runs."""
    import math
    import yaml
    path = home/'config'/f'{account:02d}'/'app_run_record'/'drive_disc_dismantle.yml'
    try:
        data = yaml.safe_load(path.read_text('utf-8-sig'))
    except (OSError, UnicodeError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    stamp = data.get('run_time_float')
    state = data.get('run_status')
    if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp <= 0 or type(state) is not int:
        return None
    return dict(account=account, updated=stamp, status=state)


def observe_native(root, account, native, scan_started):
    """Associate a newer run stamp with its batch, even if the host lost the final result."""
    if not native or native['account'] != account or native['status'] not in (0, 1, 2, 3):
        return
    if native['updated'] > scan_started:
        raise ValueError('扫描期间内置拆解记录已变化，请重新扫描后对账')
    before = read(root/'dismantle-preview.json', {})
    last = read(root/'dismantle-result.json', {})
    if (before.get('account') != account or before.get('status') != 'not_submitted'
            or before['batch'] == last.get('batch') or needs_review(last)):
        return
    baseline = before.get('nativeAtPreview') or {}
    if native['updated'] <= max(before['started'], baseline.get('updated', 0)):
        return
    candidates = before.pop('markCandidates', {})
    if candidates:
        for fp, entry in read(root/'markers.json', {}).items():
            if fp not in candidates or entry.get('batch') != before['batch'] or entry.get('inventory') != before['inventory']:
                continue
            if entry.get('state') in ('marked', 'already-discarded'):
                source = '本轮观察到弃置'
            elif entry.get('state') == 'pending' and entry.get('intended') == 'mark':
                source = '弃置标记待核'
            else:
                continue
            before['entries'].append(dict(candidates[fp], source=source))
    if not (before.get('entries') or before.get('unresolvedGrades') or before.get('unreadMarks')):
        return
    record(root, dict(before, status='pending', independentNative=native))


def addition_reason(disc, base, duplicate, shapes):
    from .optimizer import STATS
    if duplicate:
        return '同属性盘数量增加'
    if not shapes or None in shapes:
        return '上批缺少比对信息'
    if disc_shape(disc) in shapes:
        return '与上批候选属性相似'
    if disc['level'] != 0:
        return '本次多出强化盘'
    if (disc.get('observedGrade') != disc['rarity'] or disc['rarity'] not in ('A', 'B', 'S')
            or disc['setKey'].strip() in ('', 'Unknown')):
        return '品级或套装待核'
    subs = disc['substats']
    if any(s['upgrades'] != base for s in subs):
        return '强化档数与零级盘不符'
    if (not subs or (disc['rarity'] == 'S' and len(subs) < 3)
            or any(not STATS.get(s['key'], (None, None))[1] for s in subs) or not checked_values(disc, base)):
        return '词条数值未读全'
    return ''


def stock_changes(root, previous, remaining, counts, by_fp, base):
    """Describe inventory differences without adding any disc to the native batch."""
    missing = remaining - counts
    labels = {}
    if missing:
        preview = read(root/'preview.json', {})
        if preview.get('inventory') == previous['inventory']:
            labels = {e['fp']:e for e in preview.get('discs', []) if e['fp'] in missing}
    candidates = {e['fp']:e for e in previous['entries']}
    changes = []
    for fp, count in missing.items():
        entry = labels.get(fp) or candidates.get(fp, {})
        changes.append(dict(fp=fp, count=count, kind='missing', after='清单内数量减少' if fp in candidates else '清单外少盘',
                            **{k:entry[k] for k in ('set', 'slot', 'main', 'level') if k in entry}))
    shapes = {e.get('shape') for e in previous['entries']}
    additions = counts - remaining
    verified = not missing and all(n >= 0 for n in remaining.values())
    for fp, count in additions.items():
        disc = by_fp[fp][0]
        reason = addition_reason(disc, base, count != 1 or fp in previous['beforeCounts'], shapes)
        if reason:
            verified = False
        changes.append(dict(fp=fp, set=disc['setKey'], slot=disc['slotKey'], main=disc['mainStatKey'],
                            level=disc['level'], count=count, kind='extra', after=reason or '本次多出'))
    return changes, additions.total() if verified else None


def retire_departed_marks(root, receipt):
    path = root / 'markers.json'
    history = read(path, {})
    retired = False
    for disc in receipt['entries']:
        entry = history.get(disc['fp'], {})
        if (disc['after'] == '已不在库存' and entry.get('state') in ('marked', 'already-discarded')
                and (entry.get('time', 0) <= receipt['started'] or entry.get('batch') == receipt['batch'])):
            entry.update(previousState=entry['state'], state='absent-after-rescan',
                         observedAt=receipt['observedAt'], batch=receipt['batch'])
            retired = True
    if retired:
        save(path, history)


def reconcile(root, fresh):
    previous = read(root/'dismantle-result.json', {})
    if not needs_review(previous):
        return
    if previous.get('independentNative') and previous.get('afterInventory'):
        # Keep the first observation after the independent run. A later manual
        # cleanup cannot prove what the earlier native task selected.
        return
    discs, counts = inventory(fresh)
    previous.update(observedAt=time(), afterInventory=digest(fresh), afterTotal=len(discs))
    if not previous.get('entries') and not previous.get('independentNative'):
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
            if state == '在库且未标弃置' and entry['source'] in ('内置低品级范围', '低品级可能入选'):
                state = '仍在低品级范围'
        entry['after'] = state
        states[state] += 1
    previous.setdefault('nativeStatus', previous['status'])
    resolved = (not (set(states) - {'已不在库存', '在库且未标弃置'})
                and previous['beforeTotal'] - len(discs) == states['已不在库存'])
    native = previous.get('independentNative')
    if native:
        expected = previous.get('beforeCounts')
        remaining = Counter(expected or {})
        for entry in previous['entries']:
            if entry['after'] == '已不在库存':
                remaining[entry['fp']] -= 1
        all_gone = bool(previous['entries']) and states['已不在库存'] == len(previous['entries'])
        new_count = None
        if expected and sum(expected.values()) == previous['beforeTotal']:
            previous['stockChanges'], new_count = stock_changes(root, previous, remaining, counts, by_fp, roll_base(fresh))
        # With arrivals, the old batch must be completely absent. For snapshots with
        # no arrivals retain the established success/unchanged-mark reconciliation.
        exact_result = resolved and native['status'] == 1 and new_count == 0
        uncertain_marks = previous.get('unreadMarks') or any(e['source'] == '弃置标记待核' for e in previous['entries'])
        resolved = (not uncertain_marks and previous.get('unresolvedGrades') == 0
                    and (exact_result or (all_gone and new_count is not None)))
        if resolved and new_count:
            previous['newDiscs'] = new_count
    previous.update(status='reconciled' if resolved else 'needs_review', observations=dict(states),
                    resolution='已核对库存差额和现存标记' if resolved else '确认结果待核，本批不会自动重试')
    if native:
        result = {0:'记录已重置，缺少结束结果', 1:'回执成功', 2:'回执失败', 3:'只有开始记录，缺少结束结果'}
        stock = ('本批全部离库，其余盘已对上' if all_gone else '前后库存已对上') if resolved else '库存变化待核'
        if previous.get('newDiscs'):
            stock += f"，另有 {previous['newDiscs']} 张新盘入库"
        previous['resolution'] = '内置任务'+result[native['status']]+' · '+stock
        if resolved:
            retire_departed_marks(root, previous)
    record(root, previous)


def close_reviewed(root, batch, after_inventory):
    from .maintenance import exclusive
    with exclusive(root.parent/'_maintenance'):
        receipt = read(root/'dismantle-result.json', {})
        if (not batch or not after_inventory or not needs_review(receipt) or receipt.get('batch') != batch
                or receipt.get('afterInventory') != after_inventory
                or (not receipt.get('independentNative') and digest(read(root/'inventory.json')) != after_inventory)):
            raise ValueError('请先用本账号的新扫描对账，再核对这批记录')
        record(root, dict(receipt, status='reviewed', reviewedAt=time(), resolution='玩家已核对，按下轮实况重新规划'))
