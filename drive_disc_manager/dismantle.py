"""Preview the scope of the account's independently enabled native dismantler."""
from pathlib import Path
from one_dragon.utils import os_utils
from .store import config, digest, read, save
from .salvage_audit import native_run, needs_review, prepare_batch
from .task_order import following_native


def handoff(app, report, observed=None, *, batch=None):
    upcoming = following_native(app.ctx, app.account, app.group)
    if upcoming is None:
        if batch is not None:
            raise ValueError('标记期间拆解任务有变化，请核对本轮清单')
        return None
    native, day = upcoming
    if app.ctx.current_instance_idx != app.account or not app.ctx.run_context.is_context_running:
        raise ValueError('账号或运行状态变化，请下次从新扫描继续')
    if digest(config(app.root)) != report['configuration']:
        raise ValueError('拆解前配置已变化，需按新配置重新预览')
    previous = read(app.root/'dismantle-result.json', {})
    if needs_review(previous):
        raise ValueError('请先到「库存记录」核对上次的盘')
    snapshot = read(app.root/'inventory.json')
    receipt = prepare_batch(snapshot, report, observed or {}, native.dismantle_level,
                            include_marked=native.dismantle_abandon, before_marking=observed is None)
    native_state = native_run(Path(os_utils.get_work_dir()), app.account)
    if batch is not None:
        initial = read(app.root/'dismantle-preview.json')
        if (initial['batch'] != batch or initial['nativeAtPreview'] != native_state or initial.get('nativeDay') != day
                or any(initial[k] != receipt[k] for k in ('inventory', 'configuration', 'selector', 'nativeGrade'))):
            raise ValueError('标记期间拆解任务或筛选条件有变化，请重新扫描')
        receipt.update(batch=batch, started=initial['started'])
    receipt.update(account=app.account, nativeAtPreview=native_state, nativeDay=day)
    # Native selection exposes button matches only. Its failure edges can still
    # proceed to confirm. Keep the independent host task out of this invocation.
    save(app.root/'dismantle-preview.json', receipt)
    return receipt['batch']


def note_existing_marks(app, snapshot, scan_started):
    """Record what the next native task can select, even if automatic marking is off."""
    next_task = following_native(app.ctx, app.account, app.group)
    if not next_task or needs_review(read(app.root/'dismantle-result.json', {})):
        return
    native, day = next_task
    state = native_run(Path(os_utils.get_work_dir()), app.account)
    if state and state['updated'] > scan_started:
        return
    if app.ctx.current_instance_idx != app.account or not app.ctx.run_context.is_context_running:
        return
    report = dict(inventory=digest(snapshot), configuration=digest(config(app.root)))
    receipt = prepare_batch(snapshot, report, {}, native.dismantle_level,
                            include_marked=native.dismantle_abandon, existing_only=True)
    receipt.update(account=app.account, nativeDay=day, nativeAtPreview=state)
    save(app.root/'dismantle-preview.json', receipt)
