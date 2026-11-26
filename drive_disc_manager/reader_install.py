"""Install the pinned Windows reader without starting it or loading its driver."""
import hashlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zipfile import ZipFile


VERSION = '3.3.1'
URL = 'https://github.com/pingwinekZ/zzz_packet_capture_0delay/releases/download/v3.3.1/zzz-packet-capture-0delay-win64.zip'
SHA256 = '7a14d811bb07a1725c09a25ae3e0474170fee133283df036cdb4ab2102d29bbf'
BINARIES = {'zzzgui.exe':'03370b5832024584996484e991a8ec92951e94df99069589c4b7ad1f3314f759',
            'WinDivert.dll':'c1e060ee19444a259b2162f8af0f3fe8c4428a1c6f694dce20de194ac8d7d9a2',
            'WinDivert64.sys':'8da085332782708d8767bcace5327a6ec7283c17cfb85e40b03cd2323a90ddc2'}


def install(directory):
    dest = Path(directory) / VERSION
    exe = dest / 'zzzgui.exe'
    if exe.is_file():
        for name, checksum in BINARIES.items():
            if hashlib.sha256((dest/name).read_bytes()).hexdigest() != checksum:
                raise ValueError('读取器文件已变化，请重新安装')
        return exe
    with urlopen(URL, timeout=30) as response:
        payload = response.read(10_000_001)
    if hashlib.sha256(payload).hexdigest() != SHA256:
        raise ValueError('读取器下载不完整，请重试')
    dest.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=dest.parent) as tmp, ZipFile(io.BytesIO(payload)) as archive:
        staging = Path(tmp) / VERSION
        archive.extractall(staging)  # Only the exact allowlisted release hash reaches extraction.
        if not (staging/'zzzgui.exe').is_file():
            raise ValueError('读取器安装包缺少启动程序')
        try:
            staging.rename(dest)
        except FileExistsError:
            if not exe.is_file():
                raise
    return exe
