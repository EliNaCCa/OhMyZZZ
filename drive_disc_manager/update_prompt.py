"""Offer a release on interactive GUI startup and apply the chosen version after exit."""
import os
import subprocess
import sys
from pathlib import Path
from threading import Thread
from time import time_ns

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from . import maintenance
from .drive_disc_manager_const import PLUGIN_VERSION


def startup_enabled(directory):
    return maintenance.read(directory/'preferences.json',{}).get('startupPrompt',True)


def set_startup(directory, enabled):
    prefs=maintenance.read(directory/'preferences.json',{})
    prefs.pop('auto',None)
    prefs['startupPrompt']=bool(enabled)
    maintenance.write(directory/'preferences.json',prefs)


def queue(directory, action, version):
    maintenance.validate_version(version)
    maintenance.write(directory/'request.json',dict(id=str(time_ns()),action=action,version=version))


def launch_after_exit(home):
    import psutil
    directory=maintenance.state_dir(home)
    if not (directory/'pending.json').exists() and not maintenance.read(directory/'request.json',{}):
        return
    maintenance.refresh_worker(home,directory)
    interpreter = home / '.venv' / 'Scripts' / 'pythonw.exe'
    if not interpreter.is_file():
        interpreter = Path(sys.executable)
        if not interpreter.name.lower().startswith('python'):
            raise RuntimeError('找不到一条龙的 Python 解释器')
    args=[str(interpreter),'-I',str(directory/'worker.py'),'--host',str(home),'--wait-pid',str(os.getpid()),
          '--created',str(psutil.Process().create_time()),'auto']
    subprocess.Popen(args,cwd=home,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


class UpdatePrompt(QObject):
    finished=Signal(str,str)

    def __init__(self, app, home, ctx):
        super().__init__(app)
        self.home=home
        self.ctx=ctx
        self.directory=maintenance.state_dir(home)
        self.busy=False
        self.manual=False
        self.window=None
        self.box=None
        self.finished.connect(self.receive,Qt.ConnectionType.QueuedConnection)
        app.aboutToQuit.connect(self.on_exit)

    def on_exit(self):
        try:
            launch_after_exit(self.home)
        except Exception as exc:
            maintenance.write(self.directory/'last-run.json',dict(status='failed',message=str(exc)))

    def automatic(self):
        return startup_enabled(self.directory) and getattr(getattr(self.ctx,'env_config',None),'plugin_update_notify',True)

    def check(self, window, manual=False):
        if not manual and not self.automatic(): return
        if self.box is not None:
            self.box.raise_()
            return
        self.window=window
        self.manual=manual or self.manual
        if self.busy: return
        self.busy=True
        def lookup():
            try:
                version,_=maintenance.published_release()
                self.finished.emit(version,'')
            except Exception as exc:
                try: self.finished.emit('',str(exc))
                except RuntimeError: pass  # Main window closed while the request was in flight.
        Thread(target=lookup,name='OhMyZZZ release check',daemon=True).start()

    def receive(self,version,error):
        self.busy=False
        manual,self.manual=self.manual,False
        if not manual and not self.automatic(): return
        import shiboken6
        if not self.window or not shiboken6.isValid(self.window) or not self.window.isVisible(): return
        if error:
            if manual: QMessageBox.information(self.window,'OhMyZZZ','检查更新失败 · '+error)
            return
        if maintenance.validate_version(version)<=maintenance.validate_version(PLUGIN_VERSION):
            if manual: QMessageBox.information(self.window,'OhMyZZZ',f'当前已是最新版 {PLUGIN_VERSION}')
            return
        queued=maintenance.read(self.directory/'request.json',{})
        if not manual and (queued or maintenance.read(self.directory/'hold.json',{}).get('version')==version): return
        box=QMessageBox(self.window)
        self.box=box
        box.setWindowTitle('OhMyZZZ 更新')
        box.setText(f'驱动盘管理有新版本 {version}')
        box.setInformativeText(f'当前版本 {PLUGIN_VERSION}。选择更新后，将在一条龙退出时校验并安装。')
        accept=box.addButton('退出后更新',QMessageBox.ButtonRole.AcceptRole)
        later=box.addButton('稍后',QMessageBox.ButtonRole.RejectRole)
        disable=box.addButton('关闭启动提示',QMessageBox.ButtonRole.ActionRole)
        box.setDefaultButton(later)
        box.setEscapeButton(later)
        def done(_):
            selected=box.clickedButton()
            if selected is accept: queue(self.directory,'retry',version)
            elif selected is disable: set_startup(self.directory,False)
            self.box=None
            box.deleteLater()
        box.finished.connect(done)
        box.open()


def attach(ctx):
    app=QApplication.instance()
    if not isinstance(app,QApplication): return
    def start():
        if getattr(app,'_ohmyzzz_prompt',None): return
        win=next((w for w in app.topLevelWidgets() if getattr(w,'ctx',None) is ctx and w.isVisible()),None)
        if win is None: return
        home=Path(__file__).resolve().parents[2]
        prompt=app._ohmyzzz_prompt=UpdatePrompt(app,home,ctx)
        prompt.check(win)
    QTimer.singleShot(1000,app,start)
