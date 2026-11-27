"""Install committed plugin changes locally and smoke-load them in a fresh process."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from drive_disc_manager import maintenance
from drive_disc_manager.drive_disc_manager_const import PLUGIN_VERSION


def git(*args):
    return subprocess.check_output(['git', *args], cwd=REPO)


def write(path, data):
    if data is None:
        path.unlink()
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        stream.write(data)
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def normalized(data):
    if data is None or b'\0' in data:
        return data
    return data.replace(b'\r\n', b'\n')


def install(home, base):
    directory = maintenance.state_dir(home)
    plugin = home / 'plugins' / 'drive_disc_manager'
    if not plugin.is_dir():
        raise ValueError('请先在一条龙安装 OhMyZZZ')
    base = git('rev-parse', '--verify', '--end-of-options', base + '^{commit}').decode().strip()
    files = git('diff', '--name-only', '--no-renames', '-z', base, 'HEAD', '--', 'drive_disc_manager').decode('utf-8')
    changes = []
    with maintenance.exclusive(directory):
        if (directory/'pending.json').exists() or maintenance.read(directory/'request.json', {}):
            raise ValueError('一条龙正在更新插件')
        for name in filter(None, files.split('\0')):
            dest = (home/'plugins'/name).resolve()
            if not dest.is_relative_to(plugin.resolve()) or not dest.parent.is_dir():
                raise ValueError('请核对安装目录 ' + name)
            before = git('show', base + ':' + name) if git('ls-tree', base, '--', name) else None
            after = git('show', 'HEAD:' + name) if git('ls-tree', 'HEAD', '--', name) else None
            src = REPO/name
            if normalized(after) != normalized(src.read_bytes() if src.is_file() else None):
                raise ValueError('本地文件有未提交的修改 ' + name)
            current = dest.read_bytes() if dest.is_file() else None
            if normalized(current) == normalized(after):
                continue
            if normalized(current) != normalized(before):
                raise ValueError('安装目录有其他修改 ' + name)
            changes.append((dest, current, after))
        touched = []
        try:
            for dest, current, after in changes:
                write(dest, after)
                touched.append((dest, current))
            maintenance.smoke_installed(home, PLUGIN_VERSION)
        except BaseException:
            for dest, current in reversed(touched):
                write(dest, current)
            raise
    print(f'OhMyZZZ {PLUGIN_VERSION} 已加载，更新 {len(changes)} 个文件')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path('F:/OneDragon'))
    parser.add_argument('--base', default='HEAD^', help='已安装的本地提交，默认上一个提交')
    args = parser.parse_args()
    install(args.home.resolve(), args.base)
