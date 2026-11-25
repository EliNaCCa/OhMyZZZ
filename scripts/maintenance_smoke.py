"""One release pair in a temp host. No live install, game or network."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from drive_disc_manager import maintenance as m


def run():
    with TemporaryDirectory() as tmp:
        home = Path(tmp).resolve()
        directory = m.state_dir(home)
        directory.mkdir(parents=True)
        live = home/'plugins'/m.PLUGIN
        live.mkdir(parents=True)
        cfg = directory.parent/'01'/'config.json'
        m.write(cfg,dict(profiles=[{'name':'冰队'}],overrides={'matrix/test/4':{'mode':'only','values':['crit_']}}))
        original = cfg.read_bytes()
        m.write(live/'plugin.json', dict(id=m.PLUGIN,version='0.1.4'))
        factory = m.PLUGIN+'_factory.py'
        (live/factory).write_text('old=True','utf8')
        package = directory/'new.tmp'
        with ZipFile(package,'w') as archive:
            archive.writestr(m.PLUGIN+'/plugin.json',json.dumps(dict(id=m.PLUGIN,version='0.1.5')))
            archive.writestr(m.PLUGIN+'/'+factory,'old=False')
        target = m.retain(directory,package,'0.1.5')

        def apply(_home, path):
            with ZipFile(path) as archive:
                for file in ('plugin.json',factory):
                    (live/file).write_bytes(archive.read(m.PLUGIN+'/'+file))

        def verify(_home, version):
            assert m.read(live/'plugin.json')['version']==version
            assert cfg.read_bytes()==original

        def idle(_home): pass
        class PowerLoss(BaseException): pass
        def interrupt(_home, path):
            apply(_home,path)
            raise PowerLoss()

        try:
            m.switch(home,directory,target,'update',idle,interrupt,verify)
        except PowerLoss:
            assert (directory/'pending.json').is_file()
        else: raise AssertionError('interruption was not injected')
        m.maintain(home,'auto',idle,apply,verify)
        verify(home,'0.1.4')
        assert not (directory/'pending.json').exists()
        m.switch(home,directory,target,'update',idle,apply,verify)
        m.maintain(home,'rollback',idle,apply,verify)
        verify(home,'0.1.4')
        assert m.read(directory/'hold.json')['version']=='0.1.5'
        print('maintenance smoke OK: interrupted install, recovery, update, rollback, account config')


if __name__=='__main__': run()
