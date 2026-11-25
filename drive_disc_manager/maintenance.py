"""Idle updater. The bootstrap runs this file outside the live plugin directory."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile


PLUGIN = 'drive_disc_manager'
REPO = 'EliNaCCa/OhMyZZZ'
VERSION = r'(?:0|[1-9][0-9]*)[.](?:0|[1-9][0-9]*)[.](?:0|[1-9][0-9]*)'
MAX_PACKAGE = 16 * 1024 * 1024


def read(path, default=None):
    if not path.is_file() and default is not None:
        return default
    return json.loads(path.read_text('utf-8-sig'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile('w', dir=path.parent, delete=False, encoding='utf-8') as out:
        name = Path(out.name)
        try:
            json.dump(value,out,ensure_ascii=False,indent=2)
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            out.close()
            name.unlink(missing_ok=True)
            raise
    try:
        os.replace(name,path)
    finally:
        name.unlink(missing_ok=True)


def state_dir(home):
    return home/'plugin_data'/PLUGIN/'_maintenance'


class Deferred(Exception):
    pass


@contextmanager
def exclusive(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory/'apply.lock').open('a+b') as lock:
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise Deferred('插件或维护程序正在运行') from exc
        yield


def validate_version(value):
    if not isinstance(value,str) or not re.fullmatch(VERSION,value):
        raise ValueError('发布版本格式错误')
    return tuple(map(int,value.split('.')))


def package_version(path):
    with ZipFile(path) as archive:
        infos = archive.infolist()
        names = [f.filename for f in infos]
        if not infos or len(names) != len(set(n.casefold() for n in names)) or sum(f.file_size for f in infos)>MAX_PACKAGE:
            raise ValueError('安装包布局或大小异常')
        for name in names:
            elements = name.rstrip('/').split('/')
            if elements[0]!=PLUGIN or len(elements)<2 or any(e in ('','.','..') or ':' in e or chr(92) in e for e in elements):
                raise ValueError('安装包包含插件目录以外的路径')
        meta = json.loads(archive.read(PLUGIN+'/plugin.json'))
        if meta.get('id') != PLUGIN or PLUGIN+'/'+PLUGIN+'_factory.py' not in names:
            raise ValueError('安装包缺少驱动盘管理入口')
        validate_version(meta['version'])
        return meta['version']


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archived(directory, ref):
    digest = ref['sha256']
    if not re.fullmatch('[a-f0-9]{64}',digest):
        raise ValueError('备份记录损坏')
    path = directory/'packages'/(digest+'.zip')
    if sha256(path)!=digest or package_version(path)!=ref['version']:
        raise ValueError('安装包与保存的校验值不符')
    return path


def retain(directory, temp, version):
    if package_version(temp)!=version:
        raise ValueError('安装包版本与版本号不符')
    digest = sha256(temp)
    packages = directory/'packages'
    packages.mkdir(exist_ok=True)
    os.replace(temp, packages/(digest+'.zip'))
    return dict(version=version,sha256=digest)


def backup(home, directory):
    installed = home/'plugins'/PLUGIN
    if installed.resolve()!=installed or (installed/'.git').exists():
        raise ValueError('该安装目录由外部链接或 Git 管理')
    meta = read(installed/'plugin.json')
    tmp = directory/'installed.tmp'
    with ZipFile(tmp,'w',ZIP_DEFLATED) as archive:
        for file in sorted(installed.rglob('*')):
            if file.is_symlink() or not file.resolve().is_relative_to(installed):
                raise ValueError('插件文件指向安装目录外')
            if file.is_file() and '__pycache__' not in file.parts and file.suffix!='.pyc':
                archive.write(file, PLUGIN+'/'+file.relative_to(installed).as_posix())
    return retain(directory,tmp,meta['version'])


def fetch(url, maximum):
    req = Request(url, headers={'User-Agent':'OhMyZZZ-idle-maintenance'})
    with urlopen(req, timeout=12) as response:
        data = response.read(maximum+1)
    if len(data)>maximum:
        raise ValueError('发布响应超过大小上限')
    return data


def published_release(wanted=None):
    if wanted is not None:
        validate_version(wanted)
        release=json.loads(fetch(f'https://api.github.com/repos/{REPO}/releases/tags/manager-v{wanted}',2_000_000))
        if release.get('tag_name')!='manager-v'+wanted or release.get('draft'):
            raise ValueError('已选择的发布不可用')
        return wanted,release
    releases = json.loads(fetch('https://api.github.com/repos/'+REPO+'/releases?per_page=50',2_000_000))
    candidates = []
    for release in releases:
        match = re.fullmatch('manager-v('+VERSION+')',release.get('tag_name',''))
        if match and not release['draft']:
            candidates.append((validate_version(match[1]),match[1],release))
    if not candidates:
        raise ValueError('仓库没有已发布的插件版本')
    _,version,release=max(candidates,key=lambda entry:entry[0])
    return version,release


def download(directory, version, release):
    assets = {a['name']:a for a in release['assets']}
    name = f'OhMyZZZ-{version}.zip'
    if name not in assets and validate_version(version)<(0,1,5):
        name = f'{PLUGIN}-{version}.zip'
    base = f'https://github.com/{REPO}/releases/download/manager-v{version}/'
    for required in (name,name+'.sha256'):
        if required not in assets or assets[required]['browser_download_url'] != base+required:
            raise ValueError('该版本缺少正式安装包或校验文件')
    checksum = fetch(base+name+'.sha256',512).decode('ascii').split()
    raw = fetch(base+name,MAX_PACKAGE)
    digest = hashlib.sha256(raw).hexdigest()
    if checksum != [digest,name] or assets[name].get('digest') not in (None,'sha256:'+digest):
        raise ValueError('发布包 SHA-256 校验失败')
    tmp = directory/'download.tmp'
    tmp.write_bytes(raw)
    return retain(directory,tmp,version)


def require_idle(home):
    import psutil
    for proc in psutil.process_iter(['pid','name','exe','cmdline']):
        if proc.pid == os.getpid():
            continue
        info = proc.info
        name = (info['name'] or '').lower()
        if name.startswith(('onedragon-', 'zenlesszonezero')):
            raise Deferred('绝区零或一条龙正在运行')
        args = ' '.join(info['cmdline'] or ()).lower().replace(chr(92),'/')
        if name.startswith(('python', 'pwsh', 'powershell')) and any(token in args for token in (
                'zzz_od.', 'zzz_od/', 'run-verifiedonedragon.py','invoke-retryingtask.ps1',
                'update-onedragon','early_entry.py')):
            raise Deferred('正在执行一条龙任务或维护')
        if name.startswith('python') and info['exe'] and Path(info['exe']).is_relative_to(home) and not args:
            raise Deferred('无法确认其他一条龙进程的运行状态')


def install(home, path):
    sys.path.insert(0,str(home/'src'))
    from one_dragon.utils import os_utils
    from one_dragon.base.operation.application.plugin_import_service import PluginImportService
    from zzz_od.context.zzz_context import ZContext
    os_utils.set_work_dir(str(home))
    result = PluginImportService(ZContext.__new__(ZContext)).import_plugin(path,overwrite=True)
    if not result.success:
        raise ValueError(result.message)


def smoke_installed(home, version):
    # A fresh interpreter avoids sys.modules retaining the previous plugin.
    code = '''
import json,sys
from pathlib import Path
home=Path(sys.argv[1]); sys.path[:0]=[str(home/'plugins'),str(home/'src')]
from one_dragon.utils import os_utils
os_utils.set_work_dir(str(home))
from drive_disc_manager import settings, manager_app, drive_disc_manager_factory
from drive_disc_manager.drive_disc_manager_const import PLUGIN_VERSION
from drive_disc_manager.validation import validate
root=home/'plugins'/'drive_disc_manager'
if Path(settings.__file__).resolve().parent!=root or PLUGIN_VERSION!=sys.argv[2]:
    raise RuntimeError('安装版路径或版本不匹配')
validate({})
for cfg in (home/'plugin_data'/'drive_disc_manager').glob('*/config.json'):
    if cfg.parent.name.isdecimal(): validate(json.loads(cfg.read_text('utf-8-sig')))
'''
    subprocess.run([sys.executable,'-I','-c',code,str(home),version],cwd=home,check=True,timeout=45)


def restore(home, directory, old, apply, verify):
    apply(home,archived(directory,old))
    verify(home,old['version'])


def refresh_worker(home, directory):
    source=home/'plugins'/PLUGIN/'maintenance.py'
    if source.is_file():
        temp=directory/'worker.py.tmp'
        temp.write_bytes(source.read_bytes())
        os.replace(temp,directory/'worker.py')


def switch(home, directory, target, mode, idle=require_idle, apply=install, verify=smoke_installed, request=None):
    idle(home)
    old = backup(home,directory)
    package = archived(directory,target)
    journal = directory/'pending.json'
    idle(home)
    write(journal,dict(old=old,target=target,mode=mode,priorBackup=read(directory/'previous.json',{}),request=request))
    try:
        apply(home,package)
        verify(home,target['version'])
    except Exception:
        restore(home,directory,old,apply,verify)
        refresh_worker(home,directory)
        write(directory/'hold.json',dict(version=target['version']))
        if request and request==read(directory/'request.json',{}):
            write(directory/'request.json',{})
        journal.unlink()
        raise
    write(directory/'previous.json',old)
    write(directory/'hold.json',dict(version=old['version'] if mode=='rollback' else ''))
    refresh_worker(home,directory)
    # Commit point. A crash before this point restores old on the next idle run.
    journal.unlink()
    return f"{old['version']} → {target['version']}"


def maintain(home, action, idle=require_idle, apply=install, verify=smoke_installed):
    directory=state_dir(home)
    with exclusive(directory):
        pending=directory/'pending.json'
        if pending.exists():
            idle(home)
            txn=read(pending)
            restore(home,directory,txn['old'],apply,verify)
            refresh_worker(home,directory)
            write(directory/'hold.json',dict(version=txn['target']['version']))
            if 'priorBackup' in txn:
                write(directory/'previous.json',txn['priorBackup'])
            if txn.get('request') and txn['request']==read(directory/'request.json',{}):
                write(directory/'request.json',{})
            pending.unlink()
            return '已从中断恢复 '+txn['old']['version']
        queued=read(directory/'request.json',{})
        from_queue = action=='auto' and bool(queued)
        if action=='auto':
            action=queued.get('action') or 'off'
        if action=='off': return '没有已确认的版本变更'
        idle(home)
        live=read(home/'plugins'/PLUGIN/'plugin.json')['version']
        if action=='rollback':
            old=read(directory/'previous.json',{})
            if not old: raise ValueError('尚无可回退的版本')
            # A queued rollback names its exact target, including after a crash.
            wanted=queued.get('version') if from_queue else old['version']
            if live==wanted: result='已在指定版本 '+live
            elif old['version']!=wanted: raise ValueError('备份已变化，请重新选择回退版本')
            else: result=switch(home,directory,old,action,idle,apply,verify,queued if from_queue else None)
        else:
            version,release=published_release(queued['version'] if from_queue else None)
            held=read(directory/'hold.json',{}).get('version')
            if validate_version(version)<=validate_version(live): result='当前版本 '+live
            elif held==version and action!='retry': result='保留当前版本，已跳过 '+version
            elif action=='check': result='有更新 '+version
            else: result=switch(home,directory,download(directory,version,release),'update',idle,apply,verify,queued if from_queue else None)
        if from_queue and queued==read(directory/'request.json',{}):
            write(directory/'request.json',{})
        return result


def main():
    parser=argparse.ArgumentParser(description='OhMyZZZ 空闲版本维护')
    parser.add_argument('--host',required=True,type=Path)
    parser.add_argument('--wait-pid',type=int)
    parser.add_argument('--created',type=float)
    parser.add_argument('action',choices=['auto','check','update','retry','rollback'],default='auto',nargs='?')
    args=parser.parse_args()
    home=args.host.resolve()
    if not (home/'src/zzz_od').is_dir(): parser.error('需要一条龙安装目录')
    directory=state_dir(home)
    try:
        if args.wait_pid and args.created:
            import psutil
            try:
                parent=psutil.Process(args.wait_pid)
                if abs(parent.create_time()-args.created)<0.01:
                    parent.wait(timeout=120)
            except psutil.NoSuchProcess:
                pass
            except psutil.TimeoutExpired as exc:
                raise Deferred('等待一条龙正常退出，已保留下次处理的请求') from exc
        result=maintain(home,args.action)
        status='ok'
    except Deferred as exc:
        status,result='deferred',str(exc)
    except Exception as exc:
        status,result='failed',str(exc)
    write(directory/'last-run.json',dict(status=status,message=result,time=time.time()))
    print(result)
    return int(status=='failed')


if __name__=='__main__':
    sys.exit(main())
