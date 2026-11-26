from copy import deepcopy

from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton,
    QTableWidget, QVBoxLayout)

from .combat_windows import WINDOW_STATS, window_requests


MODES = {'additional':'本窗口额外生效', 'included':'配装器已计入', 'inactive':'已计入，本窗口不生效'}
STAT_NAMES = dict(zip(WINDOW_STATS, ('暴击率 (%)','暴击伤害 (%)','攻击力 (%)','攻击力','生命值 (%)','生命值',
    '伤害加成 (%)','火属性伤害 (%)','冰属性伤害 (%)','电属性伤害 (%)','以太伤害 (%)','物理伤害 (%)',
    '风属性伤害 (%)','流明伤害 (%)','贯穿伤害 (%)'), strict=True))


class WindowEditor(QDialog):
    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.setWindowTitle(profile['name']+' · 输出窗口')
        self.resize(820, 460)
        self.profile = profile
        self.data = deepcopy(profile.get('windows') or [dict(name='常态',weight=100,buffs=[])])
        self.active = -1
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox('按窗口输出占比计算配装'); layout.addWidget(self.enabled)
        self.enabled.setChecked(profile.get('windowsEnabled', bool(profile.get('windows'))))
        layout.addWidget(QLabel('按各窗口的输出占比选盘，用主要窗口的实战暴击率判断满暴。'))
        self.windows = QTableWidget(0,3); self.windows.setHorizontalHeaderLabels(['窗口','输出占比','主要窗口'])
        self.windows.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.group = QButtonGroup(self)
        layout.addWidget(self.windows)
        self.bar(layout, [('添加窗口',self.add_window),('移除所选窗口',self.remove_window)])
        self.label = QLabel(); layout.addWidget(self.label)
        self.buffs = QTableWidget(0,4); self.buffs.setHorizontalHeaderLabels(['来源 / 生效条件','词条','数值','计算方式'])
        self.buffs.setColumnWidth(0,205); self.buffs.setColumnWidth(1,165); self.buffs.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.buffs)
        self.bar(layout,[('添加增益',lambda:self.add_buff({})),('移除所选增益',self.remove_buff)])
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.commit); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        for index, row in enumerate(self.data):
            self.insert_window(row, row['name']==profile.get('mainWindow') or (not index and not profile.get('mainWindow')))
        self.windows.currentCellChanged.connect(lambda row,*_:self.switch(row))
        self.windows.selectRow(0)
        self.switch(0)

    def bar(self, layout, actions):
        bar=QHBoxLayout(); layout.addLayout(bar)
        for name, fn in actions:
            button=QPushButton(name); button.clicked.connect(fn); bar.addWidget(button)

    def insert_window(self, row, primary):
        i = self.windows.rowCount(); self.windows.insertRow(i)
        name = QLineEdit(row['name']); weight = QDoubleSpinBox(); weight.setRange(0.001,100000); weight.setDecimals(3); weight.setValue(row['weight'])
        primary_radio=QRadioButton(); self.group.addButton(primary_radio); primary_radio.setChecked(primary)
        for j,w in enumerate((name,weight,primary_radio)):
            self.windows.setCellWidget(i,j,w)
        for w in (name, weight, primary_radio):
            w.installEventFilter(self)

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type()==QEvent.Type.FocusIn:
            for i in range(self.windows.rowCount()):
                if any(self.windows.cellWidget(i,j) is obj for j in range(3)):
                    self.windows.selectRow(i); break
        return super().eventFilter(obj,event)

    def add_window(self):
        row=dict(name='窗口 '+str(len(self.data)+1),weight=1,buffs=[])
        self.data.append(row); self.insert_window(row,False); self.windows.selectRow(len(self.data)-1)

    def remove_window(self):
        if len(self.data)<=1 or self.active<0: return
        i = self.active; self.active=-1; self.data.pop(i)
        self.windows.blockSignals(True); self.windows.removeRow(i); self.windows.blockSignals(False)
        if self.group.checkedButton() is None: self.windows.cellWidget(0,2).setChecked(True)
        i=min(i,len(self.data)-1); self.windows.selectRow(i); self.switch(i)

    def add_buff(self, buff):
        i=self.buffs.rowCount(); self.buffs.insertRow(i)
        source=QLineEdit(buff.get('source','')); stat=QComboBox()
        for key, label in STAT_NAMES.items(): stat.addItem(label,key)
        stat.setCurrentIndex(stat.findData(buff.get('stat','CRIT_RATE')))
        value=QDoubleSpinBox(); value.setDecimals(4); value.setRange(-100000,100000); value.setValue(buff.get('value',0))
        mode=QComboBox()
        for k,v in MODES.items(): mode.addItem(v,k)
        mode.setCurrentIndex(mode.findData(buff.get('mode','additional')))
        for j,w in enumerate((source,stat,value,mode)): self.buffs.setCellWidget(i,j,w)

    def remove_buff(self):
        i=self.buffs.currentRow()
        if i>=0: self.buffs.removeRow(i)

    def collect(self):
        if self.active<0: return
        table=self.buffs
        self.data[self.active]['buffs']=[dict(source=table.cellWidget(i,0).text().strip(),
            stat=table.cellWidget(i,1).currentData(),value=table.cellWidget(i,2).value(),
            mode=table.cellWidget(i,3).currentData()) for i in range(table.rowCount())]

    def switch(self, row):
        if row<0 or row==self.active: return
        self.collect(); self.active=row
        self.label.setText('窗口增益 · '+self.windows.cellWidget(row,0).text())
        self.buffs.setRowCount(0)
        for buff in self.data[row].get('buffs',[]): self.add_buff(buff)

    def commit(self):
        self.collect(); main=''
        for i,row in enumerate(self.data):
            row.update(name=self.windows.cellWidget(i,0).text().strip(),weight=self.windows.cellWidget(i,1).value())
            if self.windows.cellWidget(i,2).isChecked(): main=row['name']
        self.changes = dict(windows=self.data, windowsEnabled=self.enabled.isChecked(), mainWindow=main)
        try: window_requests(dict(self.profile,**self.changes))
        except ValueError as exc:
            QMessageBox.warning(self,'输出窗口',str(exc)); return
        self.accept()
