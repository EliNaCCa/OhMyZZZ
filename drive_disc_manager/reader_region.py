"""Route the current OneDragon account by the installed client's public build label."""
from pathlib import Path
import re

from .store import read, save


def cn_client(ctx):
    game = getattr(getattr(ctx, 'game_account_config', None), 'game_path', '')
    if not game or Path(game).name.casefold() != 'zenlesszonezero.exe':
        return False
    try:
        with Path(game).with_name('version_info').open('rb') as stream:
            build = stream.read(65)
    except OSError:
        return False
    return len(build) <= 64 and re.fullmatch(rb'CNPRODWin[0-9]+(?:[.][0-9]+){2,3}', build.strip()) is not None


def align_source(ctx, root):
    """Keep an explicit OCR choice. Migrate an overseas source only for a confirmed CN client."""
    from .maintenance import exclusive
    if not cn_client(ctx) or root.name != str(ctx.current_instance_idx):
        return False
    path = root/'config.json'
    if read(path, {}).get('inventorySource') in (None, 'network'):
        with exclusive(root/'_rules'):
            settings = read(path, {})
            if root.name == str(ctx.current_instance_idx) and settings.get('inventorySource') in (None, 'network'):
                save(path, dict(settings, inventorySource='cn_export'))
    return True
