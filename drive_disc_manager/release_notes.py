"""Player-facing notes shared by the installed plugin and the release workflow."""
import re
from pathlib import Path


CHANGELOG = Path(__file__).with_name('CHANGELOG.md')


def sections(document):
    lines = document.splitlines()
    sections = {}
    heading = None
    for line in lines:
        if line.startswith('## '):
            heading = line[3:].strip()
            if heading in sections:
                raise ValueError(f'更新日志有重复标题 · {heading}')
            sections[heading] = []
        elif heading is not None:
            sections[heading].append(line)
    return {title: '\n'.join(body).strip() for title, body in sections.items()}


def plain_notes(body):
    # Display bullets as plain text in Qt. Release contents never supply HTML.
    return '\n'.join(re.sub(r'^[-*] ', '• ', line).replace('**', '') for line in body.splitlines()).strip()


def installed_history(version):
    from .maintenance import validate_version
    current = validate_version(version)
    notes = sections(CHANGELOG.read_text('utf-8'))
    return '\n\n'.join(title+'\n'+plain_notes(body) for title, body in notes.items()
                         if re.fullmatch(r'[0-9]+[.][0-9]+[.][0-9]+', title) and validate_version(title) <= current and body)


def published_notes(tag):
    from .drive_disc_manager_const import PLUGIN_VERSION
    from .maintenance import read
    package_version = read(CHANGELOG.with_name('plugin.json'))['version']
    if tag != 'manager-v'+package_version or PLUGIN_VERSION != package_version:
        raise ValueError('发布标签和插件版本须一致')
    body = sections(CHANGELOG.read_text('utf-8')).get(package_version, '')
    if not body or not any(line.startswith('- ') and line[2:].strip() for line in body.splitlines()):
        raise ValueError(f'请先为 {package_version} 填写面向玩家的更新条目')
    return body


if __name__ == '__main__':
    import sys
    print(published_notes(sys.argv[1]))
