"""Preview the scope of the account's independently enabled native dismantler."""
from pathlib import Path
from one_dragon.utils import os_utils
from .store import config, digest, read, save
from .salvage_audit import native_run, needs_review, prepare_batch
from .task_order import group_settings


def handoff(app, report, observed):
    _, native = group_settings(app.ctx, app.account, app.group)
    if native is None and app.group != 'one_dragon':
        _, native = group_settings(app.ctx, app.account)
    if native is None:
        return '标记完成'
    if app.ctx.current_instance_idx != app.account or not app.ctx.run_context.is_context_running:
        raise ValueError('账号或运行状态变化，请下次从新扫描继续')
    if digest(config(app.root)) != report['configuration']:
        raise ValueError('拆解前配置已变化，需按新配置重新预览')
    previous = read(app.root/'dismantle-result.json', {})
    if needs_review(previous):
        return '标记完成，请查看上次库存记录。'
    snapshot = read(app.root/'inventory.json')
    receipt = prepare_batch(snapshot,report,observed,native.dismantle_level, include_marked=native.dismantle_abandon)
    receipt.update(account=app.account, nativeAtPreview=native_run(Path(os_utils.get_work_dir()), app.account))
    # Native selection exposes button matches only. Its failure edges can still
    # proceed to confirm. Keep the independent host task out of this invocation.
    save(app.root/'dismantle-preview.json', receipt)
    return '标记完成'
