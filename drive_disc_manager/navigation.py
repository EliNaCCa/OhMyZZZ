from PySide6.QtCore import Qt
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import Pivot, SegmentedWidget


class ManagerPages(QWidget):
    """OneDragon-style navigation; secondary pages share their section's pivot."""

    sections = {'仓库': ('仓库预览',), '配队': ('我的配队', '配装器'),
                '规则': ('规则订阅', '角色留盘', '备用规则'), '设置': ('运行设置', '库存记录', '版本更新')}

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.pivot = Pivot(self)
        layout.addWidget(self.pivot, 0, Qt.AlignmentFlag.AlignLeft)
        self.stack = QStackedWidget(self)
        self.pages = {}
        self.last = {}
        self.selectors = {}
        for section, pages in self.sections.items():
            self.pivot.addItem(section, section, onClick=lambda _, s=section: self.open(self.last.get(s, self.sections[s][0])))
            if len(pages) > 1:
                selector = SegmentedWidget(self)
                for page in pages:
                    selector.addItem(page, page, onClick=lambda _, p=page: self.open(p))
                layout.addWidget(selector, 0, Qt.AlignmentFlag.AlignLeft)
                self.selectors[section] = selector
                selector.hide()
        layout.addWidget(self.stack, 1)
        self.stack.currentChanged.connect(self.sync)

    def addTab(self, widget, title):
        self.pages[title] = widget
        return self.stack.addWidget(widget)

    def open(self, title):
        self.stack.setCurrentWidget(self.pages[title])

    def sync(self, index):
        widget = self.stack.widget(index)
        title = next(name for name, page in self.pages.items() if page is widget)
        active = next((section for section, pages in self.sections.items() if title in pages), '仓库')
        if title in self.sections[active]:
            self.last[active] = title
        self.pivot.setCurrentItem(active)
        for section, selector in self.selectors.items():
            selector.setVisible(section == active)
            if section == active:
                selector.setCurrentItem(title)


class DetailPages(QWidget):
    """Compact subviews of the selected disc or team."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.selector = SegmentedWidget(self)
        self.stack = QStackedWidget(self)
        layout.addWidget(self.selector, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.stack, 1)

    def addTab(self, widget, title):
        index = self.stack.addWidget(widget)
        widget.setProperty('pageTitle', title)
        self.selector.addItem(title, title, onClick=lambda _: self.setCurrentIndex(index))
        if index == 0:
            self.setCurrentIndex(0)

    def currentIndex(self):
        return self.stack.currentIndex()

    def setCurrentIndex(self, index):
        self.stack.setCurrentIndex(index)
        self.selector.setCurrentItem(self.stack.currentWidget().property('pageTitle'))
