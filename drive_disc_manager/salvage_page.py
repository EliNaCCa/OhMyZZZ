"""The current account's dismantling last observed inventory changes."""
from datetime import datetime

from PySide6.QtCore import Signal, QSignalBlocker, Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CheckBox, PushButton, SubtitleLabel

from .disc_detail_page import table
from .reserve_presets import STAT_NAMES
from .salvage_audit import needs_review


class SalvagePage(QWidget):
    acknowledge = Signal(object, str, str)

    def __init__(self, fill):
        super().__init__()
        self.fill = fill
        self.account = None
        layout = QVBoxLayout(self)
        bar = QHBoxLayout(); layout.addLayout(bar)
        self.title = SubtitleLabel(); bar.addWidget(self.title, 1)
        self.issues = CheckBox('只看需要核对的盘'); bar.addWidget(self.issues)
        self.issues.toggled.connect(self.render)
        self.note = BodyLabel(); self.note.setWordWrap(True); layout.addWidget(self.note)
        self.grid = table(['套装', '号位', '主属性', '等级', '变化'])
        for col, width in enumerate((145, 58, 120, 58)):
            self.grid.setColumnWidth(col, width)
        layout.addWidget(self.grid, 1)
        self.close_batch = PushButton('我已在游戏中核对'); layout.addWidget(self.close_batch, 0, Qt.AlignmentFlag.AlignRight)
        self.close_batch.clicked.connect(self.finish)

    def present(self, account, names, last):
        batch = last.get('batch')
        if (account, batch) != (self.account, getattr(self, 'batch', None)):
            with QSignalBlocker(self.issues):
                self.issues.setChecked(needs_review(last))
        self.account, self.batch = account, batch
        self.names, self.last = names, last
        self.render()

    def finish(self):
        if needs_review(self.last) and self.last.get('afterInventory'):
            self.acknowledge.emit(self.account, self.last['batch'], self.last['afterInventory'])

    def render(self, *_):
        if self.account is None:
            return
        data = self.last
        review = needs_review(data)
        observed = bool(data.get('afterInventory'))
        rows = []
        labels = {'已不在库存':'已离库', '重复属性待核':'有同属性的盘，需核对', '仍标弃置':'仍在仓库',
                  '在库且未标弃置':'仍在仓库，已取消弃置', '仍在低品级范围':'仍在仓库', '标记待核':'弃置标记没读清'}
        for entry in data.get('entries', []):
            state = entry.get('after')
            if state:
                text = labels.get(state, state)
                uncertain = entry.get('source') == '弃置标记待核'
                if uncertain:
                    text = '标记时中断 · ' + ('仍在仓库' if state == '在库且未标弃置' else text)
                rows.append((entry, text, uncertain or state not in ('已不在库存', '在库且未标弃置')))
        for item in data.get('stockChanges', []):
            state = '新刷到的盘' if item['kind'] == 'extra' and data.get('newDiscs') else item['after']
            rows.append((item, state, review))
        can_filter = review and observed and any(row[2] for row in rows)
        self.issues.setVisible(can_filter)
        only_issues = can_filter and self.issues.isChecked()
        formatted = []
        for entry, state, issue in rows:
            if only_issues and not issue:
                continue
            key = entry.get('set')
            name = self.names.get(key, key) or '盘名未记录'
            if entry.get('count', 1) > 1:
                name += f" ×{entry['count']}"
            formatted.append((name, entry.get('slot', '—'), STAT_NAMES.get(entry.get('main'), entry.get('main', '—')),
                              f"+{entry['level']}" if 'level' in entry else '—', state))
        self.fill(self.grid, formatted)
        self.grid.setVisible(bool(formatted))
        self.close_batch.setVisible(review and observed)
        if not data:
            self.title.setText('还没有库存记录')
            self.note.hide()
            return
        if review:
            title = '有盘需要核对' if observed else '等待下次扫描'
        else:
            title = '已核对' if data.get('status') == 'reviewed' else '库存已核对'
        self.title.setText(title)
        parts = []
        if data.get('observedAt'):
            parts.append(datetime.fromtimestamp(data['observedAt']).strftime('%m-%d %H:%M'))
        if observed and 'beforeTotal' in data and 'afterTotal' in data:
            parts.append(f"库存 {data['beforeTotal']} → {data['afterTotal']} 张")
        if data.get('newDiscs'):
            parts.append(f"新刷到 {data['newDiscs']} 张")
        if review and data.get('unresolvedGrades'):
            parts.append(f"有 {data['unresolvedGrades']} 张品级没读清")
        if review and data.get('unreadMarks'):
            parts.append(f"有 {data['unreadMarks']} 张弃置标记没读清")
        self.note.setText('　·　'.join(parts))
        self.note.setVisible(bool(parts))
