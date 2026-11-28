"""Keep each tier of a run when the recovery receipt advances to the next spend."""
from .store import read, save


RECEIPT = 'upgrade-result.json'


def summary(receipt, stamp):
    status = receipt['status']
    level = (receipt.get('after') or {}).get('level')
    if status == 'unchanged':
        level = receipt['before']['level']
    return dict(submittedNs=receipt.get('submittedNs', stamp), disc=receipt['label'],
                before=receipt['before']['level'], target=receipt['target'], status=status, level=level,
                change=receipt.get('rollChange', ''), decision=receipt.get('decision', ''),
                note=receipt.get('note', ''), marked=bool(receipt.get('marked')))


def run_rows(root, receipt):
    stamp = receipt.get('submittedNs') or (root/RECEIPT).stat().st_mtime_ns
    run = receipt.get('runStartedNs') or stamp
    path = root/'upgrade-runs'/f'{run}.json'
    current = summary(receipt, stamp)
    rows = [row for row in read(path, []) if row['submittedNs'] != current['submittedNs']]
    return path, [*rows, current]


def latest_trials(root):
    receipt = read(root/RECEIPT, {})
    return run_rows(root, receipt)[1] if receipt else []


def submit_trial(root, receipt):
    previous = read(root/RECEIPT, {})
    if previous:
        if previous['status'] == 'pending':
            raise ValueError('请先核对上一次强化结果')
        # Archive first. On interruption the old receipt still overlays its archived row.
        path, rows = run_rows(root, previous)
        save(path, rows)
    save(root/RECEIPT, receipt)
