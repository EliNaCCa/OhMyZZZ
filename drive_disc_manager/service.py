import json
import os
import time
from contextlib import ExitStack
from csv import Error as CSVError
from pathlib import Path
from .planner import plan
from .maintenance import Deferred, exclusive
from .store import config, digest, equipment_names, known_agents, read, save, snapshot_path, preview_path
from .subscriptions import sync


def rule_snapshot(root, refresh=True):
    warnings = []
    if refresh and config(root)['auto_sync'] and time.time()-read(root/'rules.json',{}).get('updated',0) > 86400:
        try:
            sync(root)
        except (ValueError, OSError, CSVError, Deferred) as exc:
            message = '使用最近成功版本' if read(root/'rules.json', {}).get('fields') else '尚无可用规则快照'
            warnings.append(f'订阅失败，{message} · {exc}')
    # A player may edit local options while the upstream tables are downloading.
    with exclusive(root/'_rules'):
        return config(root), read(root/'rules.json',{}), warnings


def preview(root, wiki=None, refresh=True, *, snapshot=None, reuse_allocations=False, verified_game=False, cn_plan=None, field=None):
    cfg, rules, warnings = snapshot if snapshot is not None else rule_snapshot(root, refresh)
    warnings = list(warnings)
    inputs = dict(cfg, inventorySource='ocr') if verified_game else cfg
    z = read(snapshot_path(root, inputs))
    dictionary = read(wiki) if wiki and Path(wiki).is_file() else {}
    names = equipment_names(dictionary)
    names.update(cfg.get('aliases',{}))
    comparison = None
    game_stamp = None
    if inputs.get('inventorySource') == 'cn_export':
        from .inventory_comparison import compare_game_scan, compare_observations, saved_scan
        game_file = saved_scan(root)
        compare = compare_game_scan if game_file.name == 'inventory.json' else compare_observations
        try:
            with game_file.open(encoding='utf-8-sig') as game:
                game_stamp = os.fstat(game.fileno()).st_mtime_ns
                comparison = compare(z, json.load(game), names)
            comparison['gameStamp'] = game_stamp
        except FileNotFoundError:
            pass  # The export alone is enough to plan a build.
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            warnings.append('已按国服库存计算，上次扫描的穿戴记录不可用')
    history = read(root/'markers.json', {})
    from .optimizer import candidate_pool, NoBuildCandidates
    from .engine import managed
    with ExitStack() as stack:
        endpoint = cfg['optimizer_url']
        def candidates(url, profile, discs, current):
            nonlocal endpoint
            if not discs:
                raise NoBuildCandidates('缺少适用的驱动盘')
            if not endpoint:
                endpoint = stack.enter_context(managed(root))
            return candidate_pool(endpoint, profile, discs, current)
        inspection_cfg = dict(cfg, profiles=[], reserve=0, reserve_rules=[]) if cn_plan is not None else cfg
        report = plan(z,inspection_cfg,rules,names,candidates,
                      wiki=dictionary, known_owned=known_agents(root), history=history, comparison=comparison,
                      previous_preview=read(preview_path(root, inputs), {}) if reuse_allocations else None)
    report['configuration'] = digest(cfg)
    if cn_plan is not None:
        from .inventory_comparison import execution_plan
        from .loadout import fielded_profiles
        fielded = [p['name'] for p in fielded_profiles(cfg, field)] if field else []
        report = execution_plan(cn_plan, report, z, cfg=cfg, names=names, fielded=fielded)
    if game_stamp is not None:
        report['gameStamp'] = game_stamp
        report['scanFile'] = game_file.name
    if comparison is not None:
        report['gameComparison'] = {key: value for key, value in comparison.items() if key != 'lastWorn'}
    report.update(warnings=warnings,generated=time.time(), mode='preview')
    save(preview_path(root, inputs),report)
    return report
