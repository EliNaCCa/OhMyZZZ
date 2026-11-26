from .reserve_policy import quota, SELECTORS
from .optimizer import STATS
from .store import defaults


def validate(value):
    cfg={**defaults(),**value}
    quota(cfg['reserve'])
    if cfg['inventorySource'] not in ('ocr', 'network', 'cn_export'):
        raise ValueError('请选择库存读取方式')
    from .showcase import valid_uid
    if cfg.get('showcaseUid', '') != '' and not valid_uid(cfg['showcaseUid']):
        raise ValueError('请填写 8 到 12 位游戏 UID，或留空关闭展柜')
    for key in ('dailyLimit', 'weeklyLimit'):
        if type(cfg[key]) is not int or not 1 <= cfg[key] <= 99:
            raise ValueError('运行次数可填 1 到 99')
    for key in ('reserve_rules','profiles','pins','roster'):
        if not isinstance(cfg[key],list): raise ValueError(key+' 应为数组')
    from .roster import agent_id
    for key in ('futureAgents','excludedAgents'):
        values = cfg[key]
        if not isinstance(values,list) or any(not isinstance(v,str) or not v or agent_id(v)!=v for v in values):
            raise ValueError(key+' 应保存角色名单页的角色标识')
    for key in ('protect15','protectNaturalCrit','trimRecommended','auto_sync','run_scanner','applyMarkers','rotateSpareLocks','includeOwned'):
        if type(cfg[key]) is not bool: raise ValueError(key+' 应为布尔值')
    for r in cfg['reserve_rules']:
        quota(r['count'])
        if 'trimRecommended' in r and type(r['trimRecommended']) is not bool:
            raise ValueError('推荐盘参与限额应为布尔值')
        if any(k in r and not isinstance(r[k], (str, int)) for k in SELECTORS):
            raise ValueError('备用规则的套装、号位、主属性无效')
        if 'stats' in r and (not isinstance(r['stats'],list) or any(stat not in STATS for stat in r['stats'])):
            raise ValueError('备用用途包含未知副词条')
    names=set()
    for p in cfg['profiles']:
        if not p['name'] or p['name'] in names or not isinstance(p['request'],dict):
            raise ValueError('配队名重复或参数缺失')
        names.add(p['name'])
        if p.get('needsTarget') and (p.get('enabled') is not False or set(p['request']) != {'characterName'}
                                     or not p['request']['characterName']):
            raise ValueError('请先为这位代理人选择配装目标')
        from .combat_windows import window_requests
        window_requests(p)
    field = cfg['fieldProfile']
    if not isinstance(field,str) or (field and not any(p['name']==field and p.get('enabled',True) for p in cfg['profiles'])):
        raise ValueError('选择一个已启用的出场方案，或清空 fieldProfile 停用换装')
    if field and not cfg['run_scanner']:
        raise ValueError('应用配队需要本次运行先扫描库存')
    for item in cfg['overrides'].values():
        if item['mode'] not in ('inherit','add','only') or not isinstance(item['values'],list) or not all(isinstance(s,str) for s in item['values']):
            raise ValueError('字段覆盖无效')
    return cfg
