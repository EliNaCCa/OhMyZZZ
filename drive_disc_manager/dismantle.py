"""Preview the scope of the account's independently enabled native dismantler."""
from pathlib import Path
import yaml
from one_dragon.utils import os_utils
from .store import config, digest, read, save
from .salvage_audit import needs_review, prepare_batch, SCOPE_UNAVAILABLE


def configured_dismantler(app):
    home = Path(os_utils.get_work_dir())
    group = home/'config'/f'{app.account:02d}'/'one_dragon'/'_group.yml'
    if not group.is_file():
        return None
    settings = yaml.safe_load(group.read_text('utf-8-sig')) or {}
    if not any(row.get('app_id')=='drive_disc_dismantle' and row.get('enabled') is True
               for row in settings.get('app_list',[])):
        return None
    native = app.ctx.run_context.get_application('drive_disc_dismantle',app.account,'one_dragon')
    if not native.config.dismantle_abandon:
        return None
    return native


def handoff(app, report, observed):
    native = configured_dismantler(app)
    if native is None:
        return '已标记，等待游戏内拆解'
    if app.ctx.current_instance_idx != app.account or not app.ctx.run_context.is_context_running:
        raise ValueError('账号或运行状态变化，请下次从新扫描继续')
    if digest(config(app.root)) != report['configuration']:
        raise ValueError('拆解前配置已变化，需按新配置重新预览')
    previous = read(app.root/'dismantle-result.json', {})
    if needs_review(previous):
        return '已标记，上批确认结果待核。请查看运行设置的拆解记录。'
    receipt = prepare_batch(read(app.root/'inventory.json'),report,observed,native.config.dismantle_level)
    # Native selection exposes button matches only. Its failure edges can still
    # proceed to confirm. Keep the independent host task out of this invocation.
    save(app.root/'dismantle-preview.json', receipt)
    if not receipt['entries'] and not receipt['unresolvedGrades']:
        return '当前无弃置或符合品级条件的盘'
    return SCOPE_UNAVAILABLE
