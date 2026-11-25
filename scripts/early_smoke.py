"""One fake account, no desktop, scanner, or host application."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager.early_entry import run
from drive_disc_manager.store import read, save
from drive_disc_manager.task_order import account_order, marker_conflict


def smoke():
    with TemporaryDirectory() as temp:
        data = Path(temp)
        args = NS(run=uuid4().hex, date='2026-11-25')
        cfg = data/'1/config.json'
        receipt = cfg.with_name('early-result.json')
        save(cfg, dict(afterEarly=True, run_scanner=True))
        item = NS(app_id='drive_disc_manager', enabled=False)
        calls = []

        def app(app_id, idx, group_id):
            calls.append((app_id, idx, group_id))
            return NS(app_id=app_id, instance_idx=idx, group_id=group_id, finish_reason=NS(value='COMPLETED'))

        ctx = NS(current_instance_idx=1, ready_for_application=True, get_plugin_data_dir=lambda _: data,
                 init=lambda: None, one_dragon_config=NS(instance_run='全部实例', instance_list_in_od=[NS(idx=1)]),
                 app_group_manager=NS(get_one_dragon_group_config=lambda _: NS(app_list=[item])),
                 run_context=NS(run_application=app, last_application_result=NS(status='fixture')))
        assert run(ctx, args, lambda _: None) == run(ctx, args, lambda _: None) == 0
        assert read(receipt)['status'] == 'COMPLETED' and calls == [('drive_disc_manager', 1, 'one_dragon')]
        item.enabled = True
        args.run = uuid4().hex
        assert run(ctx, args, lambda _: None) == 0 and read(receipt)['status'] == 'daily' and len(calls) == 1
        args.run = uuid4().hex
        def closed(_):
            raise ValueError('game closed')
        assert run(ctx, args, closed) == 2 and read(receipt)['reason'] == 'game closed' and len(calls) == 1
        native = NS(app_id='drive_disc_dismantle', enabled=True)
        group = NS(app_list=[native, item])
        ctx.app_group_manager.get_one_dragon_group_config = lambda _: group
        ctx.run_context.get_config = lambda app_id, idx, group_id: NS(dismantle_level='A及以下', dismantle_abandon=True)
        flags = dict(afterEarly=True, applyMarkers=True)
        assert '排在标记前' in account_order(ctx, 1, flags)
        group.app_list.reverse()
        assert '两项本轮均执行' in account_order(ctx, 1, flags)
        item.enabled = False
        assert '等内置拆解下次实际运行' in account_order(ctx, 1, flags)
        ctx.app_group_manager.get_group_list = lambda idx: ['one_dragon']
        assert not marker_conflict(ctx, 1, 'one_dragon')
        group.app_list.append(NS(app_id='disc_cleanup', enabled=True))
        assert '旧驱动盘清理器' in marker_conflict(ctx, 1, 'one_dragon')
        group.app_list.pop()
        cfg_read = NS(dismantle_level='S及以下',dismantle_abandon=True)
        ctx.run_context.get_config = lambda app_id, idx, group_id: cfg_read
        assert '未弃置的 S 级盘' in marker_conflict(ctx, 1, 'one_dragon')
        cfg_read.dismantle_level = '未知品级'
        try:
            marker_conflict(ctx, 1, 'one_dragon')
        except ValueError as exc:
            assert '核对内置拆解' in str(exc)
        else:
            raise AssertionError('Unrecognized native grade accepted')
    print('PASS early account receipt, replay, daily entry, and game exit')


if __name__ == '__main__':
    smoke()
