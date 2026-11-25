import json
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QTabWidget,QWidget,QLabel,QPushButton,
    QPlainTextEdit,QTableWidget,QTableWidgetItem,QFileDialog,QInputDialog,QMessageBox,QAbstractItemView,QCheckBox)
from .store import read,save,config,import_inventory,digest
from .service import preview
from .subscriptions import sync
from .manager_app import wiki_path
from .drive_disc_manager_const import APP_ID, APP_NAME
from .reserve_policy import matching_rules, SELECTORS


def pretty(value):
    return json.dumps(value,ensure_ascii=False,indent=2)


class ManagerDialog(QDialog):
    @classmethod
    def show_flyout(cls,ctx,group_id,target,parent):
        dialog = cls(ctx.get_plugin_data_dir(APP_ID)/str(ctx.current_instance_idx), parent)
        dialog.ctx = ctx
        dialog.exec()

    def __init__(self,root,parent=None):
        super().__init__(parent)
        self.root = root
        self.setWindowTitle(APP_NAME)
        self.resize(960,630)
        layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.tabs = tabs = QTabWidget(); layout.addWidget(tabs)
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'仓库预览')
        bar = QHBoxLayout(); box.addLayout(bar)
        for title,callback in [('导入本账号扫描',self.load_inventory),('重新计算',self.calculate),('始终保留 / 取消',self.pin),('同类备用数量',self.set_quota)]:
            self.button(bar,title,callback)
        self.table = self.grid(['套装','号位','主属性','等级','建议','原因']); box.addWidget(self.table)
        self.reserve_info = QLabel(); self.reserve_info.setWordWrap(True); box.addWidget(self.reserve_info)
        self.table.itemSelectionChanged.connect(self.show_reserve)
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'规则订阅')
        bar = QHBoxLayout(); box.addLayout(bar)
        for title,callback in [('更新来源',self.update_rules),('保留本地',lambda:self.resolve('local')),('采用上游',lambda:self.resolve('remote')),('合并',lambda:self.resolve('union')),('编辑字段',self.edit_rule)]:
            self.button(bar,title,callback)
        self.fields = self.grid(['规则字段','远端值','本地覆盖','冲突 · 旧值 → 新值']); box.addWidget(self.fields)
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'我的配队')
        bar = QHBoxLayout(); box.addLayout(bar)
        self.button(bar,'从配装器保存为配队',self.import_profile)
        self.button(bar,'设置满暴容差',self.edit_tolerance)
        self.button(bar,'启用 / 停用',self.toggle_profile)
        self.button(bar,'设为出场 / 取消',self.set_field_profile)
        self.button(bar,'重新计算',self.calculate)
        self.teams = self.grid(['方案','同时出场组','输出容差','有效暴击','输出差距','选择依据'])
        self.teams.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        box.addWidget(self.teams)
        self.team_score = QLabel(); self.team_score.setWordWrap(True); box.addWidget(self.team_score)
        self.slots = self.grid(['号位','套装','主属性','等级','当前装备者','该盘用途'])
        box.addWidget(self.slots)
        self.equip_status = QLabel(); self.equip_status.setWordWrap(True); box.addWidget(self.equip_status)
        self.teams.itemSelectionChanged.connect(self.show_build)
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'运行设置')
        flags = QHBoxLayout(); box.addLayout(flags)
        self.flags = {}
        for key,title in [('run_scanner','运行时重新扫描'),('afterEarly','早场刷后运行'),('applyMarkers','标记后按游戏当前弃置拆解')]:
            toggle = QCheckBox(title); self.flags[key] = toggle; flags.addWidget(toggle)
            toggle.clicked.connect(lambda checked,k=key:self.set_flag(k,checked))
        self.editor = QPlainTextEdit(); box.addWidget(self.editor)
        self.button(box,'保存配置',self.save_text)
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'版本维护')
        self.maintenance_dir = self.root.parent/'_maintenance'
        self.updates_enabled = QCheckBox('启动一条龙时提示 OhMyZZZ 更新（所有账号）')
        self.updates_enabled.clicked.connect(self.set_updates)
        box.addWidget(self.updates_enabled)
        self.maintenance_status = QLabel(); self.maintenance_status.setWordWrap(True)
        box.addWidget(self.maintenance_status)
        bar = QHBoxLayout(); box.addLayout(bar)
        self.button(bar,'立即检查更新',self.check_release)
        self.button(bar,'退出后回退上一版',lambda:self.queue_maintenance('rollback'))
        self.button(bar,'取消待办',lambda:self.queue_maintenance(''))
        self.button(bar,'刷新状态',self.show_maintenance)
        box.addStretch()
        self.show_maintenance()
        self.reload()
        if self.rules.get('conflicts'):
            tabs.setCurrentIndex(1)

    def grid(self,headers):
        table=QTableWidget(0,len(headers)); table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def set_updates(self, enabled):
        from .update_prompt import set_startup
        set_startup(self.maintenance_dir,enabled)
        self.show_maintenance()

    def check_release(self):
        from PySide6.QtWidgets import QApplication
        from .update_prompt import UpdatePrompt
        app = QApplication.instance()
        prompt = getattr(app,'_ohmyzzz_prompt',None)
        if prompt is None:
            prompt = app._ohmyzzz_prompt = UpdatePrompt(app,self.root.parents[2],getattr(self,'ctx',None))
        elif prompt.ctx is None:
            prompt.ctx = getattr(self,'ctx',None)
        prompt.check(self,manual=True)

    def queue_maintenance(self, action):
        from time import time_ns
        pending = dict(action=action,id=str(time_ns())) if action else {}
        if action=='rollback':
            version=read(self.maintenance_dir/'previous.json',{}).get('version')
            if not version: raise ValueError('首次升级后会显示可回退版本')
            pending['version']=version
        save(self.maintenance_dir/'request.json',pending)
        self.show_maintenance()

    def show_maintenance(self):
        from .drive_disc_manager_const import PLUGIN_VERSION
        directory=self.maintenance_dir
        from .update_prompt import startup_enabled
        self.updates_enabled.setChecked(startup_enabled(directory))
        previous=read(directory/'previous.json',{}).get('version','尚无备份')
        request=read(directory/'request.json',{})
        lines=[f'当前版本 {PLUGIN_VERSION}　｜　可回退版本 {previous}']
        if request.get('action')=='rollback': lines.append('待办：回退到 '+request['version'])
        elif request: lines.append('退出后更新至 '+request.get('version',''))
        last=read(directory/'last-run.json',{})
        if last: lines.append('最近维护：'+str(last.get('message','')))
        self.maintenance_status.setText(chr(10).join(lines))

    def button(self,layout,title,fn):
        button=QPushButton(title); layout.addWidget(button)
        def invoke():
            try:
                fn()
            except Exception as exc:
                QMessageBox.warning(self,APP_NAME,str(exc))
        button.clicked.connect(invoke)

    @staticmethod
    def fill(table,rows):
        table.setRowCount(len(rows))
        for r,values in enumerate(rows):
            for col,value in enumerate(values):
                item=QTableWidgetItem(str(value)); item.setToolTip(str(value)); table.setItem(r,col,item)

    def reload(self):
        self.cfg=config(self.root); self.rules=read(self.root/'rules.json',{}); self.report=read(self.root/'preview.json',{})
        self.editor.setPlainText(pretty(self.cfg)); self.rows=self.report.get('discs',[])
        for key,toggle in self.flags.items(): toggle.setChecked(bool(self.cfg.get(key)))
        self.coverage = {tuple(group[k] for k in SELECTORS):group for group in self.report.get('reserveCoverage',[])}
        def row_info(row):
            notes = dict.fromkeys(row['reasons'] + row['review'] + row.get('explanation',[]) + row.get('recommendations',[]))
            return '、'.join(notes)
        self.fill(self.table,[(r['set'],r['slot'],r['main'],r['level'],r['decision'],row_info(r)) for r in self.rows])
        self.show_reserve()
        conflicts=self.rules.get('conflicts',{}); fields=self.rules.get('fields',{})
        self.keys=sorted((set(fields)|set(self.cfg['overrides']))-{k for k in fields if k.endswith('/known')},key=lambda k:(k not in conflicts,k))
        self.fill(self.fields,[(k,fields.get(k,''),self.cfg['overrides'].get(k,''),conflicts.get(k,'')) for k in self.keys])
        self.status.setText(f"上次预览 {self.report.get('counts',{})}　待处理规则冲突 {len(conflicts)}　"+' / '.join(self.report.get('issues',[])+self.report.get('warnings',[])))
        self.refresh_teams()
        field = self.cfg.get('fieldProfile')
        receipt = read(self.root/'equip-result.json', {})
        parts = [f'下次运行出场方案 · {field}' if field else '只计算配装']
        if receipt.get('status')=='verified':
            parts.append(f"上次已核对 · {receipt['profile']} · {len(receipt['verified'])} 个号位")
        elif receipt.get('status') in ('checking','interrupted'):
            parts.append('上次换装待核对 · '+receipt.get('error','下次从实际穿戴情况继续'))
        self.equip_status.setText('　｜　'.join(parts))

    def refresh_teams(self, selected=None):
        profiles = self.cfg['profiles']
        def pct(value):
            return '—' if value is None else f'{value:g}%'
        def row(p):
            result = self.report.get('assignments',{}).get(p['name'],{})
            reason = result.get('error') or result.get('reason') or '等待计算'
            if not p.get('enabled',True):
                reason = '已停用'; result = {}
            title = ('出场 · ' if p['name']==self.cfg.get('fieldProfile') else '')+p['name']
            return (title,p.get('simultaneous') or '可借盘',pct(p.get('tolerancePct',0.5)),
                    pct(result.get('effectiveCritRate',result.get('critRate'))),pct(result.get('relativeLossPct')),reason)
        self.fill(self.teams,[row(p) for p in profiles])
        self.teams.resizeColumnsToContents()
        if profiles:
            index = next((i for i,p in enumerate(profiles) if p['name']==selected),0)
            self.teams.selectRow(index)
        self.show_build()

    def show_build(self):
        index = self.teams.currentRow()
        self.fill(self.slots,[])
        self.team_score.setText('在配装器保存角色目标后，可导入为独立配队。')
        if index < 0 or index >= len(self.cfg['profiles']): return
        profile = self.cfg['profiles'][index]
        result = self.report.get('assignments',{}).get(profile['name'],{})
        if not profile.get('enabled',True) or not result.get('ids'):
            self.team_score.setText(result.get('error') or '启用并重新计算后可查看六个号位。'); return
        by_fp = {r['fp']:r for r in self.rows}
        rows = [by_fp[fp] for fp in result['ids'] if fp in by_fp]
        self.fill(self.slots,[(r['slot'],r['set'],r['main'],r['level'],r.get('location') or '背包',
                               '、'.join(r['reasons'])) for r in rows])
        self.slots.resizeColumnsToContents()
        details = [profile['name']]
        if 'outputScore' in result:
            details += [f"所选输出 {result['outputScore']:g}",f"候选最高 {result['bestOutput']:g}"]
        scope = result.get('search',{})
        if scope: details.append(f"初选 {scope['candidateBuilds']} 套")
        if self.report.get('configuration') != digest(self.cfg): details.append('配置已修改 · 上次计算结果')
        self.team_score.setText('　｜　'.join(details))

    def edit_tolerance(self):
        index = self.teams.currentRow()
        if index < 0: return
        profile = self.cfg['profiles'][index]
        value, ok = QInputDialog.getDouble(self,profile['name'],'为实战暴击 ≥ 97.6% 最多接受的输出差距（%）',
                                            profile.get('tolerancePct',0.5),0,100,4)
        if ok:
            profile['tolerancePct'] = value
            self.edit_cfg(); self.refresh_teams(profile['name'])

    def toggle_profile(self):
        index = self.teams.currentRow()
        if index < 0: return
        profile = self.cfg['profiles'][index]
        profile['enabled'] = not profile.get('enabled',True)
        if not profile['enabled'] and self.cfg.get('fieldProfile')==profile['name']:
            self.cfg['fieldProfile']=''
        self.edit_cfg(); self.refresh_teams(profile['name'])

    def set_field_profile(self):
        index = self.teams.currentRow()
        if index < 0: return
        profile = self.cfg['profiles'][index]
        name = profile['name']
        if self.cfg.get('fieldProfile') == name:
            self.cfg['fieldProfile'] = ''
        else:
            if not profile.get('enabled',True) or not profile.get('agentKey'):
                raise ValueError('请选择已启用且已关联代理人的方案')
            self.cfg.update(fieldProfile=name, run_scanner=True)
        self.edit_cfg(); self.refresh_teams(name)

    def save_text(self):
        value = json.loads(self.editor.toPlainText())
        if not isinstance(value,dict):
            raise ValueError('需要 JSON 配置对象')
        from .validation import validate
        validate(value)
        save(self.root/'config.json',value); self.reload()

    def set_flag(self,key,checked):
        cfg = json.loads(self.editor.toPlainText())
        cfg[key] = checked
        if checked and key in ('afterEarly','applyMarkers'):
            cfg['run_scanner'] = True
        if key=='run_scanner' and not checked:
            cfg.update(afterEarly=False,applyMarkers=False,fieldProfile='')
        from .validation import validate
        validate(cfg)
        save(self.root/'config.json',cfg); self.reload()

    def load_inventory(self):
        path,_=QFileDialog.getOpenFileName(self,'选择属于当前账号的 ZOD 扫描','','JSON (*.json)')
        if path:
            import_inventory(self.root,path); self.calculate()

    def import_profile(self):
        from .optimizer import saved_targets
        url,ok=QInputDialog.getText(self,'本地配装器','配装器窗口显示的地址',text=self.cfg['optimizer_url'])
        if not ok: return
        choices=saved_targets(url.strip())
        if not choices:
            raise ValueError('请先在配装器保存角色的多角色目标')
        labels=[f"{i+1}. {p['agent']}" for i,p in enumerate(choices)]
        label,ok=QInputDialog.getItem(self,'选择角色','已保存的配装器配置',labels,0,False)
        if not ok: return
        source=choices[labels.index(label)]
        if source['requiresClaimMapping']:
            raise ValueError('此方案要求避让配装器中已占用的盘。导入需核对两份库存中的占用记录。')
        name,ok=QInputDialog.getText(self,'保存配队快照','方案名称',text=source['agent']+' · 常用队')
        if not ok: return
        if not name.strip() or any(p['name']==name.strip() for p in self.cfg['profiles']):
            raise ValueError('请输入新的方案名称')
        simultaneous,ok=QInputDialog.getText(self,'同时出场','同时上场的角色填写相同分组，互斥配队可留空')
        if not ok: return
        wiki=read(wiki_path(),{})
        agent_key=next((a.get('code') or a['EN'].replace(' ','') for a in wiki.get('character',{}).values()
                        if a.get('CHS')==source['agent'] and a.get('EN')),'')
        self.cfg['optimizer_url']=url.strip()
        self.cfg['profiles'].append(dict(name=name.strip(),agentKey=agent_key,request=source['request'],
                                         simultaneous=simultaneous.strip(),tolerancePct=0.5,sourceId=source['sourceId']))
        self.edit_cfg()

    def calculate(self):
        preview(self.root,wiki_path(),refresh=False); self.reload()

    def edit_cfg(self):
        save(self.root/'config.json',self.cfg)
        if (self.root/'inventory.json').is_file():
            self.calculate()
        else:
            self.reload()

    def pin(self):
        index=self.table.currentRow()
        if index < 0: return
        fp=self.rows[index]['fp']; pins=set(self.cfg['pins'])
        self.cfg['pins']=sorted(pins^{fp}); self.edit_cfg()

    def show_reserve(self):
        index = self.table.currentRow()
        self.reserve_info.setText('选择一张盘，可查看同类备用名额和各用途的实际张数。')
        if not 0 <= index < len(self.rows): return
        group = self.coverage.get(tuple(self.rows[index][k] for k in SELECTORS))
        if not group: return
        parts = []
        for use in group['purposes']:
            cap = '不限' if use['count'] is None else str(use['count'])
            parts.append(f"{use['purpose']} {use['covered']}/{cap}")
        self.reserve_info.setText(f"同类可选 {group['eligible']} 张，实际备用 {group['kept']} 张　｜　"+'　'.join(parts))

    def set_quota(self):
        index=self.table.currentRow()
        if index<0: return
        r=self.rows[index]
        selectors = {k:r[k] for k in SELECTORS}
        limits, trim = matching_rules(selectors,self.cfg)
        options = list(dict.fromkeys([*limits,'偏暴击','偏暴伤']))
        purpose, ok = QInputDialog.getItem(self,'同类备用数量','这批备用盘的用途',options,0,False)
        if not ok: return
        current = limits.get(purpose, (0,0,()))[1]
        count,ok=QInputDialog.getInt(self,'同类额外保留','相同套装、号位、主属性，-1 为不限',-1 if current is None else current,-1,9999)
        if not ok: return
        modes = ['推荐盘全部保留','推荐盘也按同类名额筛选']
        mode,ok=QInputDialog.getItem(self,'同类备用数量','此类别的订阅推荐盘',modes,int(trim),False)
        if not ok: return
        stats = limits[purpose][2] if purpose in limits else {'偏暴击':['crit_'],'偏暴伤':['crit_dmg_']}[purpose]
        rule = dict(**selectors, purpose=purpose, count=None if count<0 else count, stats=list(stats),trimRecommended=mode==modes[1])
        def replaced(old):
            return all(str(old.get(k,''))==v for k,v in selectors.items()) and (old.get('purpose') or '通用')==purpose
        self.cfg['reserve_rules'] = [old for old in self.cfg['reserve_rules'] if not replaced(old)] + [rule]
        before = self.coverage.get(tuple(selectors.values()))
        self.edit_cfg()
        match = next((i for i,item in enumerate(self.rows) if item['fp']==r['fp']),None)
        if match is not None: self.table.selectRow(match)
        after = self.coverage.get(tuple(selectors.values()))
        if before and after:
            self.reserve_info.setText(f"调整前 {before['kept']} 张 → 调整后 {after['kept']} 张　｜　"+self.reserve_info.text())

    def update_rules(self):
        sync(self.root,self.cfg); self.reload()

    def edit_rule(self):
        i=self.fields.currentRow()
        if i<0: return
        key=self.keys[i]; old=self.cfg['overrides'].get(key,{'mode':'inherit','values':self.rules.get('fields',{}).get(key,[])})
        mode,ok=QInputDialog.getItem(self,'字段规则','覆盖方式',['inherit','add','only'],['inherit','add','only'].index(old['mode']),False)
        if not ok: return
        values,ok=QInputDialog.getText(self,'字段规则','接受的值，用英文逗号分隔',text=', '.join(old['values']))
        if ok:
            self.cfg['overrides'][key]=dict(mode=mode,values=[v.strip() for v in values.split(',') if v.strip()]); self.edit_cfg()

    def resolve(self,action):
        i=self.fields.currentRow()
        if i<0: return
        key=self.keys[i]; conflict=self.rules.get('conflicts',{}).get(key)
        if not conflict: return
        if action=='remote': self.cfg['overrides'].pop(key,None)
        elif action=='union': self.cfg['overrides'][key]=dict(mode='add',values=sorted(set(conflict['active'] or [])|set(conflict['remote'] or [])))
        latest=read(self.root/'rules.json')
        if latest.get('conflicts',{}).get(key)!=conflict:
            self.reload(); raise ValueError('该字段已有较新修订，请重新选择')
        save(self.root/'config.json',self.cfg)
        latest['conflicts'].pop(key); save(self.root/'rules.json',latest); self.reload()
