import json
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QTabWidget,QWidget,QLabel,QPushButton,
    QPlainTextEdit,QTableWidget,QTableWidgetItem,QFileDialog,QInputDialog,QMessageBox,QAbstractItemView,QCheckBox)
from .store import read,save,config,import_inventory
from .service import preview
from .subscriptions import sync
from .manager_app import wiki_path
from .drive_disc_manager_const import APP_ID


def pretty(value):
    return json.dumps(value,ensure_ascii=False,indent=2)


class ManagerDialog(QDialog):
    @classmethod
    def show_flyout(cls,ctx,group_id,target,parent):
        dialog = cls(ctx.get_plugin_data_dir(APP_ID)/str(ctx.current_instance_idx), parent)
        dialog.exec()

    def __init__(self,root,parent=None):
        super().__init__(parent)
        self.root = root
        self.setWindowTitle('驱动盘管理')
        self.resize(960,630)
        layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        tabs = QTabWidget(); layout.addWidget(tabs)
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'仓库预览')
        bar = QHBoxLayout(); box.addLayout(bar)
        for title,callback in [('导入本账号扫描',self.load_inventory),('重新计算',self.calculate),('始终保留 / 取消',self.pin),('同类备用数量',self.set_quota)]:
            self.button(bar,title,callback)
        self.table = self.grid(['套装','号位','主属性','等级','建议','原因']); box.addWidget(self.table)
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'规则订阅')
        bar = QHBoxLayout(); box.addLayout(bar)
        for title,callback in [('更新来源',self.update_rules),('保留本地',lambda:self.resolve('local')),('采用上游',lambda:self.resolve('remote')),('合并',lambda:self.resolve('union')),('编辑字段',self.edit_rule)]:
            self.button(bar,title,callback)
        self.fields = self.grid(['规则字段','远端值','本地覆盖','冲突 · 旧值 → 新值']); box.addWidget(self.fields)
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'配队与配置')
        self.button(box,'从配装器保存为配队',self.import_profile)
        flags = QHBoxLayout(); box.addLayout(flags)
        self.flags = {}
        for key,title in [('run_scanner','运行时重新扫描'),('afterEarly','早场刷后运行'),('applyMarkers','标记后按游戏当前弃置拆解')]:
            toggle = QCheckBox(title); self.flags[key] = toggle; flags.addWidget(toggle)
            toggle.clicked.connect(lambda checked,k=key:self.set_flag(k,checked))
        self.editor = QPlainTextEdit(); box.addWidget(self.editor)
        self.button(box,'保存配置',self.save_text)
        self.reload()
        if self.rules.get('conflicts'):
            tabs.setCurrentIndex(1)

    def grid(self,headers):
        table=QTableWidget(0,len(headers)); table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def button(self,layout,title,fn):
        button=QPushButton(title); layout.addWidget(button)
        def invoke():
            try:
                fn()
            except Exception as exc:
                QMessageBox.warning(self,'驱动盘管理',str(exc))
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
        self.fill(self.table,[(r['set'],r['slot'],r['main'],r['level'],r['decision'],'、'.join(r['reasons']+r['review'])) for r in self.rows])
        conflicts=self.rules.get('conflicts',{}); fields=self.rules.get('fields',{})
        self.keys=sorted((set(fields)|set(self.cfg['overrides']))-{k for k in fields if k.endswith('/known')},key=lambda k:(k not in conflicts,k))
        self.fill(self.fields,[(k,fields.get(k,''),self.cfg['overrides'].get(k,''),conflicts.get(k,'')) for k in self.keys])
        self.status.setText(f"上次预览 {self.report.get('counts',{})}　待处理规则冲突 {len(conflicts)}　"+' / '.join(self.report.get('issues',[])+self.report.get('warnings',[])))

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
            cfg.update(afterEarly=False,applyMarkers=False)
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
        agent_key=next((a['EN'].replace(' ','') for a in wiki.get('character',{}).values()
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

    def set_quota(self):
        index=self.table.currentRow()
        if index<0: return
        r=self.rows[index]
        count,ok=QInputDialog.getInt(self,'同类额外保留','相同套装、号位、主词条的备用数量，-1 为不限',2,-1,9999)
        if ok:
            self.cfg['reserve_rules'].append(dict(set=r['set'],slot=r['slot'],main=r['main'],count=None if count<0 else count)); self.edit_cfg()

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
