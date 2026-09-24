"""Boswell retail installer: verify, stage, invoke the host manager, measure.

Run only within existing user authorization. Local records describe installation
state; they cannot create consent. Requires Python and OpenSSH ssh-keygen with
SSH signature support. No daemon, credentials, package scripts, or shell eval.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

MAX_CATALOG = 65536
MAX_PACKAGE = 8 * 1024 * 1024
MAX_EXPANDED = 32 * 1024 * 1024
HOSTS = ('claude-code', 'codex')
MARKET = 'boswell-init'


class InstallError(Exception):
    pass


class InspectionUnavailable(InstallError):
    def __init__(self, reason_code):
        self.reason_code = reason_code
        super().__init__('Cannot inspect host plugin installation (' + reason_code +
                         '); rerun from a normal host terminal with the same profile before repair')


def read(path, limit):
    with Path(path).open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise InstallError('File exceeds size limit')
    return data


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_json(path):
    return json.loads(read(path, MAX_CATALOG)) if Path(path).exists() else None


@contextmanager
def install_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root/'install.lock').open('a+b') as stream:
        stream.seek(0)
        if stream.read(1) == b'':
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise InstallError('Another installation is running') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def key_bytes(path):
    fields = read(path, 8192).decode('ascii').strip().split()
    if len(fields) < 2 or fields[0] != 'ssh-ed25519':
        raise InstallError('Expected an Ed25519 publisher key')
    return (' '.join(fields[:2]) + '\n').encode('ascii')


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,6}\.\d{1,6}\.\d{1,6}', value):
        raise InstallError('Invalid release version')
    return tuple(map(int, value.split('.')))


def verify_catalog(manifest, signature, key):
    executable = shutil.which('ssh-keygen')
    if not executable:
        raise InstallError('OpenSSH ssh-keygen is required for signature verification')
    raw = read(manifest, MAX_CATALOG)
    read(signature, 8192)
    with tempfile.TemporaryDirectory(prefix='boswell-verify-') as temporary:
        allowed = Path(temporary)/'allowed_signers'
        allowed.write_bytes(b'boswell-release ' + key_bytes(key))
        result = subprocess.run([executable, '-Y', 'verify', '-f', str(allowed),
                                 '-I', 'boswell-release', '-n', 'boswell-init', '-s', str(signature)],
                                input=raw, capture_output=True, timeout=15)
    if result.returncode:
        raise InstallError('Publisher signature verification failed')
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise InstallError('Invalid signed catalog') from exc
    if (not isinstance(data, dict) or data.get('schema') != 1 or data.get('publisher') != 'boswell-hooks'
            or data.get('channel') != 'stable' or not isinstance(data.get('releases'), dict)):
        raise InstallError('Unsupported signed catalog')
    return data


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise InstallError('Catalog/artifact redirects are not permitted')


def download(url, target, maximum):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise InstallError('Use a credential-free HTTPS catalog URL')
    with urllib.request.build_opener(NoRedirect).open(url, timeout=30) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise InstallError('Download exceeds size limit')
    target.write_bytes(data)


def acquire(source, stage, key, host):
    remote = isinstance(source, str) and source.startswith('https://')
    if remote:
        if not source.endswith('/catalog.json'):
            raise InstallError('Expected an HTTPS /catalog.json URL')
        download(source, stage/'catalog.json', MAX_CATALOG)
        download(source + '.sig', stage/'catalog.json.sig', 8192)
    else:
        for name, limit in [('catalog.json', MAX_CATALOG), ('catalog.json.sig', 8192)]:
            (stage/name).write_bytes(read(Path(source)/name, limit))
    catalog = verify_catalog(stage/'catalog.json', stage/'catalog.json.sig', key)
    release = catalog['releases'].get(host)
    if not isinstance(release, dict):
        raise InstallError('No release for this host')
    version(release.get('version'))
    digest = release.get('sha256')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise InstallError('Invalid artifact digest')
    if type(release.get('size')) is not int or not 0 < release['size'] <= MAX_PACKAGE:
        raise InstallError('Invalid artifact size')
    minimum = release.get('python_min')
    if (not isinstance(minimum, list) or len(minimum) != 2
            or any(type(n) is not int for n in minimum) or tuple(sys.version_info[:2]) < tuple(minimum)):
        raise InstallError('Python version is incompatible')
    scope = release.get('scope')
    if (not isinstance(scope, dict) or set(scope) != {'events', 'network_hosts', 'write_roots'}
            or any(not isinstance(items, list) or len(items) > 40
                   or any(not isinstance(item, str) or len(item) > 200 for item in items)
                   for items in scope.values())):
        raise InstallError('Invalid declared scope')
    artifact = stage/'artifacts'/(digest+'.zip')
    artifact.parent.mkdir()
    if remote:
        download(source.rsplit('/', 1)[0]+'/artifacts/'+artifact.name, artifact, MAX_PACKAGE)
    else:
        artifact.write_bytes(read(Path(source)/'artifacts'/artifact.name, MAX_PACKAGE))
    raw = read(artifact, MAX_PACKAGE)
    if len(raw) != release['size'] or hashlib.sha256(raw).hexdigest() != digest:
        raise InstallError('Artifact size/digest mismatch')
    return release, artifact


def extract(artifact, target):
    seen = set()
    with zipfile.ZipFile(artifact) as archive:
        entries = archive.infolist()
        if len(entries) > 256 or sum(i.file_size for i in entries) > MAX_EXPANDED:
            raise InstallError('Oversized archive')
        for info in entries:
            name = info.orig_filename
            parts = PurePosixPath(name).parts
            normalized = name.rstrip('/').casefold()
            reserved = re.compile(r'(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$', re.I)
            if (not parts or name.startswith('/') or '\\' in name or ':' in name
                    or any(p in ('.', '..') or p.endswith((' ', '.')) or reserved.fullmatch(p) for p in parts)
                    or normalized in seen or stat.S_ISLNK(info.external_attr >> 16)
                    or any(ord(c) < 32 for c in name)):
                raise InstallError('Unsafe archive entry')
            seen.add(normalized)
            if info.is_dir():
                continue
            destination = target.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, destination.open('xb') as output:
                shutil.copyfileobj(source, output)


def marketplace(root, host, release):
    manifest_path = '.codex-plugin/plugin.json' if host == 'codex' else '.claude-plugin/plugin.json'
    plugin = load_json(root/'plugin'/manifest_path)
    if not plugin or plugin.get('name') != 'boswell-hooks' or plugin.get('version') != release['version']:
        raise InstallError('Package identity/version mismatch')
    entry = {'name': 'boswell-hooks', 'version': release['version']}
    if host == 'codex':
        entry['source'] = {'source': 'local', 'path': './plugin'}
        entry['policy'] = {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'}
        document = {'name': MARKET, 'plugins': [entry]}
        path = root/'.agents/plugins/marketplace.json'
    else:
        entry['source'] = './plugin'
        document = {'name': MARKET, 'owner': {'name': 'Boswell'}, 'plugins': [entry]}
        path = root/'.claude-plugin/marketplace.json'
    atomic_json(path, document)


def host_manager(host, market, updating):
    name = 'codex' if host == 'codex' else 'claude'
    executable = shutil.which(name)
    if not executable:
        raise InstallError(f'{name} executable not found')
    repairing = updating == 'repair'
    installed = subprocess.run([executable, 'plugin', 'list', '--json'],
                               capture_output=True, text=True, timeout=30)
    if installed.returncode:
        raise InstallError('Cannot inspect installed plugins')
    inventory = json.loads(installed.stdout)
    rows = inventory.get('installed', []) if host == 'codex' else inventory
    present = next((r for r in rows if r.get('pluginId', r.get('id')) == 'boswell-hooks@'+MARKET), None)
    commands = [[executable, 'plugin', 'marketplace', 'add', str(market)],
                [executable, 'plugin', 'add' if host == 'codex' else ('update' if updating else 'install'),
                 'boswell-hooks@'+MARKET]]
    if repairing or not present:
        commands[-1][2] = 'add' if host == 'codex' else 'install'
    if repairing and present:
        remove = [executable, 'plugin', 'remove' if host == 'codex' else 'uninstall', 'boswell-hooks@'+MARKET]
        if host == 'claude-code':
            remove.append('--keep-data')
        commands.insert(0, remove)
    if host == 'codex':
        listed = subprocess.run([executable, 'plugin', 'marketplace', 'list', '--json'],
                                capture_output=True, text=True, timeout=30)
        if listed.returncode:
            raise InstallError('Cannot inspect host marketplaces before installation')
        matches = [m for m in json.loads(listed.stdout).get('marketplaces', []) if m.get('name') == MARKET]
        for existing in matches:
            existing_root = Path(existing['root']).resolve()
            release_root = market.parent.parent.resolve()
            if not existing_root.is_relative_to(release_root):
                raise InstallError('Marketplace name already belongs to another installation')
            if existing_root != market.resolve():
                commands.insert(0, [executable, 'plugin', 'marketplace', 'remove', MARKET])
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if result.returncode:
            # Keep diagnostics local and bounded; commands never include secrets.
            (market/'manager-error.txt').write_text((result.stdout+result.stderr)[-4000:], encoding='utf-8')
            raise InstallError('Host plugin manager failed; see manager-error.txt in staged marketplace')


def profile_root(host):
    variable, default = ('CODEX_HOME', '.codex') if host == 'codex' else ('CLAUDE_CONFIG_DIR', '.claude')
    return Path(os.environ.get(variable) or Path.home()/default).resolve()


def manager_present(host, current):
    if current.get('profile_root') != str(profile_root(host)):
        return False
    executable = shutil.which('codex' if host == 'codex' else 'claude')
    if not executable:
        raise InspectionUnavailable('manager_executable_unavailable')
    try:
        result = subprocess.run([executable, 'plugin', 'list', '--json'],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise InspectionUnavailable('manager_command_failed')
        value = json.loads(result.stdout)
        rows = value['installed'] if host == 'codex' else value
        if (not isinstance(rows, list) or any(not isinstance(r, dict)
                or not isinstance(r.get('pluginId', r.get('id')), str) for r in rows)):
            raise InspectionUnavailable('manager_output_invalid')
        row = next((r for r in rows if r.get('pluginId', r.get('id')) == 'boswell-hooks@'+MARKET), None)
        if row is None:
            return False
        if type(row.get('enabled')) is not bool or not isinstance(row.get('version'), str):
            raise InspectionUnavailable('manager_output_invalid')
        if not row['enabled'] or row['version'] != current['version']:
            return False
        path = (profile_root(host)/'plugins/cache'/MARKET/'boswell-hooks'/current['version']
                if host == 'codex' else Path(row['installPath']))
        try:
            marker = json.loads(read(path/'.boswell-install.json', MAX_CATALOG))
        except FileNotFoundError:
            return False
        except (ValueError, InstallError):
            return False
        return bool(isinstance(marker, dict) and all(marker.get(k) == current.get(k) for k in
                                  ('host', 'version', 'sha256', 'installation_id')))
    except subprocess.TimeoutExpired as exc:
        raise InspectionUnavailable('manager_timeout') from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise InspectionUnavailable('manager_access_failed') from exc
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise InspectionUnavailable('manager_output_invalid') from exc


def status(root, host, *, verify_manager=True):
    current = load_json(Path(root)/'current.json')
    if (Path(root)/'pending.json').exists():
        return {'host': host, 'status': 'recovery_needed', 'installation_state': 'recovery_needed',
                'reason': 'Previous host-manager operation was interrupted; retry install/update or explicit rollback'}
    if not current:
        return {'host': host, 'status': 'missing', 'installation_state': 'missing'}
    if current.get('host') != host:
        raise InstallError('Install root belongs to another host')
    result = {'host': host, 'status': 'installed_awaiting_activation',
              'installed_version': current['version'], 'installed_digest': current['sha256'],
              'installation_id': current['installation_id'], 'changed': False}
    if verify_manager:
        try:
            present = manager_present(host, current)
        except InspectionUnavailable as exc:
            return {**result, 'status': 'inspection_unavailable', 'installation_state': 'inspection_unavailable',
                    'reason': str(exc), 'reason_code': exc.reason_code}
        if not present:
            return {**result, 'status': 'repair_needed', 'installation_state': 'repair_needed',
                    'reason': 'Host profile/plugin registration or installation marker differs'}
    try:
        from installation_health import measured_status
        result.update(measured_status(Path(root), current))
    except ImportError:
        pass
    result['installation_state'] = result['status']
    return result


def install(source, key, root, host, *, manager=host_manager, accept_scope=False, _rollback=False):
    if host not in HOSTS:
        raise InstallError('Unsupported local host')
    root = Path(root).resolve()
    key = Path(key).resolve()
    with install_lock(root):
        pinned = root/'publisher.pub'
        candidate_key = key_bytes(key)
        if pinned.exists() and key_bytes(pinned) != candidate_key:
            raise InstallError('Publisher key change requires independent approval')
        current = load_json(root/'current.json')
        pending = load_json(root/'pending.json')
        if current and current.get('host') != host:
            raise InstallError('Install root belongs to another host')
        if current and current.get('profile_root') != str(profile_root(host)):
            raise InstallError('Install root belongs to another host profile')
        # Unknown observation must never trigger uninstall/reinstall. Check before
        # staging or journaling so a failed inspection leaves installation intact.
        present = manager_present(host, current) if current and manager is host_manager else True
        releases = root/'releases'
        releases.mkdir(exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='verified-', dir=releases))
        release, artifact = acquire(source, stage, pinned if pinned.exists() else key, host)
        if current:
            if not _rollback and version(release['version']) < version(current['version']):
                raise InstallError('Refusing downgrade; use explicit rollback')
            if release['version'] == current['version'] and release['sha256'] != current['sha256']:
                raise InstallError('Immutable version has different bytes')
            expands = any(not set(items).issubset(current['scope'].get(kind, []))
                          for kind, items in release['scope'].items())
            if expands and not accept_scope and not _rollback:
                raise InstallError('Declared scope expanded; obtain user approval before --accept-scope')
            if (release['sha256'] == current['sha256'] and not pending
                    and present):
                # Stage is a fresh child created above; never delete a supplied path.
                if stage.resolve().parent != releases.resolve():
                    raise InstallError('Invalid temporary release path')
                shutil.rmtree(stage)
                return status(root, host, verify_manager=manager is host_manager)
        market = stage/'marketplace'
        plugin = market/'plugin'
        extract(artifact, plugin)
        marketplace(market, host, release)
        pending_target = (pending or {}).get('target') or {}
        valid_pending = (pending_target.get('host') == host
                         and pending_target.get('profile_root') == str(profile_root(host))
                         and pending_target.get('state_root') == str(root)
                         and isinstance(pending_target.get('installation_id'), str))
        installation_id = (current['installation_id'] if current else
                           pending_target['installation_id'] if valid_pending else str(uuid.uuid4()))
        record = {**release, 'host': host, 'installation_id':
                  installation_id,
                  'installed_at': time.time(), 'release_dir': str(stage), 'state_root': str(root),
                  'profile_root': str(profile_root(host))}
        atomic_json(plugin/'.boswell-install.json', record)
        # This journal is intentionally left on failure. Never pretend a partial
        # host-manager operation was atomic or successfully rolled back.
        atomic_json(root/'pending.json', {'target': record, 'previous': current})
        if not pinned.exists():
            pinned.write_bytes(candidate_key)
        repairing = bool(current and manager is host_manager and not present)
        manager(host, market, 'repair' if repairing else bool(current or pending))
        if manager is host_manager and not manager_present(host, record):
            raise InstallError('Host manager returned success but installed package did not verify; recovery is still pending')
        if current:
            atomic_json(root/'previous.json', current)
        atomic_json(root/'current.json', record)
        (root/'pending.json').unlink()
        return {**status(root, host, verify_manager=manager is host_manager), 'changed': True}


def rollback(root, host, *, manager=host_manager):
    root = Path(root).resolve()
    previous = load_json(root/'previous.json')
    pending = load_json(root/'pending.json')
    if pending and pending.get('previous'):
        previous = pending['previous']
    if not previous:
        raise InstallError('No previous verified release is available')
    source = Path(previous['release_dir']).resolve()
    if not source.is_relative_to(root/'releases'):
        raise InstallError('Rollback release path is outside installation root')
    return install(source, root/'publisher.pub', root, host, manager=manager, _rollback=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'update', 'status', 'rollback'])
    parser.add_argument('--host', required=True, choices=HOSTS)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--publisher-key', type=Path)
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument('--catalog', default=None)
    sources.add_argument('--release-dir', type=Path, help='Offline verification/pilot source')
    parser.add_argument('--accept-scope', action='store_true', help='Use only after actual user approval of scope expansion')
    args = parser.parse_args()
    root = args.root or Path.home()/'.boswell/init'/args.host
    try:
        if args.action == 'status':
            result = status(root, args.host)
        elif args.action == 'rollback':
            result = rollback(root, args.host)
        else:
            key = args.publisher_key or root/'publisher.pub'
            if not key.exists():
                raise InstallError('First install requires the approved publisher key via --publisher-key')
            result = install(args.release_dir or args.catalog or 'https://v3.askboswell.com/init/catalog.json',
                             key, root, args.host, accept_scope=args.accept_scope)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (InstallError, OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
