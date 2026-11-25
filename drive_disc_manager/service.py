import time
from pathlib import Path
from .planner import plan
from .store import config, read, save
from .subscriptions import sync


def preview(root, wiki=None, refresh=True):
    cfg = config(root)
    z = read(root/'inventory.json')
    rules = read(root/'rules.json',{})
    warnings = []
    if refresh and cfg['auto_sync'] and time.time()-rules.get('updated',0) > 86400:
        try:
            rules = sync(root,cfg)
        except (ValueError, OSError) as exc:
            warnings.append('订阅失败，使用最近成功版本 · '+str(exc))
    names = {}
    if wiki and Path(wiki).is_file():
        for entry in read(wiki).get('equipment',{}).values():
            if entry.get('EN') and entry.get('CHS'):
                names[entry['EN'].replace(' ','')] = entry['CHS']
    names.update(cfg.get('aliases',{}))
    report = plan(z,cfg,rules,names)
    report.update(warnings=warnings,generated=time.time(), mode='preview')
    save(root/'preview.json',report)
    return report
