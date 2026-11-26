import json
from copy import deepcopy
from threading import Thread
from PySide6.QtCore import Signal, Qt
from one_dragon_qt.widgets.base_interface import BaseInterface
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QTabWidget,QWidget,QLabel,QPushButton,
    QPlainTextEdit,QTableWidget,QTableWidgetItem,QFileDialog,QInputDialog,QMessageBox,QAbstractItemView,QCheckBox,QLineEdit,QMenu)
from .store import read,save,config,import_inventory,digest,equipment_names,equipment_keys
from .service import preview
from .subscriptions import sync, current_conflicts, can_merge, effective, field_tabs, resolve_conflict, save_config, save_config_draft, save_field_override
from .manager_app import wiki_path
from .drive_disc_manager_const import APP_ID, APP_NAME, PLUGIN_VERSION
from .reserve_policy import matching_rules, normalized_rule, SELECTORS
from .roster import roster_catalog
from .disc_owner import owner_label


def pretty(value):
    return json.dumps(value,ensure_ascii=False,indent=2)


class ManagerInterface(BaseInterface):
    probe_finished = Signal(object, str)
    equip_finished = Signal(str, str, str)
    rules_finished = Signal(object, str)
    preview_finished = Signal(object, str)

    def __init__(self, ctx, parent=None):
        super().__init__('ohmyzzz_settings', APP_NAME, '', parent)
        self.ctx = ctx
        layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.preview_job = None
        self.scanning_game = False
        self.preview_refresh_accounts = set()
        self.preview_finished.connect(self.finish_preview, Qt.ConnectionType.QueuedConnection)
        self.preview_note = QLabel(); self.preview_note.setWordWrap(True); self.preview_note.hide()
        layout.addWidget(self.preview_note)
        self.tabs = tabs = QTabWidget(); layout.addWidget(tabs)
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'仓库预览')
        bar = QHBoxLayout(); box.addLayout(bar)
        self.scan_button = self.button(bar,'从游戏扫描并预览',self.start_game_scan)
        self.scan_button.setToolTip('读取已打开游戏的驱动盘仓库，保存完整快照并计算预览。本次仅扫描。')
        self.import_button = self.button(bar,'导入本账号扫描',self.load_inventory)
        self.calculate_buttons = [self.button(bar,'重新计算',self.start_preview)]
        bar = QHBoxLayout(); box.addLayout(bar)
        self.pin_button = self.button(bar,'始终保留 / 取消',self.pin)
        self.quota_button = self.button(bar,'同类备用数量',self.set_quota)
        self.probe_button = self.button(bar, '读一张盘', self.start_probe)
        self.detail_button = self.button(bar, '查看盘详情', self.open_disc_detail)
        self.probe_button.setToolTip('进入已打开游戏的驱动仓库，复读当前盘的主副词条和标记。')
        self.probe_note = QLabel(); self.probe_note.setWordWrap(True); box.addWidget(self.probe_note)
        self.probe_finished.connect(self.finish_probe, Qt.ConnectionType.QueuedConnection)
        self.table = self.grid(['套装','号位','主属性','等级','建议','原因'])
        from .inventory_filter import InventoryFilter
        self.inventory_filter = InventoryFilter(self.table)
        box.addWidget(self.inventory_filter); box.addWidget(self.table)
        self.stat_info = QLabel(); self.stat_info.setWordWrap(True); box.addWidget(self.stat_info)
        self.reserve_info = QLabel(); self.reserve_info.setWordWrap(True); box.addWidget(self.reserve_info)
        self.restore_unlock = self.button(box, '取消这张盘的手动解锁保护', self.clear_manual_exception)
        self.restore_unlock.hide()
        self.table.itemSelectionChanged.connect(self.show_reserve)
        self.inventory_filter.changed.connect(self.show_reserve)
        self.table.cellDoubleClicked.connect(lambda *_:self.open_disc_detail())
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'规则订阅')
        bar = QHBoxLayout(); box.addLayout(bar)
        self.auto_sync = QCheckBox('运行时自动更新订阅（间隔 24 小时）')
        self.auto_sync.clicked.connect(lambda checked:self.set_flag('auto_sync',checked))
        self.rule_stamp = QLabel(); self.rule_stamp.setWordWrap(True)
        bar.addWidget(self.auto_sync); bar.addWidget(self.rule_stamp,1)
        bar = QHBoxLayout(); box.addLayout(bar)
        self.rule_search = QLineEdit(); self.rule_search.setPlaceholderText('搜索角色、套装、号位或来源'); self.rule_search.setClearButtonEnabled(True)
        self.only_conflicts = QCheckBox('只看冲突'); self.rule_total = QLabel()
        bar.addWidget(self.rule_search,1); bar.addWidget(self.only_conflicts); bar.addWidget(self.rule_total)
        self.rule_search.textChanged.connect(self.filter_rules); self.only_conflicts.toggled.connect(self.filter_rules)
        bar = QHBoxLayout(); box.addLayout(bar)
        self.rule_updates = set()
        self.sync_button = self.button(bar,'更新来源',self.update_rules)
        self.rules_finished.connect(self.finish_rules, Qt.ConnectionType.QueuedConnection)
        self.rule_actions = {action:self.button(bar, title, lambda a=action:self.resolve(a)) for action, title in
                        [('local','保留本地'), ('remote','采用上游'), ('union','合并')]}
        self.button(bar,'编辑字段',self.edit_rule)
        self.fields = self.grid(['角色 / 套装','字段','来源页签','上游旧值','上游现值','本地规则','当前生效值']); box.addWidget(self.fields)
        self.fields.itemSelectionChanged.connect(self.select_rule)
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'角色留盘')
        self.owned_toggle = QCheckBox('自动纳入本账号扫描到的角色')
        self.owned_toggle.clicked.connect(self.set_include_owned)
        box.addWidget(self.owned_toggle)
        bar = QHBoxLayout(); box.addLayout(bar)
        self.button(bar,'纳入所选角色',lambda:self.select_roster(True))
        self.button(bar,'移出所选角色',lambda:self.select_roster(False))
        self.agent_summary = QLabel(); box.addWidget(self.agent_summary)
        self.agent_table = self.grid(['角色','账号扫描','留盘','订阅角色名','套装来源'])
        self.agent_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        box.addWidget(self.agent_table)
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'我的配队')
        engine_bar = QHBoxLayout(); box.addLayout(engine_bar)
        self.button(engine_bar,'打开配装器',self.open_optimizer)
        self.engine_mode = QLabel(); engine_bar.addWidget(self.engine_mode); engine_bar.addStretch()
        self.button(engine_bar,'连接设置',self.configure_optimizer)
        self.profile_menu = QPushButton('管理方案'); engine_bar.addWidget(self.profile_menu)
        menu = QMenu(self.profile_menu)
        self.profile_menu.setMenu(menu)
        for title, duplicate in [('复制为另一配队', True), ('重命名', False)]:
            action = menu.addAction(title)
            action.triggered.connect(lambda _, copy=duplicate:self.name_profile(copy))
        self.button(engine_bar,'分组与优先级',self.edit_allocation)
        self.apply_button = self.button(engine_bar,'应用当前配队',self.apply_current)
        self.equip_busy = False
        self.equip_finished.connect(self.finish_equip, Qt.ConnectionType.QueuedConnection)
        bar = QHBoxLayout(); box.addLayout(bar)
        self.button(bar,'从配装器保存为配队',self.import_profile)
        self.refresh_target_button = self.button(bar,'更新所选配队',self.refresh_profile)
        self.button(bar,'设置满暴容差',self.edit_tolerance)
        self.button(bar,'输出窗口',self.edit_windows)
        self.button(bar,'启用 / 停用',self.toggle_profile)
        self.button(bar,'设为出场 / 取消',self.set_field_profile)
        self.calculate_buttons.append(self.button(bar,'重新计算',self.start_preview))
        self.teams = self.grid(['方案','同时出场组','输出容差','有效暴击','输出差距','选择依据'])
        self.teams.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        box.addWidget(self.teams)
        self.team_score = QLabel(); self.team_score.setWordWrap(True); box.addWidget(self.team_score)
        self.build_tabs = QTabWidget(); box.addWidget(self.build_tabs)
        self.slots = self.grid(['号位','套装','主属性','等级','当前装备者','该盘用途'])
        self.build_tabs.addTab(self.slots,'方案用盘')
        self.panel_stats = self.grid(['面板属性','目标口径','目标值','当前装备','所选方案','相比当前','距目标（所选−目标）'])
        panel = QWidget(); panel_box = QVBoxLayout(panel)
        self.panel_note = QLabel(); self.panel_note.setWordWrap(True); panel_box.addWidget(self.panel_note)
        panel_box.addWidget(self.panel_stats); self.build_tabs.addTab(panel,'面板对比')
        self.equip_status = QLabel(); self.equip_status.setWordWrap(True); box.addWidget(self.equip_status)
        self.teams.itemSelectionChanged.connect(self.show_build)
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'运行设置')
        flags = QHBoxLayout(); box.addLayout(flags)
        self.flags = {}
        for key,title in [('run_scanner','运行时重新扫描'),('afterEarly','早场刷后运行'),('applyMarkers','锁定保留盘并标记弃置')]:
            toggle = QCheckBox(title); self.flags[key] = toggle; flags.addWidget(toggle)
            toggle.clicked.connect(lambda checked,k=key:self.set_flag(k,checked))
        self.early_info = QLabel(); self.early_info.setWordWrap(True); box.addWidget(self.early_info)
        self.scanner_note = QLabel(); self.scanner_note.setWordWrap(True); box.addWidget(self.scanner_note)
        protections = QHBoxLayout(); box.addLayout(protections)
        for key, title in [('protect15','保留 +15 盘'),('protectNaturalCrit','保留原生四词条双暴盘')]:
            toggle = QCheckBox(title); self.flags[key] = toggle; protections.addWidget(toggle)
            toggle.clicked.connect(lambda checked,k=key:self.set_flag(k,checked))
        rotate = self.flags['rotateSpareLocks'] = QCheckBox('换代时解除插件的旧备用锁')
        rotate.setToolTip('新保留盘锁定后，才可解除本插件为备用名额加的锁。手动锁和旧版来源不明的锁继续保留。')
        rotate.clicked.connect(lambda checked:self.set_flag('rotateSpareLocks', checked))
        box.addWidget(rotate)
        self.salvage_info = QLabel(); self.salvage_info.setWordWrap(True); box.addWidget(self.salvage_info)
        self.button(box,'查看拆解记录',self.show_salvage)
        self.editor = QPlainTextEdit(); box.addWidget(self.editor)
        self.editor_drafts = {}; self.editor_account = None
        bar = QHBoxLayout(); box.addLayout(bar)
        self.button(bar,'保存配置',self.save_text)
        self.button(bar,'放弃草稿，载入已保存配置',self.reset_text)
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'版本维护')
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
        from .reserve_presets import ReservePresets
        self.reserve_presets = ReservePresets()
        tabs.addTab(self.reserve_presets, '备用规则')
        self.reserve_presets.edit.connect(self.edit_preset)
        from .disc_detail_page import DiscDetails
        self.detail = DiscDetails(); self.detail_tab = tabs.addTab(self.detail, '盘详情')
        tabs.setTabVisible(self.detail_tab,False)
        self.detail.back.connect(lambda:tabs.setCurrentIndex(0))
        self.detail.rule.connect(self.open_detail_rule)
        self.detail.team.connect(self.open_detail_team)
        self.detail.edit_policy.connect(self.edit_detail_policy)
        tabs.currentChanged.connect(lambda index:tabs.setTabVisible(self.detail_tab,False) if index!=self.detail_tab else None)

    def on_interface_shown(self):
        # OneDragon caches embedded pages by navigation stack, not by account.
        root = self.ctx.get_plugin_data_dir(APP_ID) / str(self.ctx.current_instance_idx)
        if root != getattr(self, 'root', None):
            self.tabs.setCurrentIndex(0)
        self.root = root
        self.sync_button.setEnabled(root not in self.rule_updates)
        self.show_preview_job()
        self.maintenance_dir = root.parent / '_maintenance'
        self.show_maintenance()
        self.reload()
        if self.preview_job is None and root in self.preview_refresh_accounts:
            self.preview_refresh_accounts.discard(root)
            if self.preview_stale:
                self.start_preview(reuse_allocations=True)
        if self.conflicts:
            self.tabs.setCurrentIndex(1)
            self.rule_search.clear()
            self.only_conflicts.setChecked(True)

    def grid(self,headers):
        table=QTableWidget(0,len(headers)); table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def show_salvage(self):
        from .salvage_audit import close_reviewed, needs_review
        last = read(self.root/'dismantle-result.json', {})
        possible = read(self.root/'dismantle-preview.json', {})
        dialog = QDialog(self); dialog.setWindowTitle('拆解记录'); dialog.resize(760, 420)
        box = QVBoxLayout(dialog)
        tabs = QTabWidget(); box.addWidget(tabs)
        for title, data in [('标记后的范围估算', possible), ('上批拆解对账', last)]:
            page = QWidget(); layout = QVBoxLayout(page); tabs.addTab(page, title)
            if not data:
                layout.addWidget(QLabel('本账号暂无记录')); continue
            text = data.get('resolution') or ('等待本账号新扫描对账' if data is last else '待选范围估算，未提交拆解')
            if data is possible:
                text += f"　·　品级未读清 {data['unresolvedGrades']} 张"
            elif data.get('independentNative'):
                from datetime import datetime
                completed = datetime.fromtimestamp(data['independentNative']['updated']).strftime('%m-%d %H:%M')
                text += f"　·　内置任务 {completed}　·　库存 {data['beforeTotal']} → {data.get('afterTotal', '待核')} 张"
            note = QLabel(text); note.setWordWrap(True); layout.addWidget(note)
            grid = self.grid(['套装','号位','主属性','等级','纳入原因','实物核对']); layout.addWidget(grid)
            self.fill(grid, [(e['set'],e['slot'],e['main'],e['level'],e['source'],
                              e.get('after') or ('重复属性待核' if e['beforeMultiplicity'] != 1 else '—'))
                             for e in data.get('entries', [])])
        if needs_review(last):
            tabs.setCurrentIndex(1)
            if last.get('afterInventory'):
                def acknowledge():
                    close_reviewed(self.root, last['batch'], last['afterInventory'])
                    dialog.accept(); self.reload()
                self.button(box,'已在游戏核对，结束这批记录',acknowledge)
        dialog.exec()

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
        # The host removes the secondary page while hot-reloading the plugin.
        prompt.check(self.window(), manual=True)

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
        return button

    @staticmethod
    def fill(table,rows):
        table.setRowCount(len(rows))
        for r,values in enumerate(rows):
            for col,value in enumerate(values):
                item=QTableWidgetItem(str(value)); item.setToolTip(str(value)); table.setItem(r,col,item)

    def reload(self):
        if self.editor_account is not None:
            text = self.editor.toPlainText()
            if text != pretty(self.editor_base):
                self.editor_drafts[self.editor_account] = (self.editor_base, text)
            else:
                self.editor_drafts.pop(self.editor_account, None)
        self.cfg=config(self.root); self.rules=read(self.root/'rules.json',{}); self.report=read(self.root/'preview.json',{})
        self.config_hash = digest(self.cfg)
        inventory = read(self.root/'inventory.json', {})
        wiki = read(wiki_path(), {})
        from .roster import preview_bindings
        self.preview_bindings = preview_bindings(self.cfg['profiles'], wiki)
        changed_inputs = []
        if self.report:
            if self.report.get('managerVersion') != PLUGIN_VERSION:
                changed_inputs.append('插件版本')
            if self.report.get('configuration') != self.config_hash:
                changed_inputs.append('配置')
            if self.report.get('rules') != self.rules.get('version'):
                changed_inputs.append('订阅规则')
            if not inventory or self.report.get('inventory') != digest(inventory):
                changed_inputs.append('库存')
            if self.report.get('previewBindings', {}) != self.preview_bindings:
                changed_inputs.append('配队角色对应关系')
        self.preview_stale = bool(changed_inputs)
        self.editor_account = self.root
        self.editor_base, text = self.editor_drafts.get(self.root, (deepcopy(self.cfg), pretty(self.cfg)))
        self.show_probe(read(self.root/'single-disc.json', {}))
        if text != self.editor.toPlainText(): self.editor.setPlainText(text)
        self.rows=self.report.get('discs',[])
        for key,toggle in self.flags.items(): toggle.setChecked(bool(self.cfg.get(key)))
        self.auto_sync.setChecked(self.cfg['auto_sync'])
        from datetime import datetime
        updated = self.rules.get('updated')
        stamp = datetime.fromtimestamp(updated).strftime('%Y-%m-%d %H:%M') if updated else '暂无记录'
        revision = self.rules.get('version', '')
        self.rule_stamp.setText('最近成功同步 · '+stamp+(f' · {revision[:8]}' if revision else ''))
        self.rule_stamp.setToolTip(revision)
        self.coverage = {tuple(group[k] for k in SELECTORS):group for group in self.report.get('reserveCoverage',[])}
        conflicts=self.conflicts=current_conflicts(self.rules,self.cfg['overrides']); fields=self.rules.get('fields',{})
        self.keys=sorted((set(fields)|set(self.cfg['overrides']))-{k for k in fields if k.endswith('/known')},key=lambda k:(k not in conflicts,k))
        self.refresh_roster(wiki, inventory)
        self.set_names = equipment_names(wiki) | self.cfg.get('aliases', {})
        self.inventory_filter.present(self.rows, self.set_names, self.root, self.fill)
        self.reserve_presets.present(self.cfg, self.set_names, fields, self.root)
        self.agent_names = {name:a['label'] for a in self.agents for name in [*a['builds'],*a['guides']]}
        self.rule_rows = {key:self.rule_row(key) for key in self.keys}
        if not conflicts: self.only_conflicts.setChecked(False)
        self.filter_rules()
        summary = f"上次预览 {self.report.get('counts',{})}" if self.report else '尚无完整库存预览'
        if self.preview_stale:
            summary += ' · '+'、'.join(changed_inputs)+'已更新，请重新计算'
        self.status.setText(f"{summary}　待处理规则冲突 {len(conflicts)}　"+' / '.join(self.report.get('issues',[])+self.report.get('warnings',[])))
        self.refresh_teams()
        self.engine_mode.setText('手动连接 · '+self.cfg['optimizer_url'] if self.cfg['optimizer_url'] else '由 OhMyZZZ 按需启动')
        from .salvage_audit import needs_review
        salvage = read(self.root/'dismantle-result.json', {})
        from .task_order import account_order
        try:
            note = account_order(self.ctx, int(self.root.name), self.cfg)
        except Exception as exc:
            # Host registration/configuration can be incomplete during a plugin refresh.
            note = '本账号日常任务顺序暂未读到 · ' + str(exc)
        if needs_review(salvage): note += '　上批确认结果待核。'
        self.salvage_info.setText(note)
        from .scanner_status import scanner_status
        try:
            _, scanner_note = scanner_status(self.ctx, int(self.root.name))
        except Exception as exc:
            scanner_note = '宿主设置暂未读到 · '+str(exc)
        self.scanner_note.setText('仓库扫描器 · '+scanner_note)
        early = read(self.root/'early-result.json', {})
        labels = {'COMPLETED':'完成', 'daily':'由日常列表管理', 'running':'开始后尚无完成回执',
                  'FAILED':'失败', 'STOPPED':'已停止', 'NOT_STARTED':'未启动'}
        status = early.get('status', '')
        parts = [early.get('date'), labels.get(status, status), early.get('reason')]
        self.early_info.setText('最近早场 · ' + (' · '.join(filter(None, parts)) or '暂无记录'))
        field = self.cfg.get('fieldProfile')
        receipt = read(self.root/'equip-result.json', {})
        parts = [f'下次运行出场方案 · {field}' if field else '只计算配装']
        if receipt.get('status')=='verified':
            parts.append(f"上次已核对 · {receipt['profile']} · {len(receipt['verified'])} 个号位")
        elif receipt.get('status') in ('checking','interrupted'):
            parts.append('上次换装待核对 · '+receipt.get('error','下次从实际穿戴情况继续'))
        self.equip_status.setText('　｜　'.join(parts))
        self.refresh_open_detail()

    def refresh_roster(self, wiki, inventory):
        self.owned_toggle.setChecked(self.cfg['includeOwned'])
        fields = self.rules.get('fields', {})
        self.agents = roster_catalog(inventory, fields, self.cfg, wiki,
                                     read(self.root/'owned-agents.json', []))
        def display(agent):
            selected = '手动纳入' if agent['future'] else ('自动纳入' if agent['selected'] else '未纳入')
            return (agent['label'], '已拥有' if agent['owned'] else '未扫描到', selected,
                    ' / '.join(dict.fromkeys(agent['builds']+agent['guides'])),
                    'Prydwen Builds' if agent['builds'] else '套装待核')
        self.fill(self.agent_table,[display(agent) for agent in self.agents])
        count = sum(a['selected'] for a in self.agents)
        owned = sum(a['owned'] for a in self.agents)
        self.agent_summary.setText(f'已纳入 {count} 名，扫描识别 {owned} 名。可多选准备培养的角色。')

    def set_include_owned(self, enabled):
        try:
            self.edit_cfg(includeOwned=enabled)
        except Exception as exc:
            QMessageBox.warning(self,APP_NAME,str(exc))

    def select_roster(self, include):
        selected = self.agent_table.selectionModel().selectedRows()
        if not selected:
            return
        future = set(self.cfg['futureAgents'])
        excluded = set(self.cfg['excludedAgents'])
        for index in selected:
            agent = self.agents[index.row()]
            key = agent['key']
            if include:
                excluded.discard(key)
                if not (agent['owned'] and self.cfg['includeOwned']):
                    future.add(key)
            else:
                future.discard(key)
                excluded.add(key)
        self.edit_cfg(futureAgents=sorted(future), excludedAgents=sorted(excluded))

    def refresh_teams(self, selected=None):
        profiles = self.cfg['profiles']
        def pct(value):
            return '—' if value is None else f'{value:g}%'
        from .allocation import group_indices
        def row(index, p):
            result = self.report.get('assignments',{}).get(p['name'],{})
            reason = result.get('error') or result.get('reason') or '等待计算'
            if not p.get('enabled',True):
                reason = '已停用'; result = {}
            title = ('出场 · ' if p['name']==self.cfg.get('fieldProfile') else '')+p['name']
            group = p.get('simultaneous')
            label = f"{group} · 第 {group_indices(profiles, index).index(index)+1} 位" if group else '可借盘'
            role = (p.get('request') or {}).get('roleSystem','').upper()
            tolerance = pct(p.get('tolerancePct',0.5)) if role in ('ATTACK','STRONG','RUPTURE') else '—'
            return (title,label,tolerance,
                    pct(result.get('effectiveCritRate',result.get('critRate'))),pct(result.get('relativeLossPct')),reason)
        self.fill(self.teams,[row(i,p) for i,p in enumerate(profiles)])
        self.teams.resizeColumnsToContents()
        if profiles:
            index = next((i for i,p in enumerate(profiles) if p['name']==selected),0)
            self.teams.selectRow(index)
        self.show_build()

    def show_build(self):
        index = self.teams.currentRow()
        profiles = self.cfg['profiles']
        selected = profiles[index] if 0 <= index < len(profiles) else {}
        self.profile_menu.setEnabled(bool(selected) and not self.equip_busy)
        self.refresh_target_button.setEnabled(bool(selected.get('sourceId')) and not self.equip_busy)
        ready = not self.preview_stale and selected.get('enabled',True) and self.report.get('assignments',{}).get(selected.get('name'),{}).get('ids')
        from .loadout import fielded_profiles
        lineup = fielded_profiles(self.cfg, selected['name']) if selected and selected.get('enabled', True) else []
        unbound = [p['name'] for p in lineup if not p.get('agentKey') or p['agentKey'] != self.preview_bindings.get(p['name'])]
        self.apply_button.setEnabled(bool(ready) and not unbound and not self.equip_busy and self.preview_job is None)
        binding_note = '请更新配队的角色绑定 · ' + '、'.join(unbound) if unbound else ''
        self.apply_button.setToolTip(binding_note or ('查看本次要穿戴的盘' if ready else '用当前配置和完整库存计算所选配队后可用'))
        self.fill(self.slots,[])
        self.fill(self.panel_stats,[])
        self.panel_note.setText('选择已计算的支援、击破、异常或防护等职业方案，可查看目标与装备差值。')
        self.team_score.setText('在配装器保存角色目标后，可导入为独立配队。')
        if index < 0 or index >= len(self.cfg['profiles']): return
        profile = self.cfg['profiles'][index]
        result = self.report.get('assignments',{}).get(profile['name'],{})
        if not profile.get('enabled',True) or not result.get('ids'):
            self.team_score.setText(result.get('error') or '启用并重新计算后可查看六个号位。'); return
        by_fp = {r['fp']:r for r in self.rows}
        rows = [by_fp[fp] for fp in result['ids'] if fp in by_fp]
        self.fill(self.slots,[(r['slot'],r['set'],r['main'],r['level'],owner_label(r),
                               '、'.join(r['reasons'])) for r in rows])
        self.slots.resizeColumnsToContents()
        details = [profile['name']]
        if binding_note:
            details.append(binding_note)
        if result.get('panelRows'):
            def value(v, signed=False):
                if v is None: return '—'
                return f'{v:+g}' if signed else f'{v:g}'
            self.fill(self.panel_stats, [(label, kind, value(goal), value(previous), value(chosen), value(delta,True), value(gap,True))
                                        for label,kind,goal,previous,chosen,delta,gap in result['panelRows']])
            self.panel_stats.resizeColumnsToContents()
            self.panel_note.setText('按角色面板比较。正数表示所选值更高，是否符合目标请看「目标口径」。')
            details.append(result.get('panelMode','职业面板'))
            if not result.get('currentPanel'):
                details.append('当前穿戴六盘未核实')
        if 'outputScore' in result:
            details += [f"所选输出 {result.get('rawOutput', result['outputScore'])}", f"候选最高 {result.get('rawBestOutput', result['bestOutput'])}"]
            if profile.get('simultaneous') and 'rawAvailableBestOutput' in result:
                details.append(f"扣除前序角色用盘后最高 {result['rawAvailableBestOutput']} · 所选与其相差 {result['availableLossPct']:.6g}%")
            from .optimizer import number
            worn = result.get('wornOutput')
            if worn is not None and number(worn) > 0:
                change = (number(result.get('rawOutput',result['outputScore'])) / number(worn) - 1) * 100
                details.append(f'当前六盘输出 {worn} · 换装后 {float(change):+.6g}%')
            else:
                details.append('当前六盘输出待核')
            if result.get('metric'):
                details.append(self.crit_summary(result))
                details.append(f"实战{result['metric']} {result['metricValue']:g} · 暴伤 {result['critDamage']:g}% · 增伤 {result['damageBonus']:g}%")
                if result['metric'] == '贯穿力':
                    details.append(f"贯穿伤害加成 {result['sheerBonus']:g}%")
        for window in result.get('windows', []):
            principal = '（主）' if window['name']==result['mainWindow'] else ''
            details.append(f"{window['name']}{principal} · 占比 {window['share']:.1%} · {self.crit_summary(window)} · 输出 {window.get('rawOutput', window['outputScore'])}")
            if window.get('metric'):
                details.append(f"{window['metric']} {window['metricValue']:g} · 暴伤 {window['critDamage']:g}% · 增伤 {window['damageBonus']:g}%")
        scope = result.get('search',{})
        if scope:
            details.append(f"初选 {scope['candidateBuilds']} 套")
            initial = scope.get('initialScope')
            if initial:
                details.append(f"初选送入 {initial['submitted']} 张盘，配装器 {initial['calls']} 次请求共评估 {initial['evaluated']:,} 套次")
                if initial['atReturnLimit']:
                    details.append('已触及单次候选返回上限')
            if profile.get('simultaneous') and 'availableBuilds' in scope:
                details.append(f"扣除前序占用后可选 {scope['availableBuilds']} 套")
            if scope.get('limited'):
                details.append('已达到联合搜索上限')
        if self.preview_stale:
            details.append('上次计算结果 · 请用当前库存和规则重新计算')
        self.team_score.setText('　｜　'.join(details))

    @staticmethod
    def crit_summary(result):
        effective = f"有效暴击 {result['critRate']:g}%"
        if result.get('overflowCritRate', 0) > 0:
            return f"加成后暴击 {result['uncappedCritRate']:g}% · {effective}（溢出 {result['overflowCritRate']:g} 个百分点）"
        return effective

    def edit_allocation(self):
        from .allocation import arrange_profile
        token = self.edit_token()
        profiles = self.cfg['profiles']
        index = self.teams.currentRow()
        if not 0 <= index < len(profiles): return
        profile = profiles[index]
        groups = list(dict.fromkeys(['', *(p.get('simultaneous') or '' for p in profiles)]))
        group, ok = QInputDialog.getItem(self,'分组与优先级','同时出场组（可输入新组名，留空允许借盘）',
                                        groups, groups.index(profile.get('simultaneous') or ''), True)
        if not ok: return
        group = group.strip()
        peers = [i for i,p in enumerate(profiles) if i != index and p.get('simultaneous') == group]
        rank = 1
        if group and peers:
            current = 1 + sum(i < index for i in peers) if profile.get('simultaneous') == group else len(peers)+1
            rank, ok = QInputDialog.getInt(self,'组内分盘顺序','优先级（1 最先分盘）',current,1,len(peers)+1)
            if not ok: return
        self.finish_edit(token, dict(token[2], profiles=arrange_profile(profiles, index, group, rank)))
        self.refresh_teams(profile['name'])

    def name_profile(self, duplicate):
        try:
            token = self.edit_token()
            cfg = token[2]
            profiles = cfg['profiles']
            index = self.teams.currentRow()
            if not 0 <= index < len(profiles) or self.equip_busy:
                return
            profile = profiles[index]
            old = profile['name']
            names = {p['name'] for p in profiles}
            suggestion = old
            if duplicate:
                suggestion = old+' · 副本'
                serial = 2
                while suggestion in names:
                    suggestion = f'{old} · 副本 {serial}'
                    serial += 1
            name, ok = QInputDialog.getText(self, '复制为另一配队' if duplicate else '重命名',
                '新方案名称（副本可借盘，可单独编辑输出窗口）' if duplicate else '方案名称', text=suggestion)
            name = name.strip()
            if not ok or not duplicate and name == old:
                return
            if not name or name in names:
                raise ValueError('请输入尚未使用的方案名称')
            if duplicate:
                profiles.insert(index+1, dict(deepcopy(profile), name=name, simultaneous=''))
            else:
                profiles[index] = dict(profile, name=name)
                if cfg['fieldProfile'] == old:
                    cfg['fieldProfile'] = name
            self.finish_edit(token, cfg)
            self.refresh_teams(name)
        except Exception as exc:
            QMessageBox.warning(self,APP_NAME,str(exc))

    def edit_windows(self):
        index = self.teams.currentRow()
        if index < 0: return
        from .window_editor import WindowEditor
        token = self.edit_token()
        profile = self.cfg['profiles'][index]
        editor = WindowEditor(profile,self)
        if editor.exec():
            self.edit_profile_fields(token, index, editor.changes)

    def edit_tolerance(self):
        index = self.teams.currentRow()
        if index < 0: return
        token = self.edit_token()
        profile = self.cfg['profiles'][index]
        value, ok = QInputDialog.getDouble(self,profile['name'],'为实战暴击 ≥ 97.6% 最多接受的输出差距（%）',
                                            profile.get('tolerancePct',0.5),0,100,4)
        if ok:
            self.edit_profile_fields(token, index, {'tolerancePct':value})

    def toggle_profile(self):
        index = self.teams.currentRow()
        if index < 0: return
        profile = self.cfg['profiles'][index]
        enabled = not profile.get('enabled',True)
        if enabled and profile.get('simultaneous'):
            from .allocation import group_conflict
            conflict = group_conflict(dict(p, enabled=True) if i == index else p
                                      for i,p in enumerate(self.cfg['profiles']) if p.get('simultaneous') == profile['simultaneous'])
            if conflict: raise ValueError(conflict)
        profiles = list(self.cfg['profiles'])
        profiles[index] = dict(profile, enabled=enabled)
        field = self.cfg.get('fieldProfile', '')
        self.edit_cfg(profiles=profiles, fieldProfile='' if not enabled and field==profile['name'] else field)
        self.refresh_teams(profile['name'])

    def apply_current(self):
        from .manual_equip import confirmation, run_confirmed
        account, root = self.ctx.current_instance_idx, self.root
        index = self.teams.currentRow()
        if self.equip_busy or self.preview_job is not None or not self.ctx.run_context.is_context_stop or str(account) != root.name:
            raise ValueError('请在当前账号的一条龙空闲时应用配队')
        if not 0 <= index < len(self.cfg['profiles']):
            return
        if self.report.get('rules') != read(root/'rules.json',{}).get('version'):
            self.reload()
            raise ValueError('规则已更新，请重新计算后应用配队')
        name = self.cfg['profiles'][index]['name']
        from .loadout import verify_bindings
        current_cfg, wiki = config(root), read(wiki_path(), {})
        verify_bindings(current_cfg, name, wiki)
        request, targets = confirmation(account, name, current_cfg, self.report, read(root/'inventory.json'))
        dialog = QDialog(self); dialog.setWindowTitle('应用当前配队'); dialog.resize(720,420)
        panel = QVBoxLayout(dialog)
        panel.addWidget(QLabel(f'{name} · {len(targets)} 个号位'))
        from .store import equipment_names
        from .stat_reading import display_values
        sets = equipment_names(wiki) | current_cfg.get('aliases',{})
        agents = {p['name']:p.get('request',{}).get('characterName') for p in self.cfg['profiles']}
        table = self.grid(['方案','目标角色','号位','套装','主副词条','上次扫描装备者'])
        self.fill(table,[(t['profile'], agents.get(t['profile']) or t['agent'], t['slot'], sets.get(t['set'],t['set']),
                          display_values(t['observedValues']), owner_label(t)) for t in targets])
        table.resizeColumnsToContents(); panel.addWidget(table)
        note = QLabel('请先进入游戏大世界。确认后会重新扫描驱动盘，按清单换装并复读各个号位。目标变化时会返回预览。')
        note.setWordWrap(True); panel.addWidget(note)
        actions = QHBoxLayout(); panel.addLayout(actions); actions.addStretch()
        back = self.button(actions,'返回',dialog.reject); back.setDefault(True)
        self.button(actions,'重扫并换装',dialog.accept).setAutoDefault(False)
        if not dialog.exec():
            return
        self.equip_busy = True; self.apply_button.setEnabled(False)
        self.equip_status.setText(f'正在应用 {name} · 本次仅换装')
        def worker():
            error = ''
            try:
                run_confirmed(self.ctx, request)
            except Exception as exc:
                error = str(exc)
            self.equip_finished.emit(str(root),name,error)
        Thread(target=worker, name='OhMyZZZ equip', daemon=True).start()

    def finish_equip(self, root, name, error):
        self.equip_busy = False
        if str(self.root) == root and str(self.ctx.current_instance_idx) == self.root.name:
            self.reload(); self.refresh_teams(name)
            if error:
                self.equip_status.setText(f'{name} · {error}')
        else:
            self.on_interface_shown()

    def set_field_profile(self):
        index = self.teams.currentRow()
        if index < 0: return
        profile = self.cfg['profiles'][index]
        name = profile['name']
        if self.cfg.get('fieldProfile') == name:
            self.edit_cfg(fieldProfile='')
        else:
            if not profile.get('enabled',True) or not profile.get('agentKey'):
                raise ValueError('请选择已启用且已关联代理人的方案')
            self.edit_cfg(fieldProfile=name, run_scanner=True)
        self.refresh_teams(name)

    def save_text(self):
        self.check_account(self.editor_account)
        value = json.loads(self.editor.toPlainText())
        if not isinstance(value,dict):
            raise ValueError('需要 JSON 配置对象')
        save_config_draft(self.root,self.editor_base,value)
        self.reset_text()

    def reset_text(self):
        self.check_account(self.editor_account)
        self.editor_drafts.pop(self.editor_account, None)
        self.editor_account = None
        self.reload()

    def set_flag(self,key,checked):
        try:
            self.check_account(self.root)
            cfg = dict(self.cfg, **{key:checked})
            if checked and key in ('afterEarly','applyMarkers'):
                cfg['run_scanner'] = True
            if key=='run_scanner' and not checked:
                cfg.update(afterEarly=False,applyMarkers=False,fieldProfile='')
            from .validation import validate
            validate(cfg)
            save_config(self.root,cfg,self.config_hash)
            if key in ('rotateSpareLocks','protect15','protectNaturalCrit') and (self.root/'inventory.json').is_file():
                self.refresh_saved_preview()
                return
            self.reload()
        except Exception as exc:
            self.reload()
            QMessageBox.warning(self,APP_NAME,str(exc))

    def load_inventory(self):
        account = self.root
        path,_=QFileDialog.getOpenFileName(self,'选择属于当前账号的 ZOD 扫描','','JSON (*.json)')
        if path:
            self.check_account(account)
            self.start_preview(import_path=path)

    def start_probe(self):
        from .disc_probe import probe
        account, root = self.ctx.current_instance_idx, self.root
        if int(root.name) != account or not self.ctx.run_context.is_context_stop:
            raise ValueError('请在当前账号的一条龙空闲时读盘')
        self.probe_button.setEnabled(False)
        self.probe_note.setText('正在进入驱动仓库并复读当前盘…'); self.probe_note.show()
        def worker():
            try:
                record = probe(self.ctx, root, account)
            except Exception as exc:
                record = dict(status='failed', error=str(exc))
            self.probe_finished.emit(record, str(root))
        Thread(target=worker, name='OhMyZZZ single disc', daemon=True).start()

    def finish_probe(self, record, root):
        self.probe_button.setEnabled(True)
        if str(self.root) == root and self.root.name == str(self.ctx.current_instance_idx):
            self.show_probe(record)
            self.show_reserve()
            if self.tabs.currentWidget() is self.detail and self.detail_selection[2]:
                self.refresh_open_detail()

    def show_probe(self, record):
        from .stat_reading import display_values
        self.probe_record = record
        self.detail_button.setEnabled(record.get('status')=='verified' or self.table.currentRow() >= 0)
        text = record.get('error', '')
        if record.get('status') == 'verified':
            disc = record['disc']
            lock = '已锁定' if disc['lockState']=='locked' else '未锁定'
            mark = '已弃置' if disc['markState']=='marked' else '未弃置'
            text = (f"{disc['setKey']}　{disc['slotKey']} 号位 +{disc['level']}　{lock} · {mark}　"
                    + display_values(disc['observedValues']))
        self.probe_note.setText(('上次单盘读取 · ' + text) if text else '')
        self.probe_note.setVisible(bool(text))

    def configure_optimizer(self):
        from .optimizer import loopback_json
        token = self.edit_token()
        url, ok = QInputDialog.getText(self, '配装器连接', '留空使用 OhMyZZZ 托管，或填写自用配装器的本机 HTTP 地址', text=self.cfg['optimizer_url'])
        if not ok:
            return
        url = url.strip()
        if url:
            state = loopback_json(url, '/api/state')
            if not isinstance(state, dict) or not isinstance(state.get('setEffects'), list):
                raise ValueError('该地址未返回配装器的套装数据')
        self.finish_edit(token, dict(token[2], optimizer_url=url))

    def optimizer_session(self):
        from contextlib import nullcontext
        from .engine import managed
        return nullcontext(self.cfg['optimizer_url']) if self.cfg['optimizer_url'] else managed(self.root)

    def open_optimizer(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from .engine import Endpoint
        from .optimizer import loopback_json
        with self.optimizer_session() as endpoint:
            if isinstance(endpoint, Endpoint):
                url = endpoint.url
            else:
                loopback_json(endpoint, '/api/state')
                url = endpoint
            if not QDesktopServices.openUrl(QUrl(url)):
                raise ValueError('无法打开默认浏览器')
            if isinstance(endpoint, Endpoint):
                QMessageBox.information(self, '编辑配装器目标',
                    '请在浏览器中保存角色目标，再回到这里点「确定」结束编辑。随后可点「从配装器保存为配队」。')

    def import_profile(self):
        from .optimizer import saved_targets
        token = self.edit_token()
        with self.optimizer_session() as endpoint:
            choices = saved_targets(endpoint)
        if not choices:
            raise ValueError('请先在配装器保存角色的多角色目标')
        labels=[f"{i+1}. {p['agent']}" for i,p in enumerate(choices)]
        label,ok=QInputDialog.getItem(self,'选择角色','已保存的配装器配置',labels,0,False)
        if not ok: return
        source=choices[labels.index(label)]
        from .roster import same_agent, scanner_agent_key
        wiki = read(wiki_path(), {})
        agent_key = scanner_agent_key(wiki, source['agent'])
        request_name = source['request']['characterName']
        if not same_agent(wiki, source['agent'], request_name):
            raise ValueError('目标名称与请求中的代理人不一致，请在配装器核对后再导入')
        if source['requiresClaimMapping']:
            raise ValueError('此方案要求避让配装器中已占用的盘。导入需核对两份库存中的占用记录。')
        name,ok=QInputDialog.getText(self,'保存配队快照','方案名称',text=source['agent']+' · 常用队')
        if not ok: return
        if not name.strip() or any(p['name']==name.strip() for p in token[2]['profiles']):
            raise ValueError('请输入新的方案名称')
        simultaneous,ok=QInputDialog.getText(self,'同时出场','同时上场的角色填写相同分组，互斥配队可留空')
        if not ok: return
        profile = dict(name=name.strip(),agentKey=agent_key,request=source['request'],
                       simultaneous=simultaneous.strip(),tolerancePct=0.5,sourceId=source['sourceId'])
        from .allocation import group_conflict
        peers = [p for p in token[2]['profiles'] if p.get('simultaneous') == profile['simultaneous']]
        conflict = group_conflict([*peers, profile])
        if conflict: raise ValueError(conflict)
        self.finish_edit(token, dict(token[2], profiles=[*token[2]['profiles'],profile]))
        self.refresh_teams(profile['name'])

    def refresh_profile(self):
        from .optimizer import refreshed_profile, saved_targets
        index = self.teams.currentRow()
        if index < 0 or self.equip_busy:
            return
        token = self.edit_token()
        profile = token[2]['profiles'][index]
        with self.optimizer_session() as endpoint:
            updated, changed = refreshed_profile(profile, saved_targets(endpoint), wiki=read(wiki_path(), {}))
        different = {key: {'原值': profile['request'].get(key), '新值': updated['request'].get(key)} for key in changed}
        if profile.get('agentKey') != updated.get('agentKey'):
            different['换装角色绑定'] = {'原值': profile.get('agentKey') or '未绑定', '新值': updated['agentKey']}
            from .allocation import group_conflict
            conflict = group_conflict(updated if i == index else p for i, p in enumerate(token[2]['profiles'])
                                      if p.get('simultaneous') == updated.get('simultaneous'))
            if conflict:
                raise ValueError(conflict)
        if not different:
            QMessageBox.information(self, profile['name'], '所选配队已与配装器目标一致。')
            return
        question = QMessageBox(self)
        question.setWindowTitle('更新所选配队')
        question.setText(f"将「{profile['name']}」更新为配装器里「{updated['request']['characterName']}」的当前目标？")
        summary = '变更字段：' + '、'.join(different)
        if '换装角色绑定' in different:
            binding = different['换装角色绑定']
            summary += f"\n换装角色：{binding['原值']} → {binding['新值']}"
        question.setInformativeText(summary)
        question.setDetailedText(pretty(different))
        accept = question.addButton('更新配队', QMessageBox.ButtonRole.AcceptRole)
        cancel = question.addButton('取消', QMessageBox.ButtonRole.RejectRole)
        question.setDefaultButton(cancel)
        question.exec()
        if question.clickedButton() is not accept:
            return
        self.edit_profile_fields(token, index, updated)

    @staticmethod
    def build_preview(account, wiki, import_path, reuse_allocations):
        from .maintenance import Deferred, exclusive
        try:
            # The game worker holds this same lock from its fresh scan through marking/equip.
            with exclusive(account.parent/'_maintenance'):
                if import_path is not None:
                    import_inventory(account,import_path)
                preview(account,wiki,refresh=False,reuse_allocations=reuse_allocations)
        except Deferred as exc:
            raise ValueError('插件任务或维护正在运行，请结束后再计算仓库预览。') from exc

    def show_preview_job(self, error=''):
        busy = self.preview_job is not None
        for button in [self.scan_button, self.import_button, *self.calculate_buttons]:
            button.setEnabled(not busy)
        message = error
        if busy:
            message = (f'正在读取账号 {self.preview_job.name} 的游戏仓库并计算预览…' if self.scanning_game
                       else f'正在计算账号 {self.preview_job.name} 的仓库预览…')
        self.preview_note.setText(message)
        self.preview_note.setVisible(bool(message))

    def start_game_scan(self):
        from .scanner_status import scanner_status
        account = self.ctx.current_instance_idx
        self.check_account(self.root)
        if self.preview_job is not None:
            return
        if self.equip_busy or not self.ctx.run_context.is_context_stop:
            raise ValueError('请在一条龙空闲时扫描仓库')
        ready, reason = scanner_status(self.ctx, account)
        if not ready:
            raise ValueError(reason)
        self.start_preview(from_game=True)

    def start_preview(self, *, import_path=None, reuse_allocations=False, from_game=False):
        account = self.root
        self.check_account(account)
        if self.preview_job is not None:
            return
        wiki = wiki_path()
        self.preview_job = account
        self.scanning_game = from_game
        self.preview_refresh_accounts.discard(account)
        self.show_preview_job()
        self.apply_button.setEnabled(False)

        def worker():
            error = ''
            try:
                if from_game:
                    from .manual_equip import run_scan_preview
                    run_scan_preview(self.ctx, int(account.name))
                else:
                    self.build_preview(account, wiki, import_path, reuse_allocations)
            except Exception as exc:
                error = str(exc) or type(exc).__name__
            try:
                self.preview_finished.emit(account, error)
            except RuntimeError:
                pass  # OneDragon closed the settings page during calculation.

        Thread(target=worker, name='OhMyZZZ preview', daemon=True).start()

    def finish_preview(self, account, error):
        if self.preview_job != account:
            return
        self.preview_job = None
        self.scanning_game = False
        if self.root.name != str(self.ctx.current_instance_idx):
            self.on_interface_shown()
        current = self.root == account
        self.show_preview_job('仓库预览计算失败 · '+error if current and error else '')
        if current:
            index = self.teams.currentRow()
            profiles = self.cfg['profiles']
            name = profiles[index]['name'] if 0 <= index < len(profiles) else None
            self.reload()
            if name:
                self.refresh_teams(name)
        else:
            self.show_build()
        if self.root in self.preview_refresh_accounts:
            self.preview_refresh_accounts.discard(self.root)
            if self.preview_stale and not (current and error):
                self.start_preview(reuse_allocations=True)

    def refresh_saved_preview(self):
        # A rules, role or protection edit can reuse allocations for identical team inputs.
        self.reload()
        if self.preview_job is None:
            self.start_preview(reuse_allocations=True)
        else:
            self.preview_refresh_accounts.add(self.root)

    def calculate(self, *, import_path=None, reuse_allocations=False):
        account = self.root
        self.check_account(account)
        if self.preview_job is not None:
            self.preview_refresh_accounts.add(account)
            self.reload()
            return
        try:
            self.build_preview(account, wiki_path(), import_path, reuse_allocations)
        finally:
            self.reload()

    def check_account(self, account):
        if account != self.root or account.name != str(self.ctx.current_instance_idx):
            self.on_interface_shown()
            raise ValueError('账号已切换，请在当前账号重新操作。')

    def edit_token(self):
        self.check_account(self.root)
        return self.root, self.config_hash, deepcopy(self.cfg)

    def edit_profile_fields(self, token, index, changes):
        cfg = token[2]
        profiles = list(cfg['profiles'])
        profiles[index] = dict(profiles[index], **changes)
        self.finish_edit(token, dict(cfg, profiles=profiles))
        self.refresh_teams(profiles[index]['name'])

    def finish_edit(self, token, value, *, reuse_allocations=False):
        account, expected, _ = token
        self.check_account(account)
        try:
            save_config(account, value, expected)
        except Exception:
            self.reload()
            raise
        if (account/'inventory.json').is_file():
            if reuse_allocations:
                self.calculate(reuse_allocations=True)
            else:
                self.refresh_saved_preview()
        else:
            self.reload()

    def edit_cfg(self, **changes):
        token = self.edit_token()
        self.finish_edit(token, dict(token[2], **changes))

    def pin(self):
        index=self.table.currentRow()
        if index < 0: return
        row = self.rows[index]
        if row['marked']: return
        fp=row['fp']; pins=set(self.cfg['pins'])
        self.edit_cfg(pins=sorted(pins^{fp}))

    def open_disc_detail(self, *, probe=None):
        if self.root.name != str(self.ctx.current_instance_idx):
            self.on_interface_shown(); return
        idx = self.table.currentRow()
        if probe is None:
            probe = not 0 <= idx < len(self.rows)
        if probe:
            self.present_detail(None)
        elif 0 <= idx < len(self.rows):
            self.present_detail(self.rows[idx])

    def refresh_open_detail(self):
        if self.tabs.currentWidget() is not self.detail:
            return
        account, previous, probe = self.detail_selection
        tab = self.detail.tabs.currentIndex()
        if account == self.root and account.name == str(self.ctx.current_instance_idx):
            if probe and self.detail_observation == digest(self.probe_record):
                self.present_detail(None)
                self.detail.tabs.setCurrentIndex(tab)
                return
            if not probe and not previous.get('ambiguous'):
                matches = [(i, row) for i, row in enumerate(self.rows) if row['fp'] == previous['fp']]
                if len(matches) == 1:
                    index, row = matches[0]
                    if not row.get('ambiguous') and row.get('observedValues') == previous.get('observedValues'):
                        if not self.table.isRowHidden(index):
                            self.table.selectRow(index)
                        self.present_detail(row)
                        self.detail.tabs.setCurrentIndex(tab)
                        return
        self.tabs.setCurrentIndex(0)
        self.status.setText(self.status.text()+'　原盘读数已变化或无法唯一对应，请重新选择。')

    def present_detail(self, row):
        from .disc_trace import probe_trace
        probe = row is None
        report = self.report
        if probe:
            wiki = read(wiki_path(), {})
            row, report = probe_trace(self.probe_record,self.cfg,self.rules,wiki,read(self.root/'owned-agents.json', []))
            self.detail_observation = digest(self.probe_record)
        self.detail_selection = (self.root, row, probe)
        self.detail.present(row, report, set_name=self.set_names.get(row['set'],row['set']), probe=probe, pinned=row['fp'] in self.cfg['pins'],
                            coverage=None if probe or row['marked'] else self.coverage.get(tuple(row[k] for k in SELECTORS)),
                            rule_keys=self.keys, team_names=[p['name'] for p in self.cfg['profiles']],
                            stale=not probe and self.preview_stale)
        if probe:
            criteria = {key:row[key] for key in SELECTORS}
            reverse = equipment_keys(self.set_names)
            rules = [normalized_rule(rule, reverse) for rule in self.cfg['reserve_rules']]
            limits, _ = matching_rules(criteria, dict(self.cfg, reserve_rules=rules))
            quotas = '、'.join(purpose+' '+('不限' if count is None else f'{count} 张')
                               for purpose, (_, count, _) in limits.items())
            self.detail.spares.setText('当前同类名额　'+quotas+'　｜　'+self.detail.spares.text())
        self.tabs.setTabVisible(self.detail_tab,True); self.tabs.setCurrentIndex(self.detail_tab)

    def edit_detail_policy(self, action):
        try:
            root, row, _ = self.detail_selection
            if root != self.root or root.name != str(self.ctx.current_instance_idx):
                self.on_interface_shown(); return
            if action == 'pin':
                if row['marked']: return
                self.edit_cfg(pins=sorted(set(self.cfg['pins']) ^ {row['fp']}))
            else:
                change = self.set_quota(row)
                if change and self.tabs.currentWidget() is self.detail:
                    self.detail.tabs.setCurrentIndex(1)
                    self.detail.spares.setText(change+'　｜　'+self.detail.spares.text())
        except Exception as exc:
            QMessageBox.warning(self,APP_NAME,str(exc))

    def open_detail_rule(self, key):
        if self.root.name != str(self.ctx.current_instance_idx):
            self.on_interface_shown(); return
        self.on_interface_shown()
        if key in self.keys:
            self.only_conflicts.setChecked(False); self.rule_search.clear()
            self.tabs.setCurrentIndex(1); self.fields.selectRow(self.visible_keys.index(key))

    def open_detail_team(self, name):
        if self.root.name != str(self.ctx.current_instance_idx):
            self.on_interface_shown(); return
        self.on_interface_shown()
        if any(p['name']==name for p in self.cfg['profiles']):
            self.tabs.setCurrentIndex(3); self.refresh_teams(name)

    def show_reserve(self):
        index = self.table.currentRow()
        selected = 0 <= index < len(self.rows)
        self.pin_button.setEnabled(selected and not self.rows[index]['marked'])
        self.quota_button.setEnabled(selected)
        self.detail_button.setEnabled(selected or self.probe_record.get('status')=='verified')
        self.detail_button.setText('查看盘详情' if selected or self.probe_record.get('status')!='verified' else '查看上次单盘读数')
        self.stat_info.clear()
        selected_row = self.rows[index] if selected else {}
        self.restore_unlock.setText('取消这张盘的撤销弃置保护' if selected_row.get('manualKeep') else '取消这张盘的手动解锁保护')
        self.restore_unlock.setVisible(bool(selected_row.get('manualUnlock') or selected_row.get('manualKeep')) and not selected_row['marked'])
        self.reserve_info.setText('选择一张盘，可查看同类备用名额和各用途的实际张数。')
        if not 0 <= index < len(self.rows): return
        from .stat_reading import display_values
        self.stat_info.setText(display_values(self.rows[index].get('observedValues')))
        group = self.coverage.get(tuple(self.rows[index][k] for k in SELECTORS))
        if not group: return
        parts = []
        for use in group['purposes']:
            cap = '不限' if use['count'] is None else str(use['count'])
            parts.append(f"{use['purpose']} {use['covered']}/{cap}")
        self.reserve_info.setText(f"同类可选 {group['eligible']} 张，实际备用 {group['kept']} 张　｜　"+'　'.join(parts))

    def clear_manual_exception(self):
        from .marks import clear_manual_exception
        from .store import equipment_names
        index = self.table.currentRow()
        if not 0 <= index < len(self.rows): return
        if self.ctx.current_instance_idx != int(self.root.name):
            raise ValueError('当前账号已变化，请重新打开配置页')
        names = equipment_names(read(wiki_path(), {}))
        names.update(self.cfg.get('aliases', {}))
        clear_manual_exception(self.root, self.rows[index], self.report['inventory'], names)
        self.calculate(reuse_allocations=True)

    def set_quota(self, r=None):
        if r is None:
            index=self.table.currentRow()
            if index<0: return
            r=self.rows[index]
        selectors = {k:r[k] for k in SELECTORS}
        before = self.coverage.get(tuple(selectors.values()))
        summary = self.edit_quota(selectors, '通用')
        if summary is None: return
        match = next((i for i,item in enumerate(self.rows) if item['fp']==r['fp'] and item['id']==r.get('id')),None)
        if match is not None and not self.table.isRowHidden(match): self.table.selectRow(match)
        after = self.coverage.get(tuple(selectors.values()))
        if before and after and self.preview_job is None and not self.preview_stale:
            change = f"调整前 {before['kept']} 张 → 调整后 {after['kept']} 张"
            self.reserve_info.setText(change+'　｜　'+self.reserve_info.text())
            return change
        return summary

    def edit_preset(self, selectors, purpose):
        try:
            self.edit_quota(selectors, purpose)
        except Exception as exc:
            QMessageBox.warning(self,APP_NAME,str(exc))

    def edit_quota(self, selectors, purpose):
        from .quota_editor import QuotaEditor
        from .reserve_presets import scope_name
        token = self.edit_token()
        title = scope_name(selectors, self.set_names)
        report = self.report if self.report and not self.preview_stale else None
        dialog = QuotaEditor(self, selectors, token[2], equipment_keys(self.set_names), title, report=report)
        dialog.purpose.setCurrentText(purpose)
        if not dialog.exec(): return
        self.check_account(token[0])
        rules, summary = dialog.result_rules()
        if rules == dialog.cfg['reserve_rules']:
            return
        self.finish_edit(token, dict(dialog.cfg, reserve_rules=rules), reuse_allocations=True)
        return summary

    def update_rules(self):
        root = self.root
        if root in self.rule_updates: return
        self.rule_updates.add(root)
        self.sync_button.setEnabled(False)
        self.status.setText('正在更新此账号的订阅来源…')

        def fetch_rules():
            error = ''
            try:
                sync(root)
            except Exception as exc:
                error = str(exc)
            try:
                self.rules_finished.emit(root, error)
            except RuntimeError:
                # OneDragon can unload the widget while the download is in progress.
                pass

        Thread(target=fetch_rules, name='OhMyZZZ rules', daemon=True).start()

    def finish_rules(self, root, error):
        self.rule_updates.discard(root)
        if self.root != root or str(self.ctx.current_instance_idx) != root.name:
            return
        self.sync_button.setEnabled(True)
        if error:
            fallback = '沿用最近成功版本' if self.rules.get('fields') else '尚无可用规则快照'
            self.status.setText(f'订阅更新失败，{fallback} · {error}')
            return
        try:
            if (root/'inventory.json').is_file():
                self.refresh_saved_preview()
            else:
                self.reload()
            if self.conflicts:
                self.tabs.setCurrentIndex(1)
                self.rule_search.clear()
                self.only_conflicts.setChecked(True)
        except Exception as exc:
            self.reload()
            self.status.setText('订阅已更新，预览待重新计算 · '+str(exc))

    def edit_rule(self):
        i=self.fields.currentRow()
        if i<0: return
        account = self.root
        key = self.visible_keys[i]
        seen_remote = self.rules.get('fields', {}).get(key)
        seen_local = self.cfg['overrides'].get(key)
        old = seen_local or {'mode':'inherit', 'values':seen_remote or []}
        row = self.rule_rows[key]
        modes = {'继承上游':'inherit','额外接受':'add','只接受':'only'}
        title,ok=QInputDialog.getItem(self,'字段规则','覆盖方式',list(modes),list(modes.values()).index(old['mode']),False)
        if not ok: return
        mode = modes[title]
        selected = old['values'] if old['mode'] != 'inherit' or mode == 'only' else []
        override = None
        from .subscriptions import stat_field
        field = key.rsplit('/',1)[-1]
        role_field = stat_field(key)
        if mode != 'inherit' and (role_field or (key.startswith('matrix/') and field in ('4','5','6'))):
            from .main_rule_editor import MainRuleEditor
            dialog = MainRuleEditor(self, row[0]+' · '+row[1], field[-1], mode, seen_remote or [], selected, role_field=role_field)
            if not dialog.exec(): return
            override = dict(mode=mode, values=dialog.values())
        elif mode != 'inherit' and key.startswith('build/') and key.rsplit('/',1)[-1] in ('sets4','sets2'):
            from .set_rule_editor import SetRuleEditor
            matrix_sets = {k.split('/')[1] for k in self.keys if k.startswith('matrix/') and k.count('/') == 2}
            choices = {k:self.set_names.get(k,k) for k in matrix_sets | self.set_names.keys()}
            dialog = SetRuleEditor(self, row[0]+' · '+row[1], mode, choices, seen_remote or [], selected)
            if not dialog.exec(): return
            override = dict(mode=mode, values=dialog.values())
        elif mode != 'inherit':
            text,ok=QInputDialog.getMultiLineText(self,'字段规则','每行一个值',text='\n'.join(selected))
            if not ok: return
            override = dict(mode=mode, values=[v.strip() for v in text.splitlines() if v.strip()])
        if self.root != account or str(self.ctx.current_instance_idx) != account.name:
            self.on_interface_shown()
            raise ValueError('账号已切换，请在当前账号重新选择字段。')
        try:
            save_field_override(account, key, seen_remote=seen_remote, seen_local=seen_local, override=override)
        finally:
            self.reload()
        if (account/'inventory.json').is_file():
            self.refresh_saved_preview()

    def rule_row(self, key):
        parts = key.split('/')
        source, field = parts[0], parts[-1]
        subject = '/'.join(parts[1:-1]) or key
        localized = (self.set_names if source=='matrix' else self.agent_names).get(subject)
        if localized and localized != subject:
            subject = localized+' · '+subject
        labels = {'sets4':'四件套','sets2':'两件套','subs':'副词条'}
        for slot in '456':
            labels[slot] = labels['slot'+slot] = f'{slot} 号主词条'
        tabs = field_tabs(self.rules, key) or [{'matrix':'Set+Main','build':'Prydwen Builds','guide':'角色攻略表'}.get(source,source)]
        remote = self.rules.get('fields',{}).get(key)
        tab_label = ('原页签 · ' if key in self.rules.get('formerOrigins', {}) else '') + '、'.join(tabs)
        conflict = self.conflicts.get(key)
        override = self.cfg['overrides'].get(key) or {}
        mode = override.get('mode','inherit')
        def display(values, *, show_accepted=False):
            if values is None: return '字段缺失'
            if source == 'matrix':
                from .main_rule_editor import NAMES
                return '、'.join(NAMES.get(v,v) for v in values) or '无'
            if source == 'build' and field in ('sets4','sets2'):
                return '、'.join(self.set_names.get(v,v) for v in values) or '无'
            if show_accepted and source in ('guide','build') and field in ('subs','slot4','slot5','slot6'):
                from .main_rule_editor import NAMES
                from .purpose_editor import SUBSTAT_NAMES
                from .subscriptions import accepted_terms
                names = SUBSTAT_NAMES if field == 'subs' else NAMES
                return '、'.join(names.get(v, v) for v in sorted(accepted_terms(key, values))) or '无'
            return '、'.join(values) or '无'
        local = '继承上游' if mode=='inherit' else ('额外接受 ' if mode=='add' else '只接受 ') + display(override['values'], show_accepted=True)
        return [('⚠ ' if conflict else '')+subject, labels.get(field,field), tab_label,
                display(conflict['old']) if conflict else '—', display(remote), local, display(effective(remote,override or None), show_accepted=True)]

    def filter_rules(self, *_):
        if not hasattr(self, 'rule_rows'):
            return
        old = getattr(self, 'visible_keys', [])
        index = self.fields.currentRow()
        selected = old[index] if 0 <= index < len(old) else None
        terms = self.rule_search.text().casefold().split()
        self.visible_keys = []
        for key, row in self.rule_rows.items():
            if self.only_conflicts.isChecked() and key not in self.conflicts:
                continue
            searchable = ' '.join([key, *row[:3]]).casefold().replace(' ', '')
            if all(term in searchable for term in terms):
                self.visible_keys.append(key)
        self.fields.blockSignals(True)
        try:
            self.fill(self.fields, [self.rule_rows[k] for k in self.visible_keys])
            if self.visible_keys:
                self.fields.selectRow(self.visible_keys.index(selected) if selected in self.visible_keys else 0)
        finally:
            self.fields.blockSignals(False)
        self.rule_total.setText(f'{len(self.visible_keys)} / {len(self.keys)} 条')
        self.select_rule()

    def select_rule(self):
        index = self.fields.currentRow()
        conflict = self.conflicts.get(self.visible_keys[index]) if 0 <= index < len(self.visible_keys) else None
        for action, button in self.rule_actions.items():
            button.setEnabled(can_merge(conflict) if action=='union' else bool(conflict))

    def resolve(self,action):
        self.check_account(self.root)
        i=self.fields.currentRow()
        if i<0: return
        key=self.visible_keys[i]; conflict=self.conflicts.get(key)
        if not conflict: return
        try:
            resolve_conflict(self.root,key,conflict,action)
        finally:
            self.reload()
        if (self.root/'inventory.json').is_file(): self.refresh_saved_preview()
