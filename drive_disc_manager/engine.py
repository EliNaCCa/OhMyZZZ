"""Run the unmodified upstream optimizer for one account and one operation."""
import hashlib
import os
import time
from uuid import uuid4
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

from .maintenance import exclusive, fetch


VERSION = '2.3.31.1'
SHA256 = '917d5bf4f87a2dd2e7bd236ea61070203ce3f45a39cc393ab86a4c209421ca6c'
SIZE = 15_700_480
NAME = f'ZZZ_Multi_Agent_Drive_Optimizer_v{VERSION}.exe'
RELEASE = f'https://github.com/xliuforge/ZZZ_Multi_Agent_Drive_Optimizer/releases/download/v{VERSION}/'


def executable(root):
    directory = root.parent/'_optimizer'/VERSION
    path = directory/NAME
    with exclusive(directory):
        if path.exists():
            if path.stat().st_size != SIZE or hashlib.sha256(path.read_bytes()).hexdigest() != SHA256:
                raise ValueError('本地配装器校验失败 · '+str(path))
            return path.resolve()
        raw = fetch(RELEASE + NAME, SIZE)
        if len(raw) != SIZE or hashlib.sha256(raw).hexdigest() != SHA256:
            raise ValueError('上游配装器下载校验失败')
        with NamedTemporaryFile(dir=directory, delete=False) as out:
            temp = Path(out.name)
        try:
            temp.write_bytes(raw)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
    return path.resolve()


class Endpoint:
    def __init__(self, proc, port):
        self.proc = proc
        self.url = f'http://127.0.0.1:{port}'
        self.port = port

    def check_connection(self, connection):
        # Verify ownership of THIS established connection before sending any data.
        local = connection.sock.getsockname()
        deadline = time.monotonic() + 1
        while self.proc.is_running():
            if any(c.laddr == ('127.0.0.1', self.port) and c.raddr == local
                   for c in self.proc.net_connections(kind='tcp4')):
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(.025)
        raise ValueError('本次连接不属于当前账号启动的配装器')


@contextmanager
def managed(root):
    import psutil
    import win32event
    import win32job
    import win32process
    from .optimizer import loopback_json

    data = (root/'optimizer').resolve()
    with exclusive(data):
        if (data/'storage-config.json').is_file():
            from .store import read
            custom = read(data/'storage-config.json').get('storagePath')
            if custom and Path(custom).resolve() != data/'state.json':
                raise ValueError('托管配装器的数据路径已改动，请在配装器恢复本账号默认路径')
        path = executable(root)
        env = dict(os.environ, ZZZ_APP_DATA_ROOT=str(data), ZZZ_LEGACY_CONFIG_ROOT=str(data))
        job = win32job.CreateJobObject(None, 'OhMyZZZ-' + uuid4().hex)
        process = thread = None
        try:
            info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
            limits = info['BasicLimitInformation']
            limits['ActiveProcessLimit'] = 1
            limits['LimitFlags'] = win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS | win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
            # The upstream opens a browser on startup. Only an explicit UI action may open one.
            process, thread, pid, _ = win32process.CreateProcess(str(path), f'"{path}"', None, None,
                False, win32process.CREATE_SUSPENDED | win32process.CREATE_NO_WINDOW,
                env, str(data), win32process.STARTUPINFO())
            win32job.AssignProcessToJobObject(job, process)
            proc = psutil.Process(pid)
            win32process.ResumeThread(thread)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and proc.is_running():
                ports = [c.laddr.port for c in proc.net_connections(kind='tcp4')
                         if c.status == psutil.CONN_LISTEN and c.laddr.ip == '127.0.0.1']
                if len(ports) == 1:
                    break
                time.sleep(.05)
            else:
                raise ValueError('本地配装器未能启动')
            endpoint = Endpoint(proc, ports[0])
            state = loopback_json(endpoint, '/api/state')
            if not state.get('storagePath') or Path(state['storagePath']).resolve() != data/'state.json':
                raise ValueError('配装器数据目录与本账号不一致')
            yield endpoint
        finally:
            if thread is not None:
                thread.Close()
            try:
                if process is not None and win32event.WaitForSingleObject(process, 0) == win32event.WAIT_TIMEOUT:
                    win32process.TerminateProcess(process, 0)
                    win32event.WaitForSingleObject(process, 3000)
            finally:
                if process is not None:
                    process.Close()
                job.Close()
