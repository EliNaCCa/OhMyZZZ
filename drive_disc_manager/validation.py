from .planner import quota
from .store import defaults
from .optimizer import number


def validate(value):
    cfg={**defaults(),**value}
    quota(cfg['reserve'])
    for key in ('reserve_rules','profiles','pins','roster'):
        if not isinstance(cfg[key],list): raise ValueError(key+' 应为数组')
    for key in ('protect15','auto_sync','run_scanner','afterEarly','applyMarkers'):
        if type(cfg[key]) is not bool: raise ValueError(key+' 应为布尔值')
    for r in cfg['reserve_rules']:
        quota(r['count'])
    names=set()
    for p in cfg['profiles']:
        if not p['name'] or p['name'] in names or not isinstance(p['request'],dict):
            raise ValueError('配队名重复或参数缺失')
        names.add(p['name'])
        if not 0<=number(p.get('tolerancePct',0.5))<=100: raise ValueError('配队输出容差无效')
    for item in cfg['overrides'].values():
        if item['mode'] not in ('inherit','add','only') or not isinstance(item['values'],list) or not all(isinstance(s,str) for s in item['values']):
            raise ValueError('字段覆盖无效')
    return cfg
