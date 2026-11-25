import hashlib
import json
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED

root=Path(__file__).resolve().parents[1]; pkg=root/'drive_disc_manager'
meta=json.loads((pkg/'plugin.json').read_text('utf8')); dist=root/'dist'; dist.mkdir(exist_ok=True)
out=dist/f"OhMyZZZ-{meta['version']}.zip"
with ZipFile(out,'w',ZIP_DEFLATED) as archive:
    for file in sorted(pkg.rglob('*')):
        if file.is_file() and '__pycache__' not in file.parts and file.suffix!='.pyc':
            archive.write(file,file.relative_to(root).as_posix())
    archive.write(root/'LICENSE',pkg.name+'/LICENSE')
sha=hashlib.sha256(out.read_bytes()).hexdigest()
out.with_suffix('.zip.sha256').write_text(f'{sha}  {out.name}\n', encoding='ascii')
print(out.name,sha)
