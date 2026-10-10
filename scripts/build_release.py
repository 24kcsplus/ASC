"""Build a source distribution from tracked runtime files."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATHS = (
    'main.py', 'LICENSE', 'src', 'docs', 'asc_gui', 'asc_mcp',
    'requirements.txt', 'requirements-gui.txt', 'requirements-gui-lock.txt',
    'requirements-mcp.txt', 'requirements-mcp-lock.txt',
    'README.md', 'README-GUI.md', 'README-MCP.md', 'VALIDATION.md',
    'install-gui.sh', 'run-gui.sh', 'install-mcp.sh', 'run-mcp.sh',
    'mcp-config.example.json',
)


def build(version, output):
    if not re.fullmatch(r'v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(?:alpha|beta|rc)\.[1-9]\d*)?', version):
        raise ValueError('expected vMAJOR.MINOR.PATCH or vMAJOR.MINOR.PATCH-rc.N (also alpha/beta)')
    entries = subprocess.check_output(
        ['git', 'ls-files', '--stage', '-z', '--', *RUNTIME_PATHS],
        cwd=ROOT).decode().split('\0')
    paths = {}
    for entry in filter(None, entries):
        metadata, path = entry.split('\t', 1)
        mode, _, stage = metadata.split()
        if stage != '0':
            raise ValueError(f'cannot package unmerged file: {path}')
        paths[path] = int(mode, 8)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'ASC-{version}-source.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for path, mode in sorted(paths.items()):
            info = zipfile.ZipInfo(f'ASC-{version}/{path}')
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = mode << 16
            package.writestr(info, (ROOT / path).read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / 'SHA256SUMS').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version')
    parser.add_argument('--output', type=Path, default=Path('dist'))
    args = parser.parse_args()
    print(build(args.version, args.output))
