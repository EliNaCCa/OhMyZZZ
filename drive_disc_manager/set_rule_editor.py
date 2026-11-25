"""Select accepted two-piece or four-piece set names for one subscribed agent."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout


class SetRuleEditor(QDialog):
    def __init__(self, parent, title, mode, names, remote, selected):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 410)
        box = QVBoxLayout(self)
        box.addWidget(QLabel('勾选额外接受的套装。' if mode == 'add' else '勾选这个字段接受的套装。'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索套装中文名或订阅名称')
        self.search.setClearButtonEnabled(True)
        box.addWidget(self.search)
        self.sets = QListWidget()
        box.addWidget(self.sets)
        for key in sorted(set(names) | set(remote) | set(selected), key=lambda key:(key not in selected, key not in remote, names.get(key,key), key)):
            label = names.get(key,key)
            item = QListWidgetItem(label + ('（上游推荐）' if key in remote else ''))
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(key)
            item.setCheckState(Qt.CheckState.Checked if key in selected else Qt.CheckState.Unchecked)
            self.sets.addItem(item)
        self.search.textChanged.connect(self.filter)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        box.addWidget(actions)

    def filter(self, query):
        terms = query.casefold().split()
        for index in range(self.sets.count()):
            item = self.sets.item(index)
            label = (item.text()+' '+item.toolTip()).casefold()
            item.setHidden(not all(term in label for term in terms))

    def values(self):
        items = (self.sets.item(index) for index in range(self.sets.count()))
        return sorted(item.data(Qt.ItemDataRole.UserRole) for item in items if item.checkState() == Qt.CheckState.Checked)
