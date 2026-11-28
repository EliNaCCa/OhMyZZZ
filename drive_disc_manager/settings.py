import json
from copy import deepcopy
from threading import Thread
from PySide6.QtCore import Signal, Qt, QSignalBlocker, QTimer
from one_dragon_qt.widgets.base_interface import BaseInterface
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QWidget,
    QTableWidgetItem,QFileDialog,QInputDialog,QMessageBox,QAbstractItemView,QStackedWidget)
from .store import read,save,config,import_inventory,digest,equipment_names,equipment_keys,snapshot_path,preview_path
from .store import is_pinned, known_agents, toggle_pin
from .service import preview
from .subscriptions import sync, current_conflicts, can_merge, effective, field_tabs, resolve_conflict, save_config, save_config_draft, save_field_override
from qfluentwidgets import (PushButton, PrimaryPushButton, DropDownPushButton, RoundMenu,
                            TableWidget, CheckBox, ComboBox, SpinBox, LineEdit, PlainTextEdit, ScrollArea, BodyLabel, StrongBodyLabel, SubtitleLabel, FluentIcon)
from one_dragon_qt.widgets.setting_card.switch_setting_card import SwitchSettingCard
from .navigation import DetailPages, ManagerPages
from .manager_app import wiki_path
from .drive_disc_manager_const import APP_ID, APP_NAME, PLUGIN_VERSION
from .reserve_policy import matching_rules, normalized_rule, SELECTORS
from .roster import roster_catalog
from .disc_owner import owner_label, preview_owner
from .optimizer import effective_crit_rate


def pretty(value):
    return json.dumps(value,ensure_ascii=False,indent=2)


def crit_rate_text(result):
    if result.get('critRate') is None:
        return '—'
    value = format(effective_crit_rate(result), 'f')
    return (value.rstrip('0').rstrip('.') if '.' in value else value) + '%'


class ManagerInterface(BaseInterface):
    probe_finished = Signal(object, str)
    equip_finished = Signal(str, str, str, object)
    rules_finished = Signal(object, str)
    preview_finished = Signal(object, str)
    showcase_finished = Signal(object, str, str)

    def __init__(self, ctx, parent=None):
        super().__init__('ohmyzzz_settings', APP_NAME, '', parent)
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 12)
        self.status = BodyLabel()
        self.status.hide()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.preview_job = None
        self.export_poll = QTimer(self)
        self.export_poll.setInterval(2500)
        self.export_poll.timeout.connect(self.refresh_new_export)
        self.last_export_attempt = None
        self.scanning_game = False
        self.preview_refresh_accounts = set()
        self.preview_finished.connect(self.finish_preview, Qt.ConnectionType.QueuedConnection)
        self.preview_note = BodyLabel(); self.preview_note.setWordWrap(True); self.preview_note.hide()
        layout.addWidget(self.preview_note)
        self.navigation = tabs = ManagerPages()
        self.tabs = tabs.stack
        layout.addWidget(tabs, 1)
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'仓库预览')
        bar = QHBoxLayout(); box.addLayout(bar)
        self.scan_button = self.button(bar,'扫描仓库',self.start_game_scan,primary=True)
        self.scan_button.setToolTip('更新库存并计算留盘建议')
        self.calculate_buttons = [self.button(bar,'重新计算',self.start_preview)]
        bar.addStretch()
        self.detail_button = self.button(bar,'盘详情',self.open_disc_detail)
        self.button(bar,'对比换盘',lambda:tabs.open('对比换盘'))
        more = DropDownPushButton('更多'); bar.addWidget(more)
        actions = RoundMenu(parent=more); more.setMenu(actions)
        self.import_button = self.menu_action(actions, '导入扫描文件', self.load_inventory)
        self.probe_button = self.menu_action(actions, '读一张盘', self.start_probe)
        self.recent_button = self.menu_action(actions, '看最近获得的盘', lambda:self.start_probe(recent_count=3))
        self.recent_result = self.menu_action(actions, '上次读到的新盘', self.show_recent)
        actions.addSeparator()
        self.pin_button = self.menu_action(actions, '切换个人保护', self.pin)
        self.quota_button = self.menu_action(actions, '同类备用数量', self.set_quota)
        self.observation_button = self.menu_action(actions, '上次单盘读数', lambda:self.open_disc_detail(probe=True))
        self.probe_note = BodyLabel(); self.probe_note.setWordWrap(True); box.addWidget(self.probe_note)
        self.recent_table = self.grid(['驱动盘', '建议', '主属性', '副属性'])
        self.recent_record = None
        self.detail_recent_index = None
        self.recent_table.itemActivated.connect(lambda item:self.invoke(lambda:self.open_recent_detail(item.row())))
        self.recent_table.setWordWrap(False)
        for col, width in enumerate((175, 130, 120)):
            self.recent_table.setColumnWidth(col, width)
        self.recent_table.hide()
        box.addWidget(self.recent_table)
        self.probe_finished.connect(self.finish_probe, Qt.ConnectionType.QueuedConnection)
        self.table = self.grid(['套装','号位','主属性','等级','建议','原因'])
        for col, width in enumerate((150, 70, 130, 84, 110)):
            self.table.setColumnWidth(col, width)
        self.table.setWordWrap(False)
        self.warehouse_summary = StrongBodyLabel()
        box.addWidget(self.warehouse_summary)
        self.scan_comparison = BodyLabel(); self.scan_comparison.hide(); box.addWidget(self.scan_comparison)
        from .inventory_filter import InventoryFilter
        self.inventory_filter = InventoryFilter(self.table)
        box.addWidget(self.inventory_filter); box.addWidget(self.table, 1)
        self.empty = QWidget()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title = SubtitleLabel()
        self.empty_hint = BodyLabel()
        for label in (self.empty_title, self.empty_hint):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(label)
        self.last_seen = self.grid(['套装', '号位', '等级', '主属性', '副属性', '装备者'])
        self.last_seen.hide()
        empty_layout.addWidget(self.last_seen, 1)
        box.addWidget(self.empty, 1)
        self.stat_info = BodyLabel(); self.stat_info.setWordWrap(True); box.addWidget(self.stat_info)
        self.reserve_info = BodyLabel(); self.reserve_info.setWordWrap(True); box.addWidget(self.reserve_info)
        self.restore_unlock = self.button(box, '取消这张盘的手动解锁保护', self.clear_manual_exception)
        self.restore_unlock.hide()
        self.table.itemSelectionChanged.connect(self.show_reserve)
        self.inventory_filter.changed.connect(self.show_reserve)
        self.table.cellDoubleClicked.connect(lambda *_:self.open_disc_detail())
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'规则订阅')
        bar = QHBoxLayout(); box.addLayout(bar)
        self.auto_sync = CheckBox('每天自动更新规则')
        self.auto_sync.clicked.connect(lambda checked:self.set_flag('auto_sync',checked))
        self.rule_stamp = BodyLabel(); self.rule_stamp.setWordWrap(True)
        bar.addWidget(self.auto_sync); bar.addWidget(self.rule_stamp,1)
        bar = QHBoxLayout(); box.addLayout(bar)
        self.rule_search = LineEdit(); self.rule_search.setPlaceholderText('搜索角色、套装、号位或来源'); self.rule_search.setClearButtonEnabled(True)
        self.only_conflicts = CheckBox('只看冲突'); self.rule_total = BodyLabel()
        bar.addWidget(self.rule_search,1); bar.addWidget(self.only_conflicts); bar.addWidget(self.rule_total)
        self.rule_search.textChanged.connect(self.filter_rules); self.only_conflicts.toggled.connect(self.filter_rules)
        bar = QHBoxLayout(); box.addLayout(bar)
        self.rule_updates = set()
        self.sync_button = self.button(bar,'更新规则',self.update_rules)
        self.rules_finished.connect(self.finish_rules, Qt.ConnectionType.QueuedConnection)
        bar.addStretch()
        self.rule_actions = {action:self.button(bar, title, lambda a=action:self.resolve(a)) for action, title in
                        [('local','保留本地'), ('remote','使用订阅'), ('union','合并')]}
        self.button(bar,'编辑规则',self.edit_rule)
        self.fields = self.grid(['角色 / 套装','规则','来源','订阅旧值','订阅新值','我的设置','生效规则']); box.addWidget(self.fields)
        self.fields.setWordWrap(False)
        self.fields.setColumnWidth(0, 170)
        self.fields.itemSelectionChanged.connect(self.select_rule)
        self.fields.cellDoubleClicked.connect(lambda *_:self.invoke(self.edit_rule))
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'角色留盘')
        self.owned_toggle = CheckBox('为已拥有的角色留盘')
        self.owned_toggle.clicked.connect(self.set_include_owned)
        box.addWidget(self.owned_toggle)
        bar = QHBoxLayout(); box.addLayout(bar)
        self.button(bar,'为所选角色留盘',lambda:self.select_roster(True))
        self.button(bar,'取消留盘',lambda:self.select_roster(False))
        self.agent_summary = BodyLabel(); box.addWidget(self.agent_summary)
        self.agent_table = self.grid(['角色','是否拥有','留盘','订阅名称','套装来源'])
        self.agent_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        box.addWidget(self.agent_table)
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'我的配队')
        self.team_views = QStackedWidget(); box.addWidget(self.team_views)
        team_list = QWidget(); self.team_views.addWidget(team_list)
        box = QVBoxLayout(team_list); box.setContentsMargins(0, 0, 0, 0)
        from .squad_editor import SquadEditor
        self.squad_editor = SquadEditor(); self.team_views.addWidget(self.squad_editor)
        self.squad_editor.saved.connect(lambda: self.invoke(self.save_squad))
        self.squad_editor.canceled.connect(lambda: self.team_views.setCurrentIndex(0))
        engine_bar = QHBoxLayout(); box.addLayout(engine_bar)
        self.button(engine_bar,'导入方案',self.import_profile)
        self.profile_menu = DropDownPushButton('管理方案'); engine_bar.addWidget(self.profile_menu)
        menu = RoundMenu(parent=self.profile_menu); self.profile_menu.setMenu(menu)
        self.menu_action(menu,'复制为另一配队',lambda:self.name_profile(True))
        self.menu_action(menu,'重命名',lambda:self.name_profile(False))
        self.menu_action(menu,'队伍成员',self.edit_squad)
        self.refresh_target_button = self.menu_action(menu,'同步角色目标',self.refresh_profile)
        self.menu_action(menu,'分组与优先级',self.edit_allocation)
        self.window_action = self.menu_action(menu,'输出窗口',self.edit_windows)
        self.menu_action(menu,'启用 / 停用',self.toggle_profile)
        engine_bar.addStretch()
        self.calculate_buttons.append(self.button(engine_bar,'计算配装',lambda:self.start_preview(builds=True),primary=True))
        self.apply_button = self.button(engine_bar,'一键换装',self.apply_current)
        self.equip_busy = False
        self.equip_review = None
        self.equip_finished.connect(self.finish_equip, Qt.ConnectionType.QueuedConnection)
        self.teams = self.grid(['方案','同时出场组','实战暴击率','输出差距','选择原因'])
        self.teams.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        box.addWidget(self.teams)
        self.team_score = BodyLabel(); self.team_score.setWordWrap(True); box.addWidget(self.team_score)
        self.build_tabs = DetailPages(); box.addWidget(self.build_tabs)
        self.slots = self.grid(['号位','套装','主属性','等级','当前装备者','用途'])
        self.build_tabs.addTab(self.slots,'方案用盘')
        self.panel_stats = self.grid(['面板属性','目标类型','目标值','当前装备','所选方案','相比当前','距目标'])
        panel = QWidget(); panel_box = QVBoxLayout(panel)
        self.panel_note = BodyLabel(); self.panel_note.setWordWrap(True); panel_box.addWidget(self.panel_note)
        panel_box.addWidget(self.panel_stats); self.build_tabs.addTab(panel,'面板对比')
        self.score_detail = PlainTextEdit(); self.score_detail.setReadOnly(True); self.build_tabs.addTab(self.score_detail, '计算详情')
        self.equip_status = BodyLabel(); self.equip_status.setWordWrap(True); box.addWidget(self.equip_status)
        self.teams.itemSelectionChanged.connect(self.show_build)
        scroll = ScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(ScrollArea.Shape.NoFrame)
        page = QWidget(); scroll.setWidget(page); box = QVBoxLayout(page); tabs.addTab(scroll,'运行设置')
        bar = QHBoxLayout(); box.addLayout(bar)
        bar.addWidget(BodyLabel('读取仓库')); self.reader = ComboBox()
        self.reader.addItem('游戏画面（OCR）', userData='ocr')
        self.reader.addItem('国服 · 读取导出文件', userData='cn_export')
        self.reader.addItem('国际服 · 网络读取', userData='network')
        self.reader.currentIndexChanged.connect(lambda _:self.invoke(lambda:self.edit_cfg(inventorySource=self.reader.currentData())))
        bar.addWidget(self.reader)
        self.reader_open = self.button(bar, '打开读取器', self.open_reader)
        self.export_folder = self.button(bar, '选择导出目录', self.choose_export_folder)
        self.showcase_button = self.button(bar, '绑定角色展柜', self.edit_showcase)
        self.reader_help = self.button(bar, '读取器说明', self.open_reader_guide)
        bar.addStretch()
        bar = QHBoxLayout(); box.addLayout(bar); self.limits = {}
        for key, title in [('dailyLimit','每天最多'), ('weeklyLimit','每周最多')]:
            bar.addWidget(BodyLabel(title)); spin = SpinBox(); spin.setRange(1,99); spin.setSuffix(' 次'); bar.addWidget(spin)
            spin.valueChanged.connect(lambda value,k=key:self.invoke(lambda:self.set_limit(k,value)))
            self.limits[key] = spin
        bar.addStretch()
        self.flags = {}
        for key, icon, title, hint in [
            ('run_scanner', FluentIcon.SEARCH, '整理前刷新驱动仓库', '关闭后用上次读取的库存生成筛选结果。'),
            ('applyMarkers', FluentIcon.FILTER, '自动标记驱动盘', '要留的锁定，要拆的标记为弃置。'),
            ('autoUpgrade', FluentIcon.UP, '试强化潜力盘', '每升 3 级比较一次，还能超过同类成品就继续升。'),
            ('protect15', FluentIcon.ACCEPT, '保留满级盘（+15）', None),
            ('protectNaturalCrit', FluentIcon.TILES, '保留四词条双暴胚子', '掉落时就有四条副属性，同时带暴击率和暴击伤害。强化后也保留。'),
            ('rotateSpareLocks', FluentIcon.SYNC, '优先留更好的备用盘', '同类盘按你的备用规则比较。先锁新盘，再解开旧备用盘。你手动锁的盘会保留。'),
        ]:
            toggle = SwitchSettingCard(icon=icon, title=title)
            toggle.btn.setAccessibleName(title)
            if hint:
                toggle.setToolTip(hint)
                toggle.btn.setAccessibleDescription(hint)
            toggle.value_changed.connect(lambda checked,k=key:self.set_flag(k,checked))
            self.flags[key] = toggle
            box.addWidget(toggle)
            if key == 'autoUpgrade':
                self.upgrade_budget = QWidget()
                budget = QHBoxLayout(self.upgrade_budget)
                budget.addWidget(BodyLabel('每次整理最多强化'))
                steps = SpinBox(); steps.setRange(1, 99); steps.setSuffix(' 档')
                steps.setAccessibleName('每次整理最多强化几档')
                steps.valueChanged.connect(lambda value: self.invoke(lambda: self.set_limit('upgradeSteps', value)))
                self.limits['upgradeSteps'] = steps
                budget.addWidget(steps); budget.addStretch(); box.addWidget(self.upgrade_budget)
        self.scanner_note = BodyLabel(); self.scanner_note.setWordWrap(True); box.addWidget(self.scanner_note)
        bar = QHBoxLayout(); box.addLayout(bar)
        self.button(bar,'配装器地址',self.configure_optimizer)
        self.advanced = QWidget(); advanced_box = QVBoxLayout(self.advanced)
        self.button(bar,'高级配置',lambda:self.advanced.setVisible(self.advanced.isHidden())); bar.addStretch()
        self.editor = PlainTextEdit(); self.editor.setMinimumHeight(160); advanced_box.addWidget(self.editor)
        self.editor_drafts = {}; self.editor_account = None
        bar = QHBoxLayout(); advanced_box.addLayout(bar)
        self.button(bar,'保存配置',self.save_text)
        self.button(bar,'撤销修改',self.reset_text)
        box.addWidget(self.advanced); self.advanced.hide(); box.addStretch()
        page = QWidget(); box = QVBoxLayout(page); tabs.addTab(page,'版本更新')
        self.updates_enabled = CheckBox('启动一条龙时提示 OhMyZZZ 更新（所有账号）')
        self.updates_enabled.clicked.connect(self.set_updates)
        box.addWidget(self.updates_enabled)
        self.maintenance_status = BodyLabel(); self.maintenance_status.setWordWrap(True)
        box.addWidget(self.maintenance_status)
        bar = QHBoxLayout(); box.addLayout(bar)
        self.button(bar,'立即检查更新',self.check_release)
        self.rollback_button = self.button(bar,'回到上一版',lambda:self.queue_maintenance('rollback'))
        self.cancel_queue = self.button(bar,'取消排队',lambda:self.queue_maintenance(''))
        self.button(bar,'刷新状态',self.show_maintenance)
        from .release_notes import installed_history
        box.addWidget(BodyLabel('更新日志'))
        history = PlainTextEdit(); history.setReadOnly(True)
        history.setPlainText(installed_history(PLUGIN_VERSION))
        box.addWidget(history, 1)
        from .reserve_presets import ReservePresets
        self.reserve_presets = ReservePresets()
        tabs.addTab(self.reserve_presets, '备用规则')
        self.reserve_presets.edit.connect(self.edit_preset)
        from .disc_detail_page import DiscDetails
        self.detail = DiscDetails(); self.detail_tab = tabs.addTab(self.detail, '盘详情')
        self.detail.back.connect(self.back_from_disc)
        self.detail.rule.connect(self.open_detail_rule)
        self.detail.team.connect(self.open_detail_team)
        self.detail.edit_policy.connect(self.edit_detail_policy)
        from .salvage_page import SalvagePage
        self.salvage_page = SalvagePage(self.fill)
        tabs.addTab(self.salvage_page, '库存记录')
        self.salvage_page.acknowledge.connect(lambda root, batch, inv:self.invoke(lambda:self.acknowledge_salvage(root, batch, inv)))
        self.tabs.currentChanged.connect(self.refresh_salvage)
        from .optimizer_page import OptimizerPage
        self.optimizer_page = OptimizerPage()
        self.target_edit = None
        tabs.addTab(self.optimizer_page, '配装器')
        self.optimizer_page.import_requested.connect(lambda:self.invoke(self.import_profile))
        self.optimizer_page.retry.connect(lambda:self.invoke(self.open_optimizer))
        self.optimizer_page.back.connect(lambda:tabs.open('我的配队'))
        from .challenge_page import ChallengePage
        self.challenge = ChallengePage()
        self.challenge.save_lineup.clicked.connect(lambda:self.invoke(self.save_challenge_lineup))
        tabs.addTab(self.challenge, '关卡分队')
        self.showcase_jobs = set()
        self.challenge.read_agents.clicked.connect(lambda:self.invoke(self.read_showcase_agents))
        self.showcase_finished.connect(self.finish_showcase, Qt.ConnectionType.QueuedConnection)
        self.tabs.currentChanged.connect(self.optimizer_tab_changed)
        self.tabs.currentChanged.connect(self.show_challenge)
        from .disc_comparison_page import DiscComparison
        self.disc_comparison = DiscComparison(lambda:tabs.open('仓库预览'))
        tabs.addTab(self.disc_comparison, '对比换盘')
        self.tabs.currentChanged.connect(self.show_disc_comparison)

    def show_disc_comparison(self, *_):
        if self.tabs.currentWidget() is self.disc_comparison and hasattr(self, 'set_names'):
            if self.root.name != str(self.ctx.current_instance_idx):
                self.on_interface_shown()
                return
            self.disc_comparison.present(self.root, self.agent_labels, self.set_names, self.cfg,
                                         self.report if not self.preview_stale else {})

    def show_challenge(self, *_):
        if self.tabs.currentWidget() is self.challenge and hasattr(self, 'root'):
            self.challenge.present(self.root, read(snapshot_path(self.root, self.cfg), {}), read(wiki_path(), {}))
            self.challenge.read_agents.setEnabled(self.root not in self.showcase_jobs)

    def save_challenge_lineup(self):
        from time import time
        from .challenge_profiles import lineup_profiles
        token = self.edit_token()
        page = self.challenge
        if page.root != token[0] or not page.lineup:
            return
        latest = read(self.root/'challenge-current.json', {}).get(page.mode.currentText(), {})
        if latest and not latest.get('startsAt', float('inf')) <= time() < latest.get('endsAt', 0):
            latest = {}
        if latest != page.rotation:
            self.show_challenge()
            raise ValueError('本期关卡已更新，请重新比较分队')
        cfg = token[2]
        profiles, first = lineup_profiles(cfg['profiles'], page.mode.currentText(), page.rotation.get('period', ''),
                                         page.lineup, page.pool, read(wiki_path(), {}))
        if profiles is not cfg['profiles']:
            self.finish_edit(token, dict(cfg, profiles=profiles))
        self.navigation.open('我的配队')
        self.refresh_teams(first)

    def read_showcase_agents(self):
        root = self.root
        self.check_account(root)
        if root in self.showcase_jobs:
            return
        uid = config(root).get('showcaseUid')
        if not uid:
            uid = self.edit_showcase(from_roster=True)
            if not uid:
                return
        self.showcase_jobs.add(root)
        self.challenge.read_agents.setEnabled(False)
        self.challenge.status.setText('正在读取角色展柜…')

        def worker():
            from .showcase import refresh_training
            from .maintenance import exclusive
            def check():
                if int(root.name) != self.ctx.current_instance_idx or config(root).get('showcaseUid') != uid:
                    raise ValueError('账号已切换')
            try:
                with exclusive(root.parent/'_maintenance'):
                    check()
                    count = refresh_training(root, uid, check)
                message = f'展柜已读取 {count} 位代理人' if count else '角色展柜暂未展示代理人'
            except Exception as exc:
                message = str(exc) or '角色展柜暂时读不到'
            try:
                self.showcase_finished.emit(root, uid, message)
            except RuntimeError:
                pass

        Thread(target=worker, name='OhMyZZZ showcase', daemon=True).start()

    def finish_showcase(self, root, uid, message):
        self.showcase_jobs.discard(root)
        if self.root == root and int(root.name) == self.ctx.current_instance_idx:
            if self.tabs.currentWidget() is self.challenge:
                self.reload()
            self.show_challenge()
            if config(root).get('showcaseUid') == uid:
                self.challenge.status.setText(message)

    def on_interface_shown(self):
        # OneDragon caches embedded pages by navigation stack, not by account.
        root = self.ctx.get_plugin_data_dir(APP_ID) / str(self.ctx.current_instance_idx)
        if root != getattr(self, 'root', None):
            self.equip_review = None
            self.detail_recent_index = None
            self.tabs.setCurrentIndex(0)
        self.root = root
        self.last_export_attempt = None
        self.sync_button.setEnabled(root not in self.rule_updates)
        self.show_preview_job()
        self.maintenance_dir = root.parent / '_maintenance'
        self.show_maintenance()
        self.reload()
        self.refresh_new_export()
        self.export_poll.start()
        if self.preview_job is None and root in self.preview_refresh_accounts:
            self.preview_refresh_accounts.discard(root)
            if self.preview_stale:
                self.start_preview(reuse_allocations=True)
        if self.conflicts:
            self.tabs.setCurrentIndex(1)
            self.rule_search.clear()
            self.only_conflicts.setChecked(True)

    def grid(self,headers):
        table = TableWidget()
        table.setColumnCount(len(headers))
        table.verticalHeader().hide()
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def refresh_salvage(self, *_):
        if hasattr(self, 'root') and self.tabs.currentWidget() is self.salvage_page:
            from .salvage_page import latest_batch
            self.salvage_page.present(self.root, self.set_names, latest_batch(self.root))

    def set_limit(self, key, value):
        self.check_account(self.root)
        save_config(self.root, dict(self.cfg, **{key:value}), self.config_hash)
        self.reload()

    def open_reader_guide(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        url = 'https://github.com/EliNaCCa/OhMyZZZ/blob/drive-disc-manager/docs/network-reader.md'
        if self.cfg['inventorySource'] == 'cn_export':
            url = 'https://github.com/Treageer/zzz-drive-exporter/blob/v0.2.1/README.md'
        QDesktopServices.openUrl(QUrl(url))

    def choose_export_folder(self):
        from .cn_export import select_folder
        from .maintenance import exclusive
        account = self.root
        folder = QFileDialog.getExistingDirectory(self, f'为账号 {account.name} 选择读取器目录或 exports 文件夹',
                                                  str(self.cn_file.parent) if self.cn_file.parent.is_dir() else '')
        if not folder:
            return

        def same_account():
            self.check_account(account)
            if config(account)['inventorySource'] != 'cn_export':
                raise ValueError('读取方式已切换，请重新选择目录')

        with exclusive(account.parent / '_maintenance'):
            select_folder(account, folder, same_account)
        self.reload()
        self.refresh_new_export()

    def refresh_new_export(self):
        if (self.cfg['inventorySource'] != 'cn_export' or self.root.name != str(self.ctx.current_instance_idx)
                or self.preview_job is not None or self.equip_busy
                or not self.ctx.run_context.is_context_stop):
            return
        from .cn_export import file_stamp, same_export
        try:
            stamp = self.cn_file.stat()
        except OSError:
            return  # The first export may not exist yet. Manual refresh reports the actual path.
        attempt = self.root, self.cn_file, file_stamp(stamp)
        if attempt != self.last_export_attempt and not same_export(self.cn_file, stamp, self.cn_last_export):
            self.last_export_attempt = attempt
            self.start_preview(from_game=True)

    def edit_showcase(self, *, from_roster=False):
        from .showcase import valid_uid
        token = self.edit_token()
        uid, ok = QInputDialog.getText(self, '角色展柜', '游戏 UID（用于读取公开展示的代理人，留空解绑）',
                                       text=token[2].get('showcaseUid', ''))
        if ok:
            uid = uid.strip()
            if uid and not valid_uid(uid):
                raise ValueError('请填写游戏内的 UID')
            value = dict(token[2], showcaseUid=uid)
            if from_roster:
                self.check_account(token[0])
                save_config(token[0], value, token[1])
                self.reload()
            else:
                self.finish_edit(token, value)
            return uid

    def open_reader(self):
        import os
        from .reader_install import install
        from .reader_region import cn_client
        self.check_account(self.root)
        if cn_client(self.ctx) or config(self.root)['inventorySource'] != 'network':
            self.reload()
            return
        exe = install(self.root.parent/'_reader')
        os.startfile(str(exe), 'runas', cwd=str(exe.parent))

    def acknowledge_salvage(self, root, batch, inventory):
        from .salvage_audit import close_reviewed
        if root != self.root or self.ctx.current_instance_idx != int(root.name):
            raise ValueError('账号已切换，请重新打开本账号的库存记录')
        close_reviewed(root, batch, inventory)
        self.reload()

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
        previous=read(directory/'previous.json',{}).get('version')
        request=read(directory/'request.json',{})
        lines=[f'当前版本 {PLUGIN_VERSION}']
        self.rollback_button.setVisible(bool(previous))
        self.rollback_button.setToolTip(f'退出后回到 {previous}' if previous else '')
        self.cancel_queue.setVisible(bool(request))
        if request.get('action')=='rollback': lines.append('退出后回到 '+request['version'])
        elif request: lines.append('退出后更新至 '+request.get('version',''))
        last=read(directory/'last-run.json',{})
        if last.get('status') == 'failed': lines.append('上次更新失败 · '+str(last.get('message','')))
        self.maintenance_status.setText(chr(10).join(lines))

    def set_status(self, message, detail=''):
        self.status.setText(message)
        self.status.setToolTip(detail)
        self.status.setVisible(bool(message))

    def menu_action(self, menu, title, fn):
        from PySide6.QtGui import QAction
        action = QAction(title, menu)
        action.triggered.connect(lambda _=False:self.invoke(fn))
        menu.addAction(action)
        return action

    def button(self,layout,title,fn,primary=False):
        button = PrimaryPushButton(title) if primary else PushButton(title)
        layout.addWidget(button)
        button.clicked.connect(lambda _=False:self.invoke(fn))
        return button

    def invoke(self, fn):
        try:
            fn()
        except Exception as exc:
            QMessageBox.warning(self,APP_NAME,str(exc))

    @staticmethod
    def fill(table,rows):
        table.setRowCount(len(rows))
        for r,values in enumerate(rows):
            for col,value in enumerate(values):
                item=QTableWidgetItem(str(value)); item.setToolTip(str(value)); table.setItem(r,col,item)

    def reload(self):
        self.team_views.setCurrentIndex(0)
        if self.editor_account is not None:
            text = self.editor.toPlainText()
            if text != pretty(self.editor_base):
                self.editor_drafts[self.editor_account] = (self.editor_base, text)
            else:
                self.editor_drafts.pop(self.editor_account, None)
        from .reader_region import align_source
        cn = align_source(self.ctx, self.root)
        self.cfg=config(self.root); self.rules=read(self.root/'rules.json',{}); self.report=read(preview_path(self.root,self.cfg),{})
        with QSignalBlocker(self.reader):
            overseas = self.reader.findData('network')
            if cn and overseas >= 0:
                self.reader.removeItem(overseas)
            elif not cn and overseas < 0:
                self.reader.addItem('国际服 · 网络读取', userData='network')
            self.reader.setCurrentIndex(self.reader.findData(self.cfg['inventorySource']))
        self.reader_help.setVisible(self.cfg['inventorySource'] in ('network', 'cn_export'))
        self.reader_open.setVisible(self.cfg['inventorySource'] == 'network')
        source = self.cfg['inventorySource']
        cn_export = source == 'cn_export'
        self.export_folder.setVisible(cn_export)
        self.export_folder.setEnabled(self.preview_job is None)
        self.showcase_button.setText('更改展柜 UID' if self.cfg.get('showcaseUid') else '绑定角色展柜')
        self.scan_button.setText('读取仓库' if source in ('cn_export', 'network') else '扫描仓库')
        self.import_button.setVisible(source != 'network')
        self.import_button.setText('选择其他导出文件' if cn_export else '导入扫描文件')
        self.import_button.setEnabled(source != 'network' and self.preview_job is None)
        for key, spin in self.limits.items():
            with QSignalBlocker(spin): spin.setValue(self.cfg[key])
        self.upgrade_budget.setVisible(self.cfg['autoUpgrade'])
        self.config_hash = digest(self.cfg)
        if self.equip_review:
            request = self.equip_review['request']
            if (request['configuration'] != self.config_hash or request['rules'] != self.rules.get('version')
                    or str(request['account']) != self.root.name):
                self.equip_review = None
        inventory = read(snapshot_path(self.root,self.cfg), {})
        if cn_export:
            from .cn_export import source_path
            self.cn_file = source_path(self.root, inventory)
            self.cn_last_export = {k:inventory.get(k) for k in ('exportFile', 'exportStamp', 'exportWrittenNs')}
            self.export_folder.setToolTip(str(self.cn_file.parent))
        wiki = read(wiki_path(), {})
        from .roster import preview_bindings
        self.preview_bindings = preview_bindings(self.cfg['profiles'], wiki)
        self.refresh_roster(wiki, inventory)
        self.agent_labels = {row.get('code') or row.get('EN', '').replace(' ', ''):row['CHS']
                             for row in wiki.get('character', {}).values() if row.get('CHS')}
        changed_inputs = []
        if self.report:
            if cn_export:
                from .inventory_comparison import saved_scan
                scan = saved_scan(self.root)
                stamp = scan.stat().st_mtime_ns if scan.is_file() else None
                if (stamp != self.report.get('gameStamp') or
                        (stamp is not None and scan.name != self.report.get('scanFile', 'inventory.json'))):
                    changed_inputs.append('游戏扫描记录')
            if self.report.get('managerVersion') != PLUGIN_VERSION:
                changed_inputs.append('插件版本')
            if self.cfg.get('includeOwned') and self.report.get('stockRevision') != 1:
                changed_inputs.append('驱动盘留盘规则')
            if self.report.get('assignments') and not self.report.get('fullCritPriority'):
                changed_inputs.append('配装排序')
            if self.report.get('configuration') != self.config_hash:
                changed_inputs.append('配置')
            if self.report.get('rules') != self.rules.get('version'):
                changed_inputs.append('订阅规则')
            if not inventory or self.report.get('inventory') != digest(inventory):
                changed_inputs.append('库存')
            if not self.report.get('triageOnly') and self.report.get('previewBindings', {}) != self.preview_bindings:
                changed_inputs.append('配队角色对应关系')
            selected = {a['key'] for a in self.agents if a['selected']}
            if selected != {a['key'] for a in self.report.get('roster', [])}:
                changed_inputs.append('角色留盘名单')
        self.preview_stale = bool(changed_inputs)
        self.editor_account = self.root
        self.editor_base, text = self.editor_drafts.get(self.root, (deepcopy(self.cfg), pretty(self.cfg)))
        self.show_probe(read(self.root/'single-disc.json', {}))
        self.recent_result.setEnabled((self.root/'recent-discs.json').is_file())
        if text != self.editor.toPlainText(): self.editor.setPlainText(text)
        self.rows=self.report.get('discs',[])
        from .scan_observations import show_observations
        self.last_seen_title, can_recount = show_observations(self.last_seen, self.fill, self.root, not self.rows and not self.report, self.agent_labels)
        self.recount_account = self.root if can_recount else None
        for key,toggle in self.flags.items(): toggle.setValue(bool(self.cfg.get(key)), emit_signal=False)
        for key in ('protect15', 'protectNaturalCrit'):
            self.flags[key].setVisible(not self.cfg.get('includeOwned', True))
        self.auto_sync.setChecked(self.cfg['auto_sync'])
        from datetime import datetime
        updated = self.rules.get('updated')
        stamp = datetime.fromtimestamp(updated).strftime('%Y-%m-%d %H:%M') if updated else '暂无记录'
        revision = self.rules.get('version', '')
        self.rule_stamp.setText('上次更新 · '+stamp)
        self.rule_stamp.setToolTip(revision)
        self.coverage = {tuple(group[k] for k in SELECTORS):group for group in self.report.get('reserveCoverage',[])}
        conflicts=self.conflicts=current_conflicts(self.rules,self.cfg['overrides']); fields=self.rules.get('fields',{})
        self.keys=sorted((set(fields)|set(self.cfg['overrides']))-{k for k in fields if k.endswith('/known')},key=lambda k:(k not in conflicts,k))
        self.set_names = equipment_names(wiki) | self.cfg.get('aliases', {})
        self.inventory_filter.present(self.rows, self.set_names, self.root, self.fill)
        self.reserve_presets.present(self.cfg, self.set_names, fields, self.root)
        self.agent_names = {name:a['label'] for a in self.agents for name in [*a['builds'],*a['guides']]}
        self.rule_rows = {key:self.rule_row(key) for key in self.keys}
        if not conflicts: self.only_conflicts.setChecked(False)
        self.filter_rules()
        counts = self.report.get('counts', {})
        totals = [f'库存 {len(self.rows)} 张']
        upgrade_note = read(self.root/'upgrade-result.json', {}).get('note')
        if upgrade_note:
            totals.append('上次试强化 · ' + upgrade_note)
        if cn_export and inventory.get('lastChange') and not self.preview_stale:
            totals.append('上次变化 · ' + inventory['lastChange'])
        if cn_export and self.cfg.get('showcaseUid') and not self.preview_stale:
            info = inventory.get('showcase') or {}
            if info.get('uid') == self.cfg['showcaseUid']:
                count = sum(r.get('ownerSource') == 'showcase' for r in self.rows)
                stamp = datetime.fromtimestamp(info['at']).strftime('%m-%d %H:%M')
                totals.append(info.get('note') or f'展柜 {stamp} · 对上 {count} 张')
            else:
                totals.append('刷新导出文件后同步展柜')
        totals.extend(f'{name} {counts[name]}' for name in ('保留','弃置候选','已弃置','待复核') if counts.get(name))
        self.warehouse_summary.setText('    ·    '.join(totals))
        self.warehouse_summary.setVisible(bool(self.rows))
        comparison = self.report.get('gameComparison') if cn_export and not self.preview_stale else None
        self.scan_comparison.setVisible(bool(comparison))
        if comparison:
            day = datetime.fromtimestamp(comparison.get('observedAt', comparison['gameStamp'] / 1e9)).strftime('%m-%d %H:%M')
            if 'observedRows' in comparison:
                self.scan_comparison.setText(f"{day} 扫描记录 · 读到 {comparison['observedRows']} 条，词条数值对上 {comparison['measured']} 张")
            else:
                self.scan_comparison.setText(f"对照 {day} 保存的扫描 · 当时 {comparison['gameTotal']} 张，套装和词条对上 {comparison['matched']} 张")
            self.scan_comparison.setToolTip(f"按套装、号位、等级、品级和词条档数比对。逐张核实主副词条数值 {comparison['measured']} 张。")
        summary = ''
        if self.preview_stale:
            summary = '、'.join(changed_inputs)+'已更新，请重新计算预览'
        messages = [summary] if self.preview_stale else []
        if conflicts: messages.append(f'{len(conflicts)} 条规则冲突')
        messages.extend(self.report.get('issues', []) + self.report.get('warnings', []))
        message = messages[0] if messages else ''
        if len(messages) > 1:
            message += f'（另 {len(messages)-1} 项）'
        self.set_status(message, '\n'.join(messages))
        self.refresh_teams()
        self.refresh_salvage()
        from .scanner_status import scanner_status
        try:
            ready, note = scanner_status(self.ctx, int(self.root.name))
        except Exception:
            ready, note = False, '请检查仓库扫描器是否已启用'
        self.scanner_note.setText('' if ready else note)
        self.scanner_note.setVisible(not ready and self.cfg['run_scanner'] and (self.cfg['inventorySource']=='ocr' or self.cfg['applyMarkers'] or self.cfg.get('autoUpgrade')))
        receipt = read(self.root/'equip-result.json', {})
        parts = []
        if receipt.get('status')=='verified':
            parts.append(f"上次已核对 · {receipt['profile']} · {len(receipt['verified'])} 个号位")
        elif receipt.get('status') in ('checking','interrupted'):
            parts.append('上次换装待核对 · '+receipt.get('error','下次从实际穿戴情况继续'))
        self.equip_status.setText('　｜　'.join(parts))
        self.equip_status.setVisible(bool(parts) and bool(self.cfg['profiles']))
        self.refresh_open_detail()
        self.show_disc_comparison()
        self.update_calculate_buttons()

    def refresh_roster(self, wiki, inventory):
        self.owned_toggle.setChecked(self.cfg['includeOwned'])
        fields = self.rules.get('fields', {})
        self.agents = roster_catalog(inventory, fields, self.cfg, wiki, known_agents(self.root))
        def display(agent):
            selected = '已选' if agent['future'] else ('自动' if agent['selected'] else '不留盘')
            sources = []
            if agent['builds']:
                sources.append('Prydwen Builds')
            if any('guide/'+name+'/sets4' in fields for name in agent['guides']):
                sources.append('角色攻略表')
            return (agent['label'], '已拥有' if agent['owned'] else '未扫描到', selected,
                    ' / '.join(dict.fromkeys(agent['builds']+agent['guides'])),
                    '、'.join(sources) or '套装待核')
        self.fill(self.agent_table,[display(agent) for agent in self.agents])
        count = sum(a['selected'] for a in self.agents)
        owned = sum(a['owned'] for a in self.agents)
        self.agent_summary.setText(f'已选 {count} 人 · 已拥有 {owned} 人')

    def set_include_owned(self, enabled):
        try:
            self.edit_cfg(includeOwned=enabled)
        except Exception as exc:
            QMessageBox.warning(self,APP_NAME,str(exc))

    def select_roster(self, include):
        selected = self.agent_table.selectionModel().selectedRows()
        if not selected:
            return
        future = {agent['key'] for agent in self.agents if agent['future']}
        excluded = {agent['key'] for agent in self.agents if agent['excluded']}
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
            if not result and self.report.get('triageOnly') and self.report.get('keptBuildDiscs', {}).get(p['name']):
                reason = '上次配装'
            if not p.get('enabled',True):
                reason = '待选配装目标' if p.get('needsTarget') else '已停用'
                result = {}
            title = p['name']
            group = p.get('simultaneous')
            label = f"{group} · 第 {group_indices(profiles, index).index(index)+1} 位" if group else '可借盘'
            return (title,label,crit_rate_text(result),pct(result.get('relativeLossPct')),reason)
        self.fill(self.teams,[row(i,p) for i,p in enumerate(profiles)])
        for i, profile in enumerate(profiles):
            encounter = profile.get('encounter') or {}
            if encounter:
                terms = [title+encounter[key] for key, title in (('weak', '弱点 · '), ('resist', '抗性 · '), ('buffRole', '环境增益 · ')) if encounter.get(key)]
                self.teams.item(i,0).setToolTip('\n'.join([profile['name'], *terms]))
        self.teams.resizeColumnsToContents()
        visible_rows = min(4, max(1, len(profiles)))
        self.teams.setFixedHeight(self.teams.horizontalHeader().height() + self.teams.verticalHeader().defaultSectionSize() * visible_rows + 4)
        if profiles:
            index = next((i for i,p in enumerate(profiles) if p['name']==selected),0)
            self.teams.selectRow(index)
        self.show_build()

    def show_build(self):
        index = self.teams.currentRow()
        profiles = self.cfg['profiles']
        selected = profiles[index] if 0 <= index < len(profiles) else {}
        self.profile_menu.setEnabled(bool(selected) and not self.equip_busy)
        self.window_action.setEnabled(bool(selected) and not selected.get('needsTarget'))
        self.refresh_target_button.setText('选择配装目标' if selected.get('needsTarget') else '同步角色目标')
        self.refresh_target_button.setEnabled(bool(selected.get('sourceId') or selected.get('needsTarget')) and not self.equip_busy)
        ready = not self.preview_stale and selected.get('enabled',True) and self.report.get('assignments',{}).get(selected.get('name'),{}).get('ids')
        reviewing = self.equip_review and self.equip_review['request']['name'] == selected.get('name')
        self.apply_button.setText('查看换装变化' if reviewing else '一键换装')
        from .loadout import fielded_profiles, missing_target_note
        lineup = fielded_profiles(self.cfg, selected['name']) if selected and selected.get('enabled', True) else []
        pending = missing_target_note(self.cfg, selected)
        unbound = [p['name'] for p in lineup if not p.get('agentKey') or p['agentKey'] != self.preview_bindings.get(p['name'])]
        stats_only = self.cfg['inventorySource'] == 'cn_export'
        source = self.report.get('assignments', {}).get(selected.get('name'), {}).get('baselineSource')
        baseline = {'scan':'上次扫描', 'export':'导出配装', 'showcase':'角色展柜', 'unknown':'原配装'}.get(source, '原配装' if stats_only else '当前装备')
        self.panel_stats.horizontalHeaderItem(3).setText(baseline)
        self.panel_stats.horizontalHeaderItem(5).setText('变化')
        self.slots.horizontalHeaderItem(4).setText('穿戴者')
        self.apply_button.setEnabled(bool(ready or reviewing) and not pending and not unbound and not self.equip_busy and self.preview_job is None)
        binding_note = pending or ('请核对扫描器角色名 · ' + '、'.join(unbound) if unbound else '')
        hint = '查看要换的盘' if ready else '先计算配装'
        self.apply_button.setToolTip(binding_note or ('对照上次确认和游戏重扫的配装' if reviewing else hint))
        self.fill(self.slots,[])
        self.fill(self.panel_stats,[])
        self.panel_note.setText('暂无面板对比'); self.panel_note.show()
        self.score_detail.clear()
        self.teams.setVisible(bool(profiles))
        self.build_tabs.setVisible(bool(selected))
        self.build_tabs.selector.show()
        self.equip_status.setVisible(bool(profiles) and bool(self.equip_status.text()))
        self.team_score.show()
        self.team_score.setText('在「配装器」里保存角色目标，再点「导入方案」。')
        if index < 0 or index >= len(self.cfg['profiles']): return
        profile = self.cfg['profiles'][index]
        result = self.report.get('assignments',{}).get(profile['name'],{})
        party = self.squad_text(profile)
        def show_slots(rows):
            from .reserve_presets import STAT_NAMES
            self.fill(self.slots,[(r['slot'],self.set_names.get(r['set'],r['set']),STAT_NAMES.get(r['main'],r['main']),r['level'],
                                   preview_owner(r), '、'.join(r['reasons'])) for r in rows])
            self.slots.resizeColumnsToContents()

        saved = self.report.get('keptBuildDiscs', {}).get(profile['name'], ())
        if saved and not result and self.report.get('triageOnly') and profile.get('enabled', True) and not profile.get('needsTarget'):
            keys = set(saved)
            rows = [r for r in self.rows if '已保存配装用盘' in r['reasons'] and is_pinned(r, keys)]
            show_slots(sorted(rows, key=lambda r: int(r['slot'])))
            self.build_tabs.setCurrentIndex(0)
            self.build_tabs.selector.hide()
            status = f'上次配装 · 仓库记录里有 {len(rows)} 张方案用盘'
            self.team_score.setText('　·　'.join(filter(None, (party, status))))
            return
        if not profile.get('enabled',True) or not result.get('ids'):
            self.build_tabs.hide()
            status = '方案已停用' if not profile.get('enabled',True) else result.get('error') or '点击「计算配装」查看结果。'
            if profile.get('needsTarget'):
                status = '在「管理方案 → 选择配装目标」为这位代理人选一套配装。'
            self.team_score.setText('　·　'.join(filter(None, (party, status))))
            return
        pieces = {r.get('buildId', r['fp']): r for r in self.rows}
        rows = [pieces[piece] for piece in result['ids'] if piece in pieces]
        show_slots(rows)
        details = [profile['name']]
        gain_text = ''
        if binding_note:
            details.append(binding_note)
        if result.get('panelRows'):
            def value(v, signed=False):
                if v is None: return '—'
                return f'{v:+g}' if signed else f'{v:g}'
            self.fill(self.panel_stats, [(label, kind, value(goal), value(previous), value(chosen), value(delta,True), value(gap,True))
                                        for label,kind,goal,previous,chosen,delta,gap in result['panelRows']])
            self.panel_stats.resizeColumnsToContents()
            self.panel_note.hide()
            details.append(result.get('panelMode','职业面板'))
            if not result.get('currentPanel'):
                details.append('还缺原配装的穿戴记录')
        if 'outputScore' in result:
            details += [f"所选输出 {result.get('rawOutput', result['outputScore'])}", f"候选最高 {result.get('rawBestOutput', result['bestOutput'])}"]
            if profile.get('simultaneous') and 'rawAvailableBestOutput' in result:
                details.append(f"本轮可选最高 {result['rawAvailableBestOutput']} · 所选与其相差 {result['availableLossPct']:.6g}%")
            from .optimizer import number
            worn = result.get('wornOutput')
            if worn is not None and number(worn) > 0:
                change = (number(result.get('rawOutput',result['outputScore'])) / number(worn) - 1) * 100
                details.append(f'{baseline}输出 {worn} · 方案变化 {float(change):+.6g}%')
                gain_text = (f'相比{baseline}，预计输出 {float(change):+.3g}%' if change
                             else f'与{baseline}的预计输出相同')
            else:
                details.append('原配装输出待核对')
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
            for buff in window.get('buffs', []):
                from .window_editor import STAT_NAMES as WINDOW_STAT_NAMES
                member = buff.get('member')
                who = profile['request']['characterName'] if member == '$self' else member
                source = ' · '.join(filter(None, (who, buff['source'])))
                mode = {'additional':'生效', 'included':'已计入', 'inactive':'本窗口不生效', 'off':'未满足队伍条件'}[buff['appliedMode']]
                details.append(f"  {source} · {WINDOW_STAT_NAMES[buff['stat']]} {number(buff['value']):+g} · {mode}")
        scope = result.get('search',{})
        if scope:
            details.append(f"初选 {scope['candidateBuilds']} 套")
            initial = scope.get('initialScope')
            if initial:
                details.append(f"初选送入 {initial['submitted']} 张盘，配装器 {initial['calls']} 次请求共评估 {initial['evaluated']:,} 套次")
                if initial['atReturnLimit']:
                    details.append('已触及单次候选返回上限')
            if profile.get('simultaneous') and 'availableBuilds' in scope:
                details.append(f"分盘时可选 {scope['availableBuilds']} 套")
            if scope.get('reservationAttempts') and result.get('reason'):
                details.append(result['reason'])
            if scope.get('limited'):
                details.append('已达到联合搜索上限')
        if self.preview_stale:
            details.append('上次计算结果 · 请用当前库存和规则重新计算')
        self.score_detail.setPlainText(chr(10).join(details))
        self.team_score.setText('　·　'.join(filter(None, (party, gain_text, binding_note))))
        self.team_score.setVisible(bool(self.team_score.text()))

    def squad_text(self, profile):
        if 'mindscape' not in profile and not profile.get('squad'):
            return ''
        lead = self.agent_labels.get(profile.get('agentKey')) or profile['request']['characterName']
        members = [dict(agent=lead, mindscape=profile.get('mindscape')), *profile.get('squad', [])]
        return ' / '.join(m['agent'] if m.get('mindscape') is None else f"{m['agent']} {m['mindscape']}影" for m in members)

    def edit_squad(self):
        index = self.teams.currentRow()
        if index < 0: return
        token = self.edit_token()
        profile = token[2]['profiles'][index]
        lead = self.agent_labels.get(profile.get('agentKey')) or profile['request']['characterName']
        self.squad_edit = token, index
        from .challenge import account_pool, agent_labels
        from .roster import agent_id
        wiki = read(wiki_path(), {})
        aliases = agent_labels(wiki)
        def canonical(name):
            return aliases.get(agent_id(name), name)
        inventory = read(snapshot_path(self.root, self.cfg), {})
        pool = account_pool(self.root, inventory, wiki, labels=aliases)
        for agent in self.agents:
            if agent['owned']:
                pool.setdefault(canonical(agent['label']), {})
        ranks = {name:row['mindscape'] for name,row in pool.items()
                 if type(row.get('mindscape')) is int and 0 <= row['mindscape'] <= 6}
        choices = pool or self.agent_labels.values() or [a['label'] for a in self.agents]
        self.squad_editor.present(profile, choices, canonical(lead), canonical=canonical, ranks=ranks)
        self.team_views.setCurrentWidget(self.squad_editor)

    def save_squad(self):
        token, index = self.squad_edit
        self.edit_profile_fields(token, index, self.squad_editor.values())
        self.team_views.setCurrentIndex(0)

    @staticmethod
    def crit_summary(result):
        effective = '有效暴击 ' + crit_rate_text(result)
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
                profiles[index+1].pop('lineupId', None)
                profiles[index+1].pop('encounter', None)
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

    def toggle_profile(self):
        index = self.teams.currentRow()
        if index < 0: return
        profile = self.cfg['profiles'][index]
        enabled = not profile.get('enabled',True)
        if profile.get('needsTarget'):
            self.refresh_profile()
            return
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
        from .manual_equip import SelectionChanged, confirmation, run_confirmed
        account, root = self.ctx.current_instance_idx, self.root
        index = self.teams.currentRow()
        if self.equip_busy or self.preview_job is not None or not self.ctx.run_context.is_context_stop or str(account) != root.name:
            raise ValueError('请在当前账号的一条龙空闲时应用配队')
        if not 0 <= index < len(self.cfg['profiles']):
            return
        name = self.cfg['profiles'][index]['name']
        review = self.equip_review if self.equip_review and self.equip_review['request']['name'] == name else None
        rules_version = read(root/'rules.json',{}).get('version')
        if (review['request']['rules'] if review else self.report.get('rules')) != rules_version:
            self.reload()
            raise ValueError('规则已更新，请重新计算后应用配队')
        from .loadout import verify_bindings
        current_cfg, wiki = config(root), read(wiki_path(), {})
        verify_bindings(current_cfg, name, wiki)
        sets = equipment_names(wiki) | current_cfg.get('aliases',{})
        if review:
            request, targets = review['request'], review['after']
            if request['configuration'] != digest(current_cfg) or request['account'] != account:
                self.reload()
                raise ValueError('配队配置已变化，请重新计算')
        else:
            request, targets = confirmation(account, name, current_cfg, self.report, read(snapshot_path(root,current_cfg)), preview_only=True, names=sets)
        dialog = QDialog(self); dialog.setWindowTitle('查看换装变化' if review else '应用当前配队'); dialog.resize(760,440)
        panel = QVBoxLayout(dialog)
        from .stat_reading import display_values
        agents = {p['name']:p.get('request',{}).get('characterName') for p in current_cfg['profiles']}
        owner_names = {a.get('code') or str(a.get('EN', '')).replace(' ', ''):a['CHS']
                       for a in wiki.get('character', {}).values() if isinstance(a.get('CHS'), str)}
        if review:
            from .equip_review import changed_slots
            changes = changed_slots(review['before'], targets, sets, owner_names)
            panel.addWidget(BodyLabel(f'{name} · {len(set(row[0] for row in changes))} 个号位有变化'))
            table = self.grid(['号位','变动项','上次确认','游戏重扫']); self.fill(table, changes)
            table.resizeColumnsToContents(); panel.addWidget(table)
        panel.addWidget(BodyLabel(f'{name} · {len(targets)} 个号位' if not review else '最新配装'))
        stat_header = '主副词条（换算值）' if not review and current_cfg['inventorySource'] in ('network', 'cn_export') else '主副词条'
        table = self.grid(['方案','目标角色','号位','套装',stat_header,'当前装备者'])
        self.fill(table,[(t['profile'], agents.get(t['profile']) or t['agent'], t['slot'], sets.get(t['set'],t['set']),
                          display_values(t['observedValues']), owner_names.get(owner_label(t), owner_label(t))) for t in targets])
        table.resizeColumnsToContents(); panel.addWidget(table)
        actions = QHBoxLayout(); panel.addLayout(actions); actions.addStretch()
        back = self.button(actions,'返回原预览' if review else '返回',dialog.reject); back.setDefault(True)
        action_label = '核对并换装' if current_cfg['inventorySource'] == 'cn_export' else '重扫并换装'
        self.button(actions,action_label,dialog.accept).setAutoDefault(False)
        if not dialog.exec():
            if review:
                self.equip_review = None
                self.reload(); self.refresh_teams(name)
            return
        self.check_account(root)
        self.equip_review = None
        self.equip_busy = True; self.apply_button.setEnabled(False)
        self.equip_status.setText(f'正在为「{name}」换装')
        self.equip_status.show()
        def worker():
            error = ''
            review_data = None
            try:
                run_confirmed(self.ctx, request)
            except SelectionChanged as change:
                review_data = dict(request=change.current, before=targets, after=change.targets)
            except Exception as exc:
                error = str(exc)
            self.equip_finished.emit(str(root),name,error,review_data)
        Thread(target=worker, name='OhMyZZZ equip', daemon=True).start()

    def finish_equip(self, root, name, error, review=None):
        self.equip_busy = False
        if str(self.root) == root and str(self.ctx.current_instance_idx) == self.root.name:
            self.equip_review = review
            self.reload(); self.refresh_teams(name)
            if review or error:
                message = error or ('重扫后配装有变化' if self.equip_review else '配队配置或规则已更新，请重新计算')
                self.equip_status.setText(f'{name} · {message}')
                self.equip_status.show()
        else:
            self.on_interface_shown()

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
            if checked and key in ('applyMarkers', 'autoUpgrade'):
                cfg['run_scanner'] = True
            if key=='run_scanner' and not checked:
                cfg.update(applyMarkers=False,autoUpgrade=False,fieldProfile='')
            from .validation import validate
            validate(cfg)
            save_config(self.root,cfg,self.config_hash)
            if key in ('rotateSpareLocks','protect15','protectNaturalCrit') and snapshot_path(self.root,self.cfg).is_file():
                self.refresh_saved_preview()
                return
            self.reload()
        except Exception as exc:
            self.reload()
            QMessageBox.warning(self,APP_NAME,str(exc))

    def load_inventory(self):
        account = self.root
        source = self.cfg['inventorySource']
        title = (f'选择账号 {account.name} 的国服 ZOOD 导出文件' if source == 'cn_export'
                 else '选择属于当前账号的 ZOD 扫描')
        path,_=QFileDialog.getOpenFileName(self,title,'','JSON (*.json)')
        if path:
            self.check_account(account)
            if config(account)['inventorySource'] != source:
                raise ValueError('读取方式已切换，请重新选择文件')
            self.start_preview(import_path=path)

    def start_probe(self, *, recent_count=None):
        from .disc_probe import probe
        account, root = self.ctx.current_instance_idx, self.root
        if int(root.name) != account or not self.ctx.run_context.is_context_stop:
            raise ValueError('请在当前账号的一条龙空闲时读盘')
        self.probe_button.setEnabled(False)
        self.recent_button.setEnabled(False)
        self.clear_recent_table()
        self.probe_note.setText('正在按获得顺序读盘…' if recent_count else '正在读取这张盘…'); self.probe_note.show()
        def worker():
            try:
                record = probe(self.ctx, root, account, recent_count=recent_count)
            except Exception as exc:
                record = dict(status='failed', error=str(exc))
            self.probe_finished.emit(dict(record, recent=recent_count is not None), str(root))
        Thread(target=worker, name='OhMyZZZ single disc', daemon=True).start()

    def finish_probe(self, record, root):
        self.probe_button.setEnabled(True)
        self.recent_button.setEnabled(True)
        if str(self.root) == root and self.root.name == str(self.ctx.current_instance_idx):
            if record.get('recent'):
                if record.get('status') != 'verified':
                    self.clear_recent_table()
                    self.probe_note.setText(record.get('error', '读取中断')); self.probe_note.show()
                    return
                self.recent_result.setEnabled(True)
                self.show_recent(record)
                return
            self.show_probe(record)
            self.show_reserve()
            if self.tabs.currentWidget() is self.detail and self.detail_selection[2]:
                self.refresh_open_detail()

    def show_recent(self, record=None):
        from datetime import datetime
        from .recent_discs import describe
        self.check_account(self.root)
        if record is None:
            record = read(self.root/'recent-discs.json', {})
        if record.get('status') != 'verified' or str(record.get('account')) != self.root.name:
            raise ValueError('当前账号还没有新盘记录')
        self.recent_record = record
        rows = record.get('rows')
        if rows is None:
            rows = describe(self.root, record, read(wiki_path(), {}))
        stamp = datetime.fromtimestamp(record['finished']).strftime('%m-%d %H:%M')
        basis = ' · 参考' + rows[0]['basis'] if rows and rows[0].get('basis') else ''
        self.probe_note.setText(f'{stamp} · 当前筛选下最近获得的 {len(rows)} 张' + basis)
        self.probe_note.show()
        table = self.recent_table
        table.horizontalHeaderItem(1).setText('读取时的建议' if record.get('rows') is not None else '当前建议')
        self.fill(table, [(f"{r['name']} {r['slot']} 号 +{r['level']}", r['suggestion'], r['main'], r['subs']) for r in rows])
        for index, row in enumerate(rows):
            reasons = [row['advice'], row.get('reference')]
            table.item(index, 1).setToolTip('\n'.join(filter(None, reasons)))
        table.setFixedHeight(table.horizontalHeader().sizeHint().height() +
                             table.verticalHeader().defaultSectionSize() * min(len(rows), 3) + 2 * table.frameWidth())
        table.scrollToTop()
        table.setVisible(bool(rows))

    def clear_recent_table(self):
        self.recent_table.hide()
        self.recent_table.setRowCount(0)
        self.recent_record = None

    def open_recent_detail(self, index):
        self.check_account(self.root)
        record = self.recent_record
        if record and 0 <= index < len(record['discs']):
            self.present_detail(None, recent=record, index=index)

    def back_from_disc(self):
        self.navigation.open('仓库预览')
        index = self.detail_recent_index
        self.detail_recent_index = None
        if index is None or self.detail_selection[0] != self.root or self.root.name != str(self.ctx.current_instance_idx):
            return
        record = read(self.root/'recent-discs.json', {})
        if record.get('status') == 'verified' and str(record.get('account')) == self.root.name:
            self.show_recent(record)
            if index < len(record['discs']) and self.detail_observation == (record['finished'], digest(record['discs'][index])):
                self.recent_table.selectRow(index)

    def show_probe(self, record):
        self.clear_recent_table()
        self.probe_record = record
        verified = record.get('status') == 'verified'
        self.observation_button.setEnabled(verified)
        self.detail_button.setEnabled(verified or self.table.currentRow() >= 0)
        error = record.get('error', '')
        self.probe_note.setToolTip('')
        self.probe_note.setText(error)
        self.probe_note.setVisible(bool(error))

    def configure_optimizer(self):
        from .optimizer import loopback_json
        token = self.edit_token()
        url, ok = QInputDialog.getText(self, '配装器地址', '留空使用 OhMyZZZ 托管，或填写自用配装器的本机 HTTP 地址', text=self.cfg['optimizer_url'])
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
        self.check_account(self.root)
        self.optimizer_page.start(self.root, self.optimizer_session())

    def optimizer_tab_changed(self, *_):
        if self.tabs.currentWidget() is self.optimizer_page:
            self.invoke(self.open_optimizer)
        else:
            self.optimizer_page.stop()
            self.target_edit = None
            self.optimizer_page.import_button.setText('导入方案')

    def on_interface_hidden(self):
        self.export_poll.stop()
        if self.tabs.currentWidget() is self.optimizer_page:
            self.navigation.open('我的配队')

    def import_profile(self):
        from .optimizer import saved_targets
        token = self.edit_token()
        embedded = self.tabs.currentWidget() is self.optimizer_page
        if embedded:
            if self.optimizer_page.account != self.root or self.optimizer_page.endpoint is None:
                raise ValueError('请先打开当前账号的配装器')
            choices = saved_targets(self.optimizer_page.endpoint)
            if self.target_edit is not None:
                old_token, index = self.target_edit
                self.check_account(old_token[0])
                if token[1] != old_token[1]:
                    raise ValueError('配队已更新，请返回「我的配队」重新选择目标')
                self.choose_lineup_target(token, index, token[2]['profiles'][index], choices=choices)
                return
        else:
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
        name,ok=QInputDialog.getText(self,'保存配队','方案名称',text=source['agent']+' · 常用队')
        if not ok: return
        if not name.strip() or any(p['name']==name.strip() for p in token[2]['profiles']):
            raise ValueError('请输入新的方案名称')
        simultaneous,ok=QInputDialog.getText(self,'同时出场','同时上场的角色填写相同分组，互斥配队可留空')
        if not ok: return
        profile = dict(name=name.strip(),agentKey=agent_key,request=source['request'],
                       simultaneous=simultaneous.strip(),sourceId=source['sourceId'])
        from .allocation import group_conflict
        peers = [p for p in token[2]['profiles'] if p.get('simultaneous') == profile['simultaneous']]
        conflict = group_conflict([*peers, profile])
        if conflict: raise ValueError(conflict)
        if embedded:
            self.navigation.open('我的配队')
        self.finish_edit(token, dict(token[2], profiles=[*token[2]['profiles'],profile]))
        self.refresh_teams(profile['name'])

    def choose_lineup_target(self, token, index, profile, *, choices=None):
        from .allocation import group_conflict
        from .challenge_profiles import bind_target
        from .optimizer import saved_targets
        from .role_panel import MODE_NAMES
        from .roster import same_agent
        wiki = read(wiki_path(), {})
        actor = profile['request']['characterName']
        in_optimizer = choices is not None
        if choices is None:
            with self.optimizer_session() as endpoint:
                choices = saved_targets(endpoint)
        options = [target for target in choices if same_agent(wiki, target['agent'], actor)]
        if not options:
            if in_optimizer:
                raise ValueError(f'请先在配装器保存{actor}的角色目标')
            self.target_edit = token, index
            self.optimizer_page.import_button.setText(f'用于{actor}的配装')
            self.navigation.open('配装器')
            return
        source = options[0]
        if len(options) > 1:
            labels = []
            for i, target in enumerate(options, 1):
                mode = target['request'].get('mode') or ''
                labels.append(f"{i}. {target['agent']} · {MODE_NAMES.get(mode, mode or '默认目标')}")
            label, ok = QInputDialog.getItem(self, actor, '选择配装目标', labels, 0, False)
            if not ok:
                return
            source = options[labels.index(label)]
        updated = bind_target(profile, source, wiki)
        profiles = list(token[2]['profiles'])
        profiles[index] = updated
        conflict = group_conflict(p for p in profiles if p.get('simultaneous') == updated.get('simultaneous'))
        if conflict:
            raise ValueError(conflict)
        if in_optimizer:
            self.navigation.open('我的配队')
        self.finish_edit(token, dict(token[2], profiles=profiles))
        self.refresh_teams(updated['name'])

    def refresh_profile(self):
        from .optimizer import refreshed_profile, saved_targets
        index = self.teams.currentRow()
        if index < 0 or self.equip_busy:
            return
        token = self.edit_token()
        profile = token[2]['profiles'][index]
        if profile.get('needsTarget'):
            self.choose_lineup_target(token, index, profile)
            return
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
    def build_preview(account, wiki, import_path, reuse_allocations, *, builds=False):
        from .maintenance import Deferred, exclusive
        try:
            # The game worker holds this same lock from its fresh scan through marking/equip.
            with exclusive(account.parent/'_maintenance'):
                if import_path is not None:
                    import_inventory(account,import_path)
                elif config(account)['inventorySource'] == 'ocr' and not (account/'inventory.json').is_file():
                    from .store import recount_inventory
                    recount_inventory(account)
                preview(account,wiki,refresh=False,reuse_allocations=reuse_allocations,triage_only=not builds)
        except Deferred as exc:
            raise ValueError('插件任务或维护正在运行，请结束后再计算仓库预览。') from exc

    def update_calculate_buttons(self):
        root = getattr(self, 'root', None)
        has_inventory = root is not None and snapshot_path(root, getattr(self, 'cfg', {})).is_file()
        can_recount = (root is not None and getattr(self, 'cfg', {}).get('inventorySource') == 'ocr'
                       and not has_inventory and getattr(self, 'recount_account', None) == root)
        can_calculate = (has_inventory or can_recount) and self.preview_job is None
        self.calculate_buttons[0].setEnabled(can_calculate)
        self.calculate_buttons[1].setEnabled(bool(can_calculate and getattr(self, 'cfg', {}).get('profiles')))
        for button in self.calculate_buttons:
            button.setToolTip('按上次保存的盘位重新核对仓库' if can_recount else
                              '' if has_inventory else f'请先在「仓库」点「{self.scan_button.text()}」')

    def show_preview_job(self, error=''):
        busy = self.preview_job is not None
        for button in (self.scan_button, self.import_button):
            button.setEnabled(not busy)
        self.import_button.setEnabled(not busy and getattr(self, 'cfg', {}).get('inventorySource') != 'network')
        self.update_calculate_buttons()
        message = error
        if busy:
            message = (f'正在扫描账号 {self.preview_job.name} 的游戏仓库…' if self.scanning_game
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
        if self.cfg['inventorySource'] == 'ocr':
            ready, reason = scanner_status(self.ctx, account)
            if not ready:
                raise ValueError(reason)
        self.start_preview(from_game=True)

    def start_preview(self, *, import_path=None, reuse_allocations=False, from_game=False, builds=False):
        account = self.root
        self.check_account(account)
        if self.preview_job is not None:
            return
        wiki = wiki_path()
        source = self.cfg['inventorySource']
        network = from_game and source == 'network'
        cn_import = source == 'cn_export' and (from_game or import_path is not None)
        self.preview_job = account
        self.scanning_game = from_game and source == 'ocr'
        self.preview_refresh_accounts.discard(account)
        self.show_preview_job()
        self.apply_button.setEnabled(False)

        def worker():
            error = ''
            try:
                if network or cn_import:
                    from .maintenance import exclusive
                    from .service import rule_snapshot
                    def same_account():
                        if self.ctx.current_instance_idx != int(account.name) or config(account)['inventorySource'] != source:
                            raise ValueError('账号或读取方式已切换，请重新读取')
                    with exclusive(account.parent/'_maintenance'):
                        pinned = rule_snapshot(account, refresh=False)
                        if pinned[0]['inventorySource'] != source:
                            raise ValueError('读取方式已切换，请重新读取')
                        if cn_import:
                            from .cn_export import refresh
                            refresh(account, same_account, path=import_path, showcase_uid=pinned[0].get('showcaseUid', ''), wiki=read(wiki, {}))
                        else:
                            from .network_reader import capture
                            capture(account, same_account)
                        preview(account, wiki, snapshot=pinned, triage_only=True)
                elif from_game:
                    from .manual_equip import run_scan_preview
                    run_scan_preview(self.ctx, int(account.name))
                else:
                    self.build_preview(account, wiki, import_path, reuse_allocations, builds=builds)
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
        if snapshot_path(account,config(account)).is_file():
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
        self.edit_cfg(pins=toggle_pin(row, self.cfg['pins']))

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
            if probe and self.detail_recent_index is not None:
                record = read(self.root/'recent-discs.json', {})
                index = self.detail_recent_index
                if (record.get('status') == 'verified' and str(record.get('account')) == self.root.name
                        and index < len(record['discs']) and self.detail_observation == (record['finished'], digest(record['discs'][index]))):
                    self.present_detail(None, recent=record, index=index)
                    self.detail.tabs.setCurrentIndex(tab)
                    return
            elif probe and self.detail_observation == digest(self.probe_record):
                self.present_detail(None)
                self.detail.tabs.setCurrentIndex(tab)
                return
            if not probe and previous.get('exportUid'):
                match = next(((i, r) for i, r in enumerate(self.rows) if r.get('exportUid') == previous['exportUid']), None)
                if match:
                    index, row = match
                    if not self.table.isRowHidden(index):
                        self.table.selectRow(index)
                    self.present_detail(row)
                    self.detail.tabs.setCurrentIndex(tab)
                    return
            elif not probe and not previous.get('ambiguous'):
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
        self.set_status('原盘读数已变化，请重新选择。')

    def present_detail(self, row, *, recent=None, index=None):
        from .disc_trace import probe_trace
        probe = row is None
        self.detail_recent_index = index
        report = self.report
        if probe:
            wiki = read(wiki_path(), {})
            from .potential import saved_comparison
            comparison = saved_comparison(self.root, self.cfg, self.rules)
            observation = dict(status='verified', disc=recent['discs'][index]) if recent else self.probe_record
            row, report = probe_trace(observation, self.cfg, self.rules, wiki, known_agents(self.root), comparison=comparison)
            self.detail_observation = (recent['finished'], digest(observation['disc'])) if recent else digest(observation)
            if row.get('trialUse') and not comparison:
                from .recent_discs import observed_peer
                example = observed_peer(row, read(self.root/'scan-observations.json', {}), self.set_names)
                if example:
                    row['explanation'].append(example)
        self.detail_selection = (self.root, row, probe)
        self.detail.present(row, report, set_name=self.set_names.get(row['set'],row['set']), probe=probe, pinned=is_pinned(row, self.cfg['pins']),
                            set_names=self.set_names,
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
        self.navigation.open('盘详情')

    def edit_detail_policy(self, action):
        try:
            root, row, _ = self.detail_selection
            if root != self.root or root.name != str(self.ctx.current_instance_idx):
                self.on_interface_shown(); return
            if action == 'pin':
                if row['marked']: return
                self.edit_cfg(pins=toggle_pin(row, self.cfg['pins']))
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
        has_rows = bool(self.rows)
        self.table.setVisible(selected)
        self.empty.setVisible(not selected)
        self.inventory_filter.setVisible(has_rows)
        if not selected:
            self.empty_title.setText('没有匹配的驱动盘' if has_rows else ('仓库里没有驱动盘' if self.report else '还没有仓库预览'))
            hint = '扫描游戏仓库，或从「更多」导入扫描文件'
            if self.cfg['inventorySource'] == 'cn_export':
                hint = '在「运行设置」选择导出目录，读取器生成文件后再来看仓库'
            elif self.cfg['inventorySource'] == 'network':
                hint = '连接国际服读取器后，点「读取仓库」'
            self.empty_hint.setText('换个筛选条件试试' if has_rows else ('下次读取会更新库存' if self.report else hint))
            if not has_rows and getattr(self, 'last_seen_title', ''):
                self.empty_title.setText(self.last_seen_title)
                self.empty_hint.setText('上次扫描的记录，仅供查看')
        self.pin_button.setEnabled(selected and not self.rows[index]['marked'])
        self.quota_button.setEnabled(selected)
        self.detail_button.setEnabled(selected or self.probe_record.get('status')=='verified')
        self.detail_button.setText('盘详情' if selected or self.probe_record.get('status')!='verified' else '上次单盘读数')
        self.stat_info.hide()
        selected_row = self.rows[index] if selected else {}
        self.restore_unlock.setText('取消这张盘的撤销弃置保护' if selected_row.get('manualKeep') else '取消这张盘的手动解锁保护')
        self.restore_unlock.setVisible(bool(selected_row.get('manualUnlock') or selected_row.get('manualKeep')) and not selected_row['marked'])
        self.reserve_info.hide()
        if not 0 <= index < len(self.rows): return
        from .stat_reading import display_values
        if selected_row.get('observedValues'):
            self.stat_info.setText(display_values(selected_row['observedValues']))
            self.stat_info.show()
        group = self.coverage.get(tuple(self.rows[index][k] for k in SELECTORS))
        if not group: return
        parts = []
        for use in group['purposes']:
            cap = '不限' if use['count'] is None else str(use['count'])
            parts.append(f"{use['purpose']} {use['covered']}/{cap}")
        if group.get('fixedExtra'):
            parts.append(f"另有 {group['fixedExtra']} 张旧备用保持锁定")
        self.reserve_info.show()
        self.reserve_info.setText(f"参与备用筛选 {group['eligible']} 张 · 留作备用 {group['kept']} 张　｜　"+'　'.join(parts))

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
        self.set_status('正在更新此账号的订阅来源…')

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
            self.set_status(f'订阅更新失败，{fallback} · {error}')
            return
        try:
            if snapshot_path(root,config(root)).is_file():
                self.refresh_saved_preview()
            else:
                self.reload()
            if self.conflicts:
                self.tabs.setCurrentIndex(1)
                self.rule_search.clear()
                self.only_conflicts.setChecked(True)
        except Exception as exc:
            self.reload()
            self.set_status('订阅已更新，预览待重新计算 · '+str(exc))

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
        title,ok=QInputDialog.getItem(self,'编辑规则','怎么保留',list(modes),list(modes.values()).index(old['mode']),False)
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
            initial = dialog.values()
            if not dialog.exec(): return
            chosen = dialog.values()
            # Existing guide rules can store the original sheet text for the same checkboxes.
            override = dict(mode=mode, values=selected if chosen == initial and mode == old['mode'] else chosen)
        elif mode != 'inherit' and key.startswith(('build/', 'guide/')) and field in ('sets4','sets2'):
            from .set_rule_editor import SetRuleEditor
            matrix_sets = {k.split('/')[1] for k in self.keys if k.startswith('matrix/') and k.count('/') == 2}
            choices = {k:self.set_names.get(k,k) for k in matrix_sets | self.set_names.keys()}
            dialog = SetRuleEditor(self, row[0]+' · '+row[1], mode, choices, seen_remote or [], selected)
            if not dialog.exec(): return
            override = dict(mode=mode, values=dialog.values())
        elif mode != 'inherit':
            text,ok=QInputDialog.getMultiLineText(self,'编辑规则','每行一项',text='\n'.join(selected))
            if not ok: return
            override = dict(mode=mode, values=[v.strip() for v in text.splitlines() if v.strip()])
        if self.root != account or str(self.ctx.current_instance_idx) != account.name:
            self.on_interface_shown()
            raise ValueError('账号已切换，请在当前账号重新选择字段。')
        try:
            changed = save_field_override(account, key, seen_remote=seen_remote, seen_local=seen_local, override=override)
        finally:
            self.reload()
        if changed and snapshot_path(account,config(account)).is_file():
            self.refresh_saved_preview()

    def rule_row(self, key):
        from .disc_detail_page import cell_change_text, field_label, rule_values
        parts = key.split('/')
        source = parts[0]
        subject = '/'.join(parts[1:-1]) or key
        localized = (self.set_names if source=='matrix' else self.agent_names).get(subject)
        if localized and localized != subject:
            subject = localized
        tabs = field_tabs(self.rules, key) or [{'matrix':'Set+Main','build':'Prydwen Builds','guide':'角色攻略表'}.get(source,source)]
        remote = self.rules.get('fields',{}).get(key)
        tab_label = ('原页签 · ' if key in self.rules.get('formerOrigins', {}) else '') + '、'.join(tabs)
        conflict = self.conflicts.get(key)
        override = self.cfg['overrides'].get(key) or {}
        mode = override.get('mode','inherit')
        def display(values, *, show_accepted=False):
            return rule_values(key, values, self.set_names, accepted=show_accepted)
        local = '继承上游' if mode=='inherit' else ('额外接受 ' if mode=='add' else '只接受 ') + display(override['values'], show_accepted=True)
        changes = (conflict or {}).get('cellChanges')
        old_text = display(conflict['old']) if conflict else '—'
        new_text = display(remote)
        if changes:
            old_text, new_text = cell_change_text(changes, 0), cell_change_text(changes, 1)
        return [('⚠ ' if conflict else '')+subject, field_label(key), tab_label,
                old_text, new_text, local, display(effective(remote,override or None), show_accepted=True)]

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
        for index, key in enumerate(self.visible_keys):
            if self.conflicts.get(key, {}).get('cellChanges'):
                from .disc_detail_page import rule_values
                remote = self.rules.get('fields', {}).get(key)
                self.fields.item(index, 4).setToolTip('订阅推荐　' + rule_values(key, remote, self.set_names))
            else:
                self.fields.item(index, 6).setToolTip('上游原文　' + self.rule_rows[key][4])
        self.rule_total.setText(f'{len(self.visible_keys)} / {len(self.keys)} 条')
        self.select_rule()

    def select_rule(self):
        index = self.fields.currentRow()
        conflict = self.conflicts.get(self.visible_keys[index]) if 0 <= index < len(self.visible_keys) else None
        for col in (3, 4):
            self.fields.setColumnHidden(col, not conflict)
        for action, button in self.rule_actions.items():
            button.setVisible(bool(conflict))
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
        if snapshot_path(self.root,self.cfg).is_file(): self.refresh_saved_preview()
