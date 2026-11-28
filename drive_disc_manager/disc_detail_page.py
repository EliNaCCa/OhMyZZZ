from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from qfluentwidgets import BodyLabel, TableWidget, PushButton, PlainTextEdit
from .navigation import DetailPages
from .disc_trace import teams_for_disc
from .stat_reading import display_values


def label():
    widget = BodyLabel()
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def table(headers):
    widget = TableWidget()
    widget.setColumnCount(len(headers))
    widget.verticalHeader().hide()
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.horizontalHeader().setStretchLastSection(True)
    return widget


def fill(widget, rows):
    widget.clearSelection()
    widget.setRowCount(0)
    for index, row in enumerate(rows):
        widget.insertRow(index)
        for column, text in enumerate(row):
            item = QTableWidgetItem(str(text)); item.setToolTip(str(text))
            widget.setItem(index, column, item)
    widget.resizeColumnsToContents()
    if rows:
        widget.selectRow(0)


def field_label(key):
    field = key.rsplit('/', 1)[-1]
    if field in ('sets4', 'sets2', 'subs'):
        return {'sets4':'四件套', 'sets2':'两件套', 'subs':'副词条'}[field]
    slot = field.removeprefix('slot')
    return f'{slot} 号主词条' if slot in ('4', '5', '6') else field


def rule_values(key, items, set_names, *, accepted=False):
    if items is None:
        return '表中没有这项'
    from .main_rule_editor import NAMES
    from .purpose_editor import SUBSTAT_NAMES
    from .subscriptions import accepted_terms, stat_field
    names = {}
    if key.startswith('matrix/'):
        names = NAMES
    elif key.endswith(('/sets4', '/sets2')):
        names = set_names
    elif accepted and (field := stat_field(key)):
        names = SUBSTAT_NAMES if field == 'subs' else NAMES
        items = sorted(accepted_terms(key, items))
    return '、'.join(names.get(v, v) for v in items) or '无'


def cell_change_text(changes, side=None):
    from .main_rule_editor import NAMES
    states = {'recommended':'推荐', 'zero':'不推荐', 'blank':'未填写', 'missing':'无此项'}
    parts = []
    for stat, pair in changes.items():
        status = ' → '.join(states[v] for v in pair) if side is None else states[pair[side]]
        parts.append(f'{NAMES.get(stat, stat)}（{status}）')
    return '、'.join(parts)


class DiscDetails(QWidget):
    back = Signal()
    rule = Signal(str)
    team = Signal(str)
    edit_policy = Signal(str)

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        bar = QHBoxLayout(); outer.addLayout(bar)
        close = PushButton('返回仓库'); bar.addWidget(close); close.clicked.connect(self.back.emit)
        self.title = label(); bar.addWidget(self.title, 1)
        self.pin = PushButton(); bar.addWidget(self.pin)
        self.quota = PushButton('同类备用数量'); bar.addWidget(self.quota)
        self.pin.clicked.connect(lambda: self.edit_policy.emit('pin'))
        self.quota.clicked.connect(lambda: self.edit_policy.emit('quota'))
        self.stats = label(); outer.addWidget(self.stats)
        self.tabs = DetailPages(); outer.addWidget(self.tabs)
        page = QWidget(); box = QVBoxLayout(page); self.tabs.addTab(page,'规则来源')
        self.rules = table(['角色 / 套装','规则','来源页签','对这张盘的判断']); box.addWidget(self.rules)
        self.field_value = label(); box.addWidget(self.field_value)
        actions = QHBoxLayout(); box.addLayout(actions)
        self.rule_button = PushButton('查看规则'); actions.addWidget(self.rule_button)
        self.show_source = PushButton('表格原文'); self.show_source.setCheckable(True); actions.addWidget(self.show_source)
        actions.addStretch()
        self.source_text = PlainTextEdit(); self.source_text.setReadOnly(True); self.source_text.setMaximumHeight(140)
        box.addWidget(self.source_text); self.source_text.hide()
        self.show_source.toggled.connect(self.source_text.setVisible)
        self.rule_button.clicked.connect(self.open_rule)
        self.rules.itemSelectionChanged.connect(self.select_rule)
        page = QWidget(); box = QVBoxLayout(page); self.tabs.addTab(page,'配队与备用')
        self.teams = table(['方案','角色','同时出场组','选择依据']); box.addWidget(self.teams)
        self.team_button = PushButton('查看配队'); box.addWidget(self.team_button)
        self.team_button.clicked.connect(self.open_team)
        self.teams.itemSelectionChanged.connect(self.select_team)
        self.spares = label(); box.addWidget(self.spares)
        self.summary = PlainTextEdit(); self.summary.setReadOnly(True); self.tabs.addTab(self.summary,'留盘理由')
        self.version = label(); outer.addWidget(self.version)

    def present(self, row, report, *, set_name, coverage, rule_keys, team_names, set_names=None, probe=False, stale=False, pinned=False):
        self.set_names = set_names or {}
        self.fixed_main = str(row['slot']) in ('1', '2', '3')
        self.show_source.setChecked(False)
        self.book = report.get('traceRules', {})
        self.uses = row.get('ruleUses', [])
        self.empty_hint = ('旧预览中没有逐字段来源，可返回仓库重新计算。' if 'traceRules' not in report else
                           '已标记弃置' if row['marked'] else '没有匹配的订阅规则')
        self.rule_keys, self.team_names = set(rule_keys), set(team_names)
        title = f"{set_name} · {row['slot']} 号位 +{row['level']}　{row.get('probeAdvice', row['decision'])}"
        self.title.setText(title)
        self.pin.setText('取消个人保护' if pinned else '始终保留这张')
        self.pin.setEnabled(not row['marked'])
        self.pin.setToolTip('按驱动盘编号保留，强化后仍然有效。' if row.get('exportUid') else '同属性的盘会一起保留。')
        from .disc_owner import preview_owner
        owner = '　穿戴 · '+preview_owner(row) if row.get('ownerState') or row.get('location') else ''
        prefix = '换算值　' if report.get('networkPreview') and row.get('observedValues') else ''
        self.stats.setText(prefix+display_values(row.get('observedValues'))+owner)
        # Keep recommendation evidence distinct from the final quota decision.
        summary = []
        reasons = row['reasons']
        if reasons:
            summary.append('保留依据　'+'、'.join(reasons) if not row['marked'] else '弃置标记　'+'、'.join(reasons))
        summary.extend(row.get('explanation', []))
        if row['review']:
            summary.append('待核　'+'、'.join(row['review']))
        if probe:
            summary.append('配队占用和备用名额需要完整库存。')
        self.summary.setPlainText(chr(10).join(summary))
        rule_rows = []
        for key, agent, outcome in self.uses:
            entry = self.book.get(key, {})
            source = '、'.join(entry.get('tabs', [])) or '未记录页签'
            name = '套装收录' if self.fixed_main and key.startswith('matrix/') else field_label(key)
            rule_rows.append((agent or set_name, name, ('原页签 · ' if entry.get('formerSource') else '')+source, outcome))
        fill(self.rules,rule_rows); self.select_rule()
        teams = teams_for_disc(report, row)
        fill(self.teams, [(name, team.get('agent') or '—', team.get('group') or '可借盘', result.get('reason',''))
                          for name, team, result in teams]); self.select_team()
        lines = []
        if coverage:
            if 'agents' in coverage:
                lines.append('适用代理人：'+'、'.join(coverage['agents']) if coverage['agents'] else '当前代理人用不上')
            else:
                lines.append('订阅推荐盘参与备用排序' if coverage['trimRecommended'] else '订阅推荐盘直接保留')
            for use in coverage['purposes']:
                count = '不限' if use['count'] is None else str(use['count'])
                picked = ' · 这张已入选' if '备用 · '+use['purpose'] in reasons else ''
                lines.append(f"{use['purpose']} 名额 {count}，同类入选 {use['covered']} 张{picked}")
            if coverage.get('fixedExtra'):
                lines.append(f"另有 {coverage['fixedExtra']} 张旧备用保持锁定")
        if not teams and not probe:
            lines.append('暂无配队使用')
        if probe:
            lines.append('读完整个仓库后可查看备用数量。')
        self.spares.setText('　｜　'.join(lines))
        rev = (report.get('rules') or '')[:12] or '无'
        scope = '单盘实测 · 当前规则' if probe else '库存预览 · 计算时规则'
        self.version.setToolTip(rev)
        self.version.setText(f'{scope}' + ('　｜　预览已过期，请重新计算' if stale else ''))
        self.tabs.setCurrentIndex(0 if self.uses else 2)

    def select_rule(self):
        index = self.rules.currentRow()
        key = self.uses[index][0] if 0 <= index < len(self.uses) else None
        entry = self.book.get(key)
        self.rule_button.setEnabled(key in self.rule_keys)
        cells = (entry or {}).get('sourceCells', {})
        fixed = self.fixed_main and bool(key and key.startswith('matrix/'))
        lines = []
        for tab, source in cells.items():
            lines.append(tab)
            for coord, heading, raw in source['cells']:
                if not fixed or heading == '套装':
                    if heading == '四件套图标':
                        raw = ' / '.join(self.set_names.get(v, v) for v in raw.split(' / '))
                    lines.append(f'{coord}　{heading}　{raw or "（空白）"}')
        self.source_text.setPlainText(chr(10).join(lines))
        self.source_text.setToolTip(chr(10).join(s['url'] for s in cells.values()))
        self.show_source.setVisible(bool(lines))
        self.source_text.setVisible(bool(lines) and self.show_source.isChecked())
        if not entry:
            self.field_value.setText(self.empty_hint)
            return
        if fixed:
            self.field_value.setText('')
            return
        def values(items):
            return rule_values(key, items, self.set_names, accepted=True)
        lines = ['订阅推荐　'+values(entry['remote'])]
        if entry['mode'] != 'inherit':
            mode = {'add':'额外接受', 'only':'只接受'}[entry['mode']]
            lines.extend(['我的规则　'+mode+' '+values(entry['local']), '当前生效　'+values(entry['active'])])
        if entry['conflict']:
            if entry.get('cellChanges'):
                lines.insert(0, '攻略更新　'+cell_change_text(entry['cellChanges']))
            else:
                lines.insert(0,'上游变动前　'+values(entry['old']))
        self.field_value.setText('　｜　'.join(lines))

    def open_rule(self):
        index = self.rules.currentRow()
        if 0 <= index < len(self.uses):
            self.rule.emit(self.uses[index][0])

    def select_team(self):
        item = self.teams.item(self.teams.currentRow(),0)
        self.team_button.setEnabled(bool(item and item.text() in self.team_names))

    def open_team(self):
        item = self.teams.item(self.teams.currentRow(),0)
        if item:
            self.team.emit(item.text())
