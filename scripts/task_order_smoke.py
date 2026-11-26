"""One fake account, no desktop, scanner, or host application."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager.store import config, save
from drive_disc_manager.task_order import marker_conflict


def smoke():
    with TemporaryDirectory() as temp:
        root = Path(temp)
        assert config(root)['run_scanner'] and not config(root)['applyMarkers']
        save(root / 'config.json', dict(afterEarly=True, run_scanner=False))
        assert not config(root)['run_scanner'] and 'afterEarly' not in config(root)
        item = NS(app_id='drive_disc_manager', enabled=True)
        ctx = NS(app_group_manager=NS(), run_context=NS())
        native = NS(app_id='drive_disc_dismantle', enabled=True)
        group = NS(app_list=[native, item])
        ctx.app_group_manager.get_one_dragon_group_config = lambda _: group
        ctx.run_context.get_config = lambda app_id, idx, group_id: NS(dismantle_level='A及以下', dismantle_abandon=True)
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
        # Custom groups enable their own rows, but the installed native factory
        # always reads the current account's default-group dismantle filters.
        item.enabled = True
        group.app_list = [item]
        ctx.app_group_manager.get_group_config = lambda idx, gid: NS(app_list=[native])
        actual = {'one_dragon':'S及以下', 'custom':'B'}
        ctx.run_context.get_config = lambda app_id, idx, gid: NS(dismantle_level=actual[gid], dismantle_abandon=True)
        assert '未弃置的 S 级盘' in marker_conflict(ctx, 1, 'custom')
    print('PASS daily defaults, saved preferences and native task order')


if __name__ == '__main__':
    smoke()
