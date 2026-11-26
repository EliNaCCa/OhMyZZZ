"""Offer a release on interactive GUI startup and apply the chosen version after exit."""
import os
import subprocess
import sys
from pathlib import Path
from threading import Thread
from time import time, time_ns
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

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
    finished=Signal(str,str,str)
    downloaded=Signal(object,str)

    def __init__(self, app, home, ctx):
        super().__init__(app)
        self.home=home
        self.ctx=ctx
        self.directory=maintenance.state_dir(home)
        self.busy=False
        self.manual=False
        self.window=None
        self.box=None
        self.version=PLUGIN_VERSION
        self.progress=None
        self.finished.connect(self.receive,Qt.ConnectionType.QueuedConnection)
        self.downloaded.connect(self.finish_install,Qt.ConnectionType.QueuedConnection)
        app.aboutToQuit.connect(self.on_exit)

    def can_reload(self):
        return getattr(getattr(self.ctx,'run_context',None),'is_context_stop',False)

    def immediate(self, version):
        self.progress = QProgressDialog(f'正在下载并校验 OhMyZZZ {version}…', '', 0, 0, self.window)
        self.progress.setCancelButton(None)
        self.progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.progress.setWindowTitle('OhMyZZZ 更新')
        self.progress.show()
        def prepare():
            temporary = TemporaryDirectory(prefix='OhMyZZZ-update-')
            try:
                stage = Path(temporary.name)
                chosen, release = maintenance.published_release(version)
                ref = maintenance.download(stage, chosen, release)
                self.downloaded.emit((temporary, ref), '')
            except Exception as exc:
                temporary.cleanup()
                self.downloaded.emit(None, str(exc))
        Thread(target=prepare, name='OhMyZZZ package download', daemon=True).start()

    def finish_install(self, package, error):
        def idle(home):
            if not self.can_reload():
                raise maintenance.Deferred('一条龙正在执行任务，请在空闲时更新')
            maintenance.require_idle(home, gui_pid=os.getpid())

        def verify(home, version):
            maintenance.smoke_installed(home, version)
            self.ctx.refresh_application_registration()
            info = self.ctx.factory_manager.get_plugin_info(maintenance.PLUGIN)
            if info is None or info.version != version:
                raise ValueError('一条龙未加载目标插件版本')

        try:
            if error:
                raise ValueError(error)
            temporary, ref = package
            self.progress.setLabelText('正在安装并刷新一条龙任务列表…')
            directory = self.directory
            with maintenance.exclusive(directory):
                idle(self.home)
                if (directory/'pending.json').exists():
                    raise ValueError('上次更新待恢复，请正常退出一条龙后再试')
                live = maintenance.read(self.home/'plugins'/maintenance.PLUGIN/'plugin.json')['version']
                if live != self.version:
                    raise ValueError('安装版本已变化，请重新打开设置后检查更新')
                queued = maintenance.read(directory/'request.json', {})
                if queued and (queued.get('action'),queued.get('version')) != ('retry',ref['version']):
                    raise ValueError('已有其他版本待办，请先在版本维护页取消待办')
                # Copy on the host volume, then retain() can perform an atomic move.
                import shutil
                stage = maintenance.archived(Path(temporary.name), ref)
                transfer = directory/'download.tmp'
                shutil.copyfile(stage, transfer)
                target = maintenance.retain(directory, transfer, ref['version'])
                if ref != target:
                    raise ValueError('暂存包校验失败')
                result = maintenance.switch(self.home,directory,target,'update',idle=idle,verify=verify,request=queued)
                if queued and maintenance.read(directory/'request.json',{}) == queued:
                    maintenance.write(directory/'request.json',{})
                maintenance.write(directory/'last-run.json',dict(status='ok',time=time(),message=result))
            message = '更新已生效 · '+result
        except Exception as exc:
            message = '更新未完成 · '+str(exc)
        finally:
            if package:
                package[0].cleanup()
            self.progress.close()
            self.progress.deleteLater()
            self.progress = None
        QMessageBox.information(self.window, 'OhMyZZZ', message)

    def on_exit(self):
        try:
            launch_after_exit(self.home)
        except Exception as exc:
            maintenance.write(self.directory/'last-run.json',dict(status='failed',message=str(exc)))

    def automatic(self):
        return startup_enabled(self.directory) and getattr(getattr(self.ctx,'env_config',None),'plugin_update_notify',True)

    def check(self, window, manual=False):
        if self.progress is not None: return
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
                version,release=maintenance.published_release()
                self.finished.emit(version,release.get('body') or '','')
            except Exception as exc:
                try: self.finished.emit('','',str(exc))
                except RuntimeError: pass  # Main window closed while the request was in flight.
        Thread(target=lookup,name='OhMyZZZ release check',daemon=True).start()

    def receive(self,version,body,error):
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
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(f'驱动盘管理有新版本 {version}')
        from .release_notes import plain_notes
        notes = plain_notes(body) or '此版本暂无更新说明。'
        if len(notes) > 430 or len(notes.splitlines()) > 8:
            box.setDetailedText(notes)
            notes = '\n'.join(notes.splitlines()[:8])[:430].rstrip() + '…'
        box.setInformativeText(f'当前版本 {PLUGIN_VERSION}\n\n{notes}')
        now=box.addButton('立即更新',QMessageBox.ButtonRole.AcceptRole)
        now.setEnabled(bool(self.can_reload()))
        accept=box.addButton('退出后更新',QMessageBox.ButtonRole.AcceptRole)
        later=box.addButton('稍后',QMessageBox.ButtonRole.RejectRole)
        disable=box.addButton('关闭启动提示',QMessageBox.ButtonRole.ActionRole)
        box.setDefaultButton(later)
        box.setEscapeButton(later)
        def done(_):
            selected=box.clickedButton()
            if selected is now: self.immediate(version)
            elif selected is accept: queue(self.directory,'retry',version)
            elif selected is disable: set_startup(self.directory,False)
            self.box=None
            box.deleteLater()
        box.finished.connect(done)
        box.open()


def attach(ctx):
    app=QApplication.instance()
    if not isinstance(app,QApplication): return
    def start():
        previous=getattr(app,'_ohmyzzz_prompt',None)
        if previous is not None and getattr(previous,'version',None)==PLUGIN_VERSION: return
        win=next((w for w in app.topLevelWidgets() if getattr(w,'ctx',None) is ctx and w.isVisible()),None)
        if win is None: return
        home=Path(__file__).resolve().parents[2]
        if previous is not None:
            app.aboutToQuit.disconnect(previous.on_exit)
            previous.deleteLater()
        prompt=app._ohmyzzz_prompt=UpdatePrompt(app,home,ctx)
        if previous is None: prompt.check(win)
    QTimer.singleShot(1000,app,start)
