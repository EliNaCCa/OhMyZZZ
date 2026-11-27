from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ComboBox, LineEdit

from .disc_owner import owner_label
from .reserve_presets import STAT_NAMES


class InventoryFilter(QWidget):
    """Filter the saved preview. Table row indices still identify the original discs."""
    changed = Signal()

    def __init__(self, table):
        super().__init__()
        self.table = table
        self.index = []
        self.rows = []
        self.search = LineEdit()
        self.search.setPlaceholderText('搜索套装、号位、主属性或保留理由')
        self.search.setClearButtonEnabled(True)
        self.kind = ComboBox()
        self.kind.addItems(['全部', '建议强化', '弃置候选', '保留', '额外备用', '配队用盘', '已标记弃置', '待复核'])
        self.total = BodyLabel()
        bar = QHBoxLayout(self)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.kind)
        bar.addWidget(self.total)
        self.search.textChanged.connect(self.apply)
        self.kind.currentIndexChanged.connect(self.apply)

    def selected_identity(self):
        index = self.table.currentRow()
        if 0 <= index < len(self.rows) and not self.table.isRowHidden(index):
            row = self.rows[index]
            return self.identity(row)

    @staticmethod
    def identity(row):
        return ('cn-uid', row['exportUid']) if row.get('exportUid') else (row['id'], row['fp'])

    def present(self, rows, names, account, fill):
        same_account = account == getattr(self, 'account', None)
        selected = self.selected_identity() if same_account else None
        if not same_account:
            from PySide6.QtCore import QSignalBlocker
            with QSignalBlocker(self.search), QSignalBlocker(self.kind):
                self.search.clear()
                self.kind.setCurrentIndex(0)
        self.account = account
        self.rows = rows
        self.index = []
        formatted = []
        tooltips = []
        for row in rows:
            reasons = row['reasons']
            items = list(dict.fromkeys(row['review'] + reasons + row.get('explanation', []) + row.get('recommendations', [])))
            notes = '、'.join(items)
            headline = items[0] if items else '未命中保留规则'
            if len(items) > 1:
                headline += f'  ·  +{len(items) - 1}'
            if row.get('upgrade'):
                headline = row['upgrade']['reason']
            suit = names.get(row['set'], row['set'])
            main = STAT_NAMES.get(row['main'],row['main'])
            slot = str(row['slot'])+' 号位'
            formatted.append((suit,row['slot'],main,f"+{row['level']}",row['decision'],headline))
            tooltips.append(notes)
            kinds = {row['decision']}
            if row.get('upgrade'): kinds.add('建议强化')
            if row['marked']: kinds.add('已标记弃置')
            if '已保存配装用盘' in reasons or any(reason.startswith('配队 · ') for reason in reasons): kinds.add('配队用盘')
            if '插件备用锁' in reasons or any(reason.startswith('备用 · ') for reason in reasons): kinds.add('额外备用')
            keywords = ' '.join((suit, row['set'], slot, main, row['main'], owner_label(row), notes, *kinds))
            self.index.append((keywords.casefold().replace(' ',''), kinds))
        self.table.blockSignals(True)
        try:
            fill(self.table, formatted)
            for index, note in enumerate(tooltips):
                self.table.item(index, 5).setToolTip(note)
        finally:
            self.table.blockSignals(False)
        self.filter(selected)

    def apply(self, *_):
        self.filter(self.selected_identity())

    def filter(self, selected):
        terms = self.search.text().casefold().split()
        kind = self.kind.currentText()
        visible = []
        self.table.blockSignals(True)
        try:
            for i, (keywords, kinds) in enumerate(self.index):
                show = (kind == '全部' or kind in kinds) and all(term in keywords for term in terms)
                self.table.setRowHidden(i, not show)
                if show: visible.append(i)
            if visible:
                match = next((i for i in visible if self.identity(self.rows[i]) == selected), visible[0])
                self.table.selectRow(match)
            else:
                self.table.setCurrentCell(-1,-1)
        finally:
            self.table.blockSignals(False)
        self.total.setText(f'{len(visible)} / {len(self.rows)} 张')
        self.changed.emit()
