"""Build tenant-neutral immutable packages and sign the catalog off-server."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import zipfile

from init_installer import atomic_json


def build_package(root, output, host):
    root, output = Path(root), Path(output)
    hostroot = root if host == 'codex' else root/'claude'
    manifest_path = '.codex-plugin/plugin.json' if host == 'codex' else '.claude-plugin/plugin.json'
    manifest = json.loads((hostroot/manifest_path).read_text(encoding='utf-8'))
    # Reuse the release inventory; never glob arbitrary scripts into executable releases.
    inventory = re.search(r'RUNTIME_FILES=\((.*?)\)', (root/'build_release.sh').read_text(), re.S).group(1).split()
    inventory = sorted(set(inventory + ['init_installer.py', 'installation_health.py']))
    files = {f'scripts/{name}': root/'scripts'/name for name in inventory}
    files[manifest_path] = hostroot/manifest_path
    files['hooks/hooks.json'] = hostroot/'hooks/hooks.json'
    files.update({name: root/name for name in ['INSTALL.md', 'AGENT-INSTALL.md', 'CODEX.md', 'LICENSE']})
    blob = io.BytesIO()
    forbidden = re.compile(rb'bos_[A-Za-z0-9]{12,}|sk-[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY')
    with zipfile.ZipFile(blob, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(files.items()):
            raw = path.read_bytes().replace(b'\r\n', b'\n')
            if forbidden.search(raw):
                raise ValueError('Credential-shaped content in release file: '+name)
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)
    raw = blob.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    artifacts = output/'artifacts'
    artifacts.mkdir(parents=True, exist_ok=True)
    path = artifacts/(digest+'.zip')
    if path.exists() and path.read_bytes() != raw:
        raise ValueError('Immutable artifact collision')
    path.write_bytes(raw)
    events = sorted(json.loads((hostroot/'hooks/hooks.json').read_text())['hooks'])
    return {'version': manifest['version'], 'sha256': digest, 'size': len(raw),
            'python_min': [3, 10], 'scope': {'events': events,
                'network_hosts': ['v3.askboswell.com'],
                'write_roots': ['boswell-state', 'boswell-transcripts']}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--signing-key', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output/'catalog.json').exists():
        raise SystemExit('Use a fresh release output directory; catalogs are immutable snapshots.')
    releases = {host: build_package(root, args.output, host) for host in ('claude-code', 'codex')}
    catalog = {'schema': 1, 'publisher': 'boswell-hooks', 'channel': 'stable', 'releases': releases}
    atomic_json(args.output/'catalog.json', catalog)
    subprocess.run(['ssh-keygen', '-Y', 'sign', '-f', str(args.signing_key), '-n', 'boswell-init',
                    str(args.output/'catalog.json')], check=True)
    print(json.dumps(catalog, indent=2))


if __name__ == '__main__':
    main()
