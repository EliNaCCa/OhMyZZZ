"""Optional OneDragonEarly post-run entry. Caller holds the scheduler's session lock."""
import os
import sys
import time
from pathlib import Path


def main():
    home = Path.cwd()
    sys.path[:0] = [str(home/'src'),str(home/'plugins')]
    from one_dragon.utils import os_utils
    os_utils.set_work_dir(str(home))
    from drive_disc_manager.store import config, save
    from drive_disc_manager.drive_disc_manager_const import APP_ID
    from zzz_od.context.zzz_context import ZContext
    from one_dragon.base.operation.application.application_run_context import RunFinishReason
    ctx = ZContext()
    try:
        ctx.init()
        idx = ctx.current_instance_idx
        root = ctx.get_plugin_data_dir(APP_ID)/str(idx)
        cfg = config(root)
        if not cfg.get('afterEarly'):
            return 0
        if not cfg['run_scanner']:
            raise ValueError('早场自动维护需要启用本轮扫描')
        receipt = root/'early-result.json'
        save(receipt, dict(started=time.time(),status='running'))
        result = ctx.run_context.run_application(APP_ID,idx,'one_dragon')
        save(receipt,dict(finished=time.time(),status=result.finish_reason.value))
        return 0 if result.finish_reason==RunFinishReason.COMPLETED else 2
    finally:
        ctx.after_app_shutdown()


if __name__ == '__main__':
    sys.exit(main())
