"""Preview the scope of the account's independently enabled native dismantler."""
from pathlib import Path
from one_dragon.utils import os_utils
from .store import config, digest, read, save
from .salvage_audit import native_run, needs_review, prepare_batch, SCOPE_UNAVAILABLE
from .task_order import group_settings


def handoff(app, report, observed):
    _, native = group_settings(app.ctx, app.account, app.group)
    if native is None and app.group != 'one_dragon':
        _, native = group_settings(app.ctx, app.account)
    if native is None:
        return '已标记，等待游戏内拆解'
    if app.ctx.current_instance_idx != app.account or not app.ctx.run_context.is_context_running:
        raise ValueError('账号或运行状态变化，请下次从新扫描继续')
    if digest(config(app.root)) != report['configuration']:
        raise ValueError('拆解前配置已变化，需按新配置重新预览')
    previous = read(app.root/'dismantle-result.json', {})
    if needs_review(previous):
        return '已标记，上批确认结果待核。请查看运行设置的拆解记录。'
    snapshot = read(app.root/'inventory.json')
    receipt = prepare_batch(snapshot,report,observed,native.dismantle_level, include_marked=native.dismantle_abandon)
    from collections import Counter
    from .store import fingerprint
    receipt.update(account=app.account, nativeAtPreview=native_run(Path(os_utils.get_work_dir()), app.account),
                   beforeCounts=dict(Counter(fingerprint(disc) for disc in snapshot['discs'])))
    # Native selection exposes button matches only. Its failure edges can still
    # proceed to confirm. Keep the independent host task out of this invocation.
    save(app.root/'dismantle-preview.json', receipt)
    if not receipt['entries'] and not receipt['unresolvedGrades']:
        return '本轮内置筛选范围为空'
    return SCOPE_UNAVAILABLE
