"""Delegate to the account's enabled native dismantler, with its existing grade policy."""
import time
from pathlib import Path
import yaml
from one_dragon.utils import os_utils
from .store import read, save


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


def handoff(app, report):
    native = configured_dismantler(app)
    if native is None:
        return '已标记，等待游戏内拆解'
    if app.ctx.current_instance_idx != app.account or not app.ctx.run_context.is_context_running:
        raise ValueError('账号或运行状态变化，请下次从新扫描继续')
    path = app.root/'dismantle-result.json'
    receipt = dict(started=time.time(), inventory=report['inventory'], selector='全选已弃置',
                   nativeGrade=native.config.dismantle_level, status='pending')
    save(path,receipt)
    result = native.execute()
    if not result.success:
        raise ValueError('内置拆解未完成，下次按游戏现状重新扫描')
    save(path,dict(receipt,finished=time.time(),status='native_completed',result=result.status))
    return '内置拆解已完成（全选已弃置）'
