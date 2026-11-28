"""Install and run the pinned CN exporter. It only watches login traffic with pktmon."""
import hashlib
import io
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zipfile import ZipFile

from .store import read, save


VERSION = '0.2.1'
URL = 'https://github.com/Treageer/zzz-drive-exporter/releases/download/v{}/zzz-drive-exporter-v{}-windows-x86_64.zip'.format(VERSION, VERSION)
SHA256 = 'c2520492e35ecebfc37ba35358d2b160bfa7af776ca499de5fcb537b5a99d7e8'
FILES = {'zzz-drive-exporter.exe': 'e6e0298877b91839e99884636cafa40a4d76c57666a95f31cad2f3c9a0caa549',
         'LICENSE': '3c714645aa9989b8e620f141fdd99fd265b166312da06c647f89cc41c5d302f5',
         'README.md': 'a377ae7053d560ee99517def3fc98a74f97ef418831509598d79e7faed4fbb59',
         'RELEASE_NOTES.md': 'f128979700521b051fbc08bfeb21561631afab5997cfbaf7a6b0315fa0d7a1a6',
         'THIRD_PARTY_NOTICES.md': 'c73d34e2bba4400250eacebd9535723b9da5ee83cb06f8a75256cbe6fda0a2c3',
         'CHANGELOG.md': '312ae8bca971e8239462f673b2f94cdc284080d0a5087469bca27de02176465e'}
CLIENT_BUILD = 'CNPRODWin3.3.0'
STATE = 'cn-reader-state.json'
# One login usually suffices. On a failed single-session recovery the exporter
# asks the player to log in a second time, so allow for that fallback.
WAIT_MINUTES = 20


def executable(root):
    dest = Path(root)/VERSION
    exe = dest/'zzz-drive-exporter.exe'
    if exe.is_file():
        verify(dest)
        return exe
    with urlopen(URL, timeout=30) as response:
        payload = response.read(4_000_001)
    if hashlib.sha256(payload).hexdigest() != SHA256:
        raise ValueError('国服读取器下载不完整，请重试')
    dest.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=dest.parent) as tmp, ZipFile(io.BytesIO(payload)) as archive:
        staging = Path(tmp)/VERSION
        staging.mkdir()
        # Only the exact allowlisted release hash reaches extraction.
        for name in FILES:
            (staging/name).write_bytes(archive.read('zzz-drive-exporter-v{}-windows-x86_64/{}'.format(VERSION, name)))
        verify(staging)
        try:
            staging.rename(dest)
        except FileExistsError:
            if not exe.is_file():
                raise
    return exe


def verify(directory):
    for name, checksum in FILES.items():
        path = Path(directory)/name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError('国服读取器文件已变化，请重新安装 · '+name)


def arguments(root, exe):
    """The export lands where this account already reads from; captures stay inside the account."""
    from .cn_export import EXPORT_NAME, source_path
    output = source_path(root, read(root/'cn-inventory.json', {}))
    captures = root/'cn-captures'
    return output, [str(exe), '--capture_root', str(captures), '--output', str(output),
                    '--calculator_output', str(output.with_name('zzz-drives-zzz-calculator-v1.json')), '--force']


def running():
    """Exporter processes now running, as (pid, the account folder it was started for)."""
    import psutil
    found = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        if (proc.info['name'] or '').casefold() != 'zzz-drive-exporter.exe':
            continue
        args = proc.info['cmdline'] or []
        account = next((Path(arg).parent.name for i, arg in enumerate(args[:-1]) if arg.casefold() == '--capture_root'), '')
        found.append((proc.pid, account))
    return found


def saved(root):
    return read(root.parent/'_reader'/STATE, {})


def start(root, is_cn):
    """Launch the exporter for this account, then the player logs in within WAIT_MINUTES."""
    exe = executable(root.parent/'_reader')
    output, args = arguments(root, exe)
    output.parent.mkdir(parents=True, exist_ok=True)
    (root/'cn-captures').mkdir(parents=True, exist_ok=True)
    for pid, account in running():
        # Two capture programs at once corrupt each other's reassembly.
        whose = '本账号' if account == root.name else '另一个一条龙账号' if account else '手动启动的'
        raise ValueError(f'国服读取器已在运行（{whose}的上一次抓包还没结束）。请等它完成，或在它的窗口按 Ctrl+C 结束')
    if not is_cn():
        raise ValueError('当前游戏目录不是国服客户端。读取器 v{} 只支持 {}'.format(VERSION, CLIENT_BUILD))
    import win32com.shell.shell as shell
    import win32com.shell.shellcon as shellcon
    import win32process
    import pywintypes
    started = time.time()
    # runas shows the UAC prompt, so this call returns only after the player answers it.
    try:
        info = shell.ShellExecuteEx(fMask=shellcon.SEE_MASK_NOCLOSEPROCESS, lpVerb='runas', lpFile=str(exe),
                                    lpParameters=subprocess.list2cmdline(args[1:]), lpDirectory=str(exe.parent), nShow=1)
    except pywintypes.error as exc:
        # 1223 is the cancelled consent prompt. Report any other failure with Windows' own words.
        detail = '已取消管理员确认。读取器需要管理员权限才能监听登录通信' if exc.winerror == 1223 else '无法启动国服读取器 · ' + str(exc.strerror or exc)
        raise ValueError(detail) from exc
    pid = win32process.GetProcessId(info['hProcess'])
    save(root.parent/'_reader'/STATE, dict(account=root.name, pid=pid, started=started, status='waiting'))
    return pid


def outcome(root, pid, output):
    """Why the last exporter stopped, in the player's words. '' while it is still working."""
    import psutil
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        proc = None
    record = saved(root)
    if output.is_file() and output.stat().st_size > 0 and output.stat().st_mtime >= record.get('started', 0):
        return '已导出库存'
    if proc is not None and proc.is_running():
        if time.time() - record.get('started', time.time()) < WAIT_MINUTES * 60:
            return ''
        try:
            proc.kill()
        except psutil.AccessDenied:
            # An elevated exporter outlives a non-elevated OneDragon. Say so instead of claiming it stopped.
            return (f'等待登录超过 {WAIT_MINUTES} 分钟。读取器以管理员身份运行，插件无法结束它，'
                    '请在它的窗口按 Ctrl+C')
        return f'等待登录超过 {WAIT_MINUTES} 分钟，已结束读取器。请确认游戏停在登录页后再试'
    return (f'读取器已退出，但没有导出库存。请确认游戏停在登录页、客户端为 {CLIENT_BUILD}，'
            '并查看它窗口里的提示')


def hint():
    """What the player does after clicking the button."""
    return ('读取器窗口出现「Capture started」后，再登录游戏进入世界。'
            '如果它提示按 Enter，就保持驱动盘不变、完全退出游戏、停在登录页，再回到它的窗口按 Enter 并重新登录')


def waiting(root, pid):
    """True while the exporter is still running inside its time limit."""
    import psutil
    try:
        return (psutil.Process(pid).is_running()
                and time.time() - saved(root).get('started', time.time()) < WAIT_MINUTES * 60)
    except psutil.NoSuchProcess:
        return False


def close(root, pid):
    """Record the final result of this account's exporter."""
    output, _ = arguments(root, Path())
    result = outcome(root, pid, output)
    if result:
        save(root.parent/'_reader'/STATE, dict(saved(root), status='done' if result == '已导出库存' else 'failed',
                                              reason=result, finished=time.time()))
    return result
