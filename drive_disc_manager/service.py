import time
from contextlib import ExitStack
from pathlib import Path
from .planner import plan
from .maintenance import Deferred
from .store import config, digest, equipment_names, read, save
from .subscriptions import sync


def rule_snapshot(root, refresh=True):
    cfg = config(root)
    rules = read(root/'rules.json',{})
    warnings = []
    if refresh and cfg['auto_sync'] and time.time()-rules.get('updated',0) > 86400:
        try:
            rules = sync(root)
        except (ValueError, OSError, Deferred) as exc:
            warnings.append('订阅失败，使用最近成功版本 · '+str(exc))
    return cfg, rules, warnings


def preview(root, wiki=None, refresh=True, *, snapshot=None):
    cfg, rules, warnings = snapshot if snapshot is not None else rule_snapshot(root, refresh)
    z = read(root/'inventory.json')
    dictionary = read(wiki) if wiki and Path(wiki).is_file() else {}
    names = equipment_names(dictionary)
    names.update(cfg.get('aliases',{}))
    manual = [fp for fp, entry in read(root/'markers.json', {}).items() if entry.get('state')=='manual-keep']
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
        report = plan(z,dict(cfg,pins=sorted(set(cfg['pins'])|set(manual))),rules,names,candidates,
                      wiki=dictionary, known_owned=read(root/'owned-agents.json', []))
    report['configuration'] = digest(cfg)
    report.update(warnings=warnings,generated=time.time(), mode='preview')
    save(root/'preview.json',report)
    return report
