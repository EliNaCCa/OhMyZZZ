"""The CN exporter launched from the settings page. No UAC prompt, pktmon or game input."""
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import psutil
import pywintypes
from win32com.shell import shellcon

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
host = Path(sys.argv[1])
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(host/'src')]
from PySide6.QtWidgets import QApplication, QMessageBox
from one_dragon.utils import os_utils
from drive_disc_manager import cn_reader
from drive_disc_manager.cn_export import EXPORT_NAME
from drive_disc_manager.settings import ManagerInterface
from drive_disc_manager.store import read, save

PID = 4242
real_verify = cn_reader.verify

with TemporaryDirectory() as tmp, ExitStack() as stack:
    base = Path(tmp)
    os_utils.set_work_dir(str(base))
    account = base/'plugin_data'/'drive_disc_manager'/'1'
    state_file = account.parent/'_reader'/cn_reader.STATE
    exe = account.parent/'_reader'/cn_reader.VERSION/'zzz-drive-exporter.exe'
    exe.parent.mkdir(parents=True)
    for name in cn_reader.FILES:
        (exe.parent/name).write_bytes(b'fixture')
    # The real hash check still rejects bytes that are not the signed release.
    try:
        real_verify(exe.parent)
    except ValueError as exc:
        assert 'zzz-drive-exporter.exe' in str(exc)
    else:
        raise AssertionError('fixture bytes passed the release hash check')
    (base/'version_info').write_text('CNPRODWin3.3.0', encoding='ascii')
    ctx = SimpleNamespace(current_instance_idx=1, get_plugin_data_dir=lambda _:account.parent,
                          game_account_config=SimpleNamespace(game_path=str(base/'ZenlessZoneZero.exe')),
                          run_context=SimpleNamespace(is_context_stop=True, is_context_running=True))
    save(account/'config.json', dict(inventorySource='cn_export', auto_sync=False))
    app = QApplication([])
    widget = ManagerInterface(ctx)
    widget.export_poll.setInterval(40)

    # Every button reports failure through a modal warning; record it instead of blocking.
    warnings, launched = [], []
    alive = SimpleNamespace(is_running=lambda:True)
    def launch(**kw):
        launched.append(kw)
        return {'hProcess':SimpleNamespace(pid=PID)}
    def warn(parent, title, text, *a, **k):
        warnings.append(text)
        return QMessageBox.Ok
    # Stand-ins for the whole script: no download, no launch, no real process.
    stack.enter_context(patch.object(cn_reader, 'executable', lambda root:exe))
    stack.enter_context(patch.object(cn_reader, 'verify', lambda directory:None))
    stack.enter_context(patch.object(psutil, 'Process', lambda pid:alive))
    stack.enter_context(patch('win32process.GetProcessId', lambda handle:handle.pid, create=True))
    stack.enter_context(patch('win32com.shell.shell.ShellExecuteEx', launch, create=True))
    stack.enter_context(patch.object(QMessageBox, 'warning', warn))
    stack.enter_context(patch.object(cn_reader, 'running', lambda:[]))

    widget.on_interface_shown()
    assert not widget.reader_open.isHidden(), 'the CN account has no reader button'

    # Clicking goes through the page's own handler, which turns a refusal into a warning.
    def click():
        before = len(launched), len(warnings)
        widget.reader_open.clicked.emit()
        return len(launched)-before[0], len(warnings)-before[1]
    started, failed = click()
    assert started == 1 and failed == 0 and launched[0]['lpVerb'] == 'runas'
    assert launched[0]['fMask'] == shellcon.SEE_MASK_NOCLOSEPROCESS
    parameters = launched[0]['lpParameters']
    output = account/'exports'/EXPORT_NAME
    for flag, value in (('--capture_root', account/'cn-captures'), ('--output', output),
                        ('--calculator_output', output.with_name('zzz-drives-zzz-calculator-v1.json'))):
        assert subprocess.list2cmdline([str(value)]) in parameters, flag
    assert '--force' in parameters and str(account) in parameters
    assert output.parent.is_dir() and (account/'cn-captures').is_dir() and not output.exists()
    record = read(state_file)
    assert (record['account'], record['pid'], record['status']) == ('1', PID, 'waiting')
    # The page itself is never shown offscreen, so "not explicitly hidden" is the observable fact.
    assert cn_reader.hint()[:6] in widget.status.text() and not widget.status.isHidden()

    # Waiting: nothing is exported, the exporter runs, and polling keeps the state.
    assert cn_reader.waiting(account, PID) and cn_reader.outcome(account, PID, output) == ''
    widget.refresh_cn_reader()
    assert read(state_file)['status'] == 'waiting'
    # A stale export from an earlier run is neither this run's result nor a success.
    output.write_text('{"format":"ZOOD"}', encoding='utf-8')
    stale = record['started']-50
    os.utime(output, (stale, stale))
    assert cn_reader.outcome(account, PID, output) == '', 'a stale export was accepted'
    widget.refresh_cn_reader()
    assert read(state_file)['status'] == 'waiting', 'a stale export was reported as success'

    # Another account's exporter blocks a new capture before any prompt.
    with patch.object(cn_reader, 'running', lambda:[(7, '2')]):
        assert click() == (0, 1) and '另一个一条龙账号' in warnings[-1]
    # The same account's own running exporter is refused too.
    with patch.object(cn_reader, 'running', lambda:[(8, '1')]):
        assert click() == (0, 1) and '本账号' in warnings[-1]
    # A non-CN client is refused before any prompt appears.
    (base/'version_info').write_text('OSPRODWin3.3.0', encoding='ascii')
    assert click() == (0, 1) and '不是国服客户端' in warnings[-1]
    (base/'version_info').write_text('CNPRODWin3.3.0', encoding='ascii')
    # Declining the UAC prompt, or any launch failure, is reported in plain words and leaves no waiting state.
    for code, text, expected in ((1223, '操作已被用户取消。', '已取消管理员确认'), (5, '拒绝访问。', '拒绝访问')):
        def failure(*_, code=code, text=text, **kw):
            raise pywintypes.error(code, 'ShellExecuteEx', text)
        save(state_file, {})
        with patch('win32com.shell.shell.ShellExecuteEx', failure, create=True):
            assert click() == (0, 1)
        assert expected in warnings[-1] and read(state_file) == {}
    assert len(launched) == 1

    # This run's export arrives: the state becomes done.
    save(state_file, record)
    output.write_text('{"format":"ZOOD"}', encoding='utf-8')
    assert cn_reader.outcome(account, PID, output) == '已导出库存'
    assert cn_reader.close(account, PID) == '已导出库存' and read(state_file)['status'] == 'done'

    # The exporter gave up without exporting: the page says so instead of staying silent.
    save(state_file, dict(account='1', pid=PID, started=record['started'], status='waiting'))
    output.unlink()
    with patch.object(psutil, 'Process', side_effect=psutil.NoSuchProcess(PID)):
        assert not cn_reader.waiting(account, PID)
        widget.refresh_cn_reader()
    assert '读取器已退出' in read(state_file)['reason'] and read(state_file)['status'] == 'failed'
    assert read(state_file)['reason'] in widget.status.text()
    # A timed-out exporter is ended, and the state records why.
    save(state_file, dict(account='1', pid=PID, started=record['started']-cn_reader.WAIT_MINUTES*60-5, status='waiting'))
    assert not cn_reader.waiting(account, PID)
    ended = SimpleNamespace(is_running=lambda:True, killed=False)
    def kill():
        ended.killed = True
    ended.kill = kill
    with patch.object(psutil, 'Process', lambda pid:ended):
        assert '已结束读取器' in cn_reader.close(account, PID) and ended.killed
    # A timed-out elevated exporter cannot be ended from a normal OneDragon; say so.
    save(state_file, dict(account='1', pid=PID, started=record['started']-cn_reader.WAIT_MINUTES*60-5, status='waiting'))
    denied = SimpleNamespace(is_running=lambda:True, kill=lambda:(_ for _ in ()).throw(psutil.AccessDenied(PID)))
    with patch.object(psutil, 'Process', lambda pid:denied):
        assert '无法结束它' in cn_reader.close(account, PID)

    # The poll never changes the state while the page is computing, or for another account.
    save(state_file, dict(record))
    widget.preview_job = account
    with patch.object(psutil, 'Process', side_effect=psutil.NoSuchProcess(PID)):
        widget.refresh_cn_reader()
        assert read(state_file)['status'] == 'waiting'
    widget.preview_job = None
    save(state_file, dict(record, account='2'))
    with patch.object(psutil, 'Process', side_effect=psutil.NoSuchProcess(PID)):
        widget.refresh_cn_reader()
        assert read(state_file)['status'] == 'waiting'

    # An overseas account keeps its own reader button and never reaches the CN exporter.
    save(account/'config.json', dict(inventorySource='network', auto_sync=False))
    (base/'version_info').write_text('OSPRODWin3.3.0', encoding='ascii')
    widget.on_interface_shown()
    assert not widget.reader_open.isHidden()
    overseas, started_file = [], []
    def overseas_install(directory):
        overseas.append(directory)
        exe = base/'overseas.exe'
        exe.write_bytes(b'fixture')
        return exe
    with patch('drive_disc_manager.reader_install.install', overseas_install), \
         patch('os.startfile', lambda path, *a, **k:started_file.append(path)):
        assert click() == (0, 0)
    assert overseas == [account.parent/'_reader'] and started_file == [str(base/'overseas.exe')]
    assert len(launched) == 1, 'the overseas branch launched the CN exporter'
    # On a CN client the overseas reader stays closed, as before.
    (base/'version_info').write_text('CNPRODWin3.3.0', encoding='ascii')
    with patch('drive_disc_manager.reader_install.install', overseas_install), \
         patch('os.startfile', lambda path, *a, **k:started_file.append(path)):
        assert click() == (0, 0)
    assert len(started_file) == 1 and len(launched) == 1
    widget.on_interface_hidden()
    assert not widget.export_poll.isActive()
    widget.close()
print('PASS CN reader button, runas launch, account-bound output, stale export, timeout, declined consent')
