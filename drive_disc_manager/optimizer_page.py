"""Show the local optimizer in OneDragon and release the account's engine on exit."""
from concurrent.futures import Future
from threading import Thread

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, PushButton


def open_session(session):
    from .engine import Endpoint
    from .optimizer import loopback_json
    endpoint = session.__enter__()
    try:
        if not isinstance(endpoint, Endpoint):
            state = loopback_json(endpoint, '/api/state')
            if not isinstance(state, dict) or not isinstance(state.get('setEffects'), list):
                raise ValueError('这个地址没有角色配装数据')
        return endpoint
    except BaseException:
        session.__exit__(None, None, None)
        raise


class OptimizerPage(QWidget):
    import_requested = Signal()
    back = Signal()
    retry = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_box = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.layout_box.addLayout(bar)
        back = PushButton('返回我的配队')
        back.clicked.connect(self.back.emit)
        bar.addWidget(back)
        bar.addStretch()
        self.import_button = PushButton('导入方案')
        self.import_button.clicked.connect(self.import_requested.emit)
        bar.addWidget(self.import_button)
        self.status = BodyLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.layout_box.addWidget(self.status)
        self.retry_button = PushButton('重新打开')
        self.retry_button.clicked.connect(self.retry.emit)
        self.layout_box.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.poll)
        self.future = self.view = self.endpoint = self.account = None

    def start(self, account, session):
        self.stop()
        future = Future()
        self.future = future
        self.account = account
        self.import_button.setEnabled(False)
        self.retry_button.hide()
        self.status.setText('正在打开配装器…')
        self.status.show()

        def load():
            try:
                endpoint = open_session(session)
            except Exception as exc:
                future.set_exception(exc)
            else:
                future.set_result((session, endpoint))

        # Also release a startup still in flight if the host unloads this page.
        self.on_destroy = lambda *_: OptimizerPage.release(future)
        self.destroyed.connect(self.on_destroy)
        Thread(target=load, name='OhMyZZZ optimizer', daemon=True).start()
        self.timer.start()

    @staticmethod
    def release(future):
        def finish(done):
            if done.exception() is None:
                session, _ = done.result()
                session.__exit__(None, None, None)
        future.add_done_callback(finish)

    def stop(self):
        self.timer.stop()
        if self.view is not None:
            self.view.stop()
            self.view.hide()
            self.view.deleteLater()
            self.view = None
        self.endpoint = self.account = None
        if self.future is not None:
            self.destroyed.disconnect(self.on_destroy)
            self.on_destroy = None
            self.release(self.future)
            self.future = None

    def failed(self, message):
        self.stop()
        self.import_button.setEnabled(False)
        self.status.setText(message)
        self.status.show()
        self.retry_button.show()

    def poll(self):
        if self.future is None or not self.future.done():
            return
        self.timer.stop()
        try:
            _, self.endpoint = self.future.result()
            from .optimizer_webview import local_view
            self.view = local_view(getattr(self.endpoint, 'url', self.endpoint), self)
            view = self.view
            self.layout_box.addWidget(view, 1)
            view.loadFinished.connect(lambda ok: self.loaded(view, ok))
            view.load(QUrl(getattr(self.endpoint, 'url', self.endpoint)))
        except ImportError:
            self.failed('请在一条龙的 Python 环境中安装与 PySide6 同版本的 PySide6-Addons，再打开配装器。')
        except Exception as exc:
            self.failed(str(exc))

    def loaded(self, view, ok):
        if view is not self.view:
            return
        if not ok:
            self.failed('配装器页面加载失败')
            return
        self.status.hide()
        self.import_button.setEnabled(True)
