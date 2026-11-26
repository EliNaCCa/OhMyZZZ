"""Open one actual optimizer page and import one saved target into a temporary account."""
import argparse
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import patch


def smoke(host, temp):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(host/'src')]
    from PySide6.QtWidgets import QApplication, QInputDialog
    from one_dragon.utils import os_utils
    from drive_disc_manager.engine import NAME, VERSION, SIZE, SHA256
    from drive_disc_manager.maintenance import sha256
    from drive_disc_manager.settings import ManagerInterface
    from drive_disc_manager.store import config

    exe = host/'plugin_data'/'drive_disc_manager'/'_optimizer'/VERSION/NAME
    assert exe.stat().st_size == SIZE and sha256(exe) == SHA256
    app = QApplication([])
    os_utils.set_work_dir(str(temp))
    ctx = SimpleNamespace(current_instance_idx=1, get_plugin_data_dir=lambda _:temp/'accounts')
    widget = ManagerInterface(ctx)
    widget.on_interface_shown()
    panel = widget.optimizer_page
    assert 'PySide6.QtWebEngineWidgets' not in sys.modules

    def wait(check):
        deadline = monotonic() + 15
        while monotonic() < deadline:
            app.processEvents()
            if check():
                return
            sleep(.02)
        raise AssertionError(panel.status.text() or 'page timed out')

    def javascript(code):
        value = []
        panel.view.page().runJavaScript(code, 0, value.append)
        wait(lambda: bool(value))
        return value[0]

    with patch('drive_disc_manager.engine.executable', return_value=exe):
        widget.navigation.open('配装器')
        try:
            wait(lambda: panel.endpoint is not None and panel.import_button.isEnabled())
            proc = panel.endpoint.proc
            assert javascript("document.querySelector('#saveMultiPlanBtn').textContent").startswith('保存')
            wait(lambda: javascript("document.querySelector('#required2Set').options.length") > 2)
            actor = javascript("currentCharacter().name")
            full_name = javascript("currentCharacter().fullName || currentCharacter().name")
            assert actor
            selected = javascript("""(() => {
                const four = document.querySelector('#required4Set');
                const two = document.querySelector('#required2Set');
                four.value = Array.from(four.options).find(option => option.value).value;
                two.value = Array.from(two.options).find(option => option.value && option.value !== four.value).value;
                return Boolean(four.value && two.value && four.value !== two.value);
            })()""")
            assert selected
            javascript("document.querySelector('#saveMultiPlanBtn').click()")
            from drive_disc_manager.optimizer import saved_targets
            wait(lambda: len(saved_targets(panel.endpoint)) == 1)
            with (patch.object(QInputDialog, 'getItem', side_effect=lambda *args:(args[3][0],True)),
                  patch.object(QInputDialog, 'getText', side_effect=[('配装器冒烟',True),('',True)])):
                widget.import_profile()
            imported, = config(temp/'accounts'/'1')['profiles']
            assert imported['name'] == '配装器冒烟' and imported['request']['characterName'] == full_name
            assert widget.tabs.currentWidget() is widget.navigation.pages['我的配队'] and not proc.is_running()
            assert all(card.titleLabel.text() == card.btn.accessibleName() for card in widget.flags.values())
            assert '四词条双暴胚子' in widget.flags['protectNaturalCrit'].titleLabel.text()
            # Leaving while startup is still in flight must release the same instance.
            widget.navigation.open('配装器')
            opening = panel.future
            ctx.current_instance_idx = 2
            widget.on_interface_shown()
            wait(lambda: opening.done() and not opening.result()[1].proc.is_running())
            assert panel.view is None and panel.account is None and not config(temp/'accounts'/'2')['profiles']
        finally:
            panel.stop()
            widget.close()
            from PySide6.QtCore import QCoreApplication, QEvent
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    print('PASS embedded optimizer, save one target, import into current team, engine exit and switch labels')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    with TemporaryDirectory() as tmp:
        smoke(parser.parse_args().home.resolve(), Path(tmp))
