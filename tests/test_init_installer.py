import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))


@pytest.fixture
def installer():
    assert importlib.util.find_spec('init_installer'), 'Signed installer is not implemented'
    import init_installer
    return init_installer


@pytest.fixture
def signed(tmp_path):
    key = tmp_path/'signing'
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
    root = tmp_path/'published'
    root.mkdir()
    (root/'artifacts').mkdir()

    def publish(version='2.4.0', extra=None, scope=None):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as z:
            z.writestr('.claude-plugin/plugin.json', json.dumps({'name': 'boswell-hooks', 'version': version}))
            z.writestr('hooks/hooks.json', '{"hooks": {}}')
            z.writestr('scripts/dispatcher.py', 'pass\n')
            if extra:
                info = zipfile.ZipInfo('placeholder')
                info.filename = extra[0]  # Preserve hostile backslashes on Windows.
                z.writestr(info, extra[1])
        blob = output.getvalue()
        digest = hashlib.sha256(blob).hexdigest()
        (root/'artifacts'/f'{digest}.zip').write_bytes(blob)
        catalog = {'schema': 1, 'publisher': 'boswell-hooks', 'channel': 'stable',
                   'releases': {'claude-code': {'version': version, 'sha256': digest, 'size': len(blob),
                   'python_min': [3, 9], 'scope': scope or {'events': ['SessionStart'],
                   'network_hosts': ['v3.askboswell.com'], 'write_roots': ['boswell-state']}}}}
        path = root/'catalog.json'
        path.write_text(json.dumps(catalog), encoding='utf-8')
        path.with_suffix('.json.sig').unlink(missing_ok=True)
        subprocess.run(['ssh-keygen', '-Y', 'sign', '-f', str(key), '-n', 'boswell-init', str(path)],
                       check=True, capture_output=True)
        return catalog

    return root, Path(str(key)+'.pub'), publish


def test_real_signature_and_tampering(installer, signed):
    root, key, publish = signed
    publish()
    assert installer.verify_catalog(root/'catalog.json', root/'catalog.json.sig', key)['publisher'] == 'boswell-hooks'
    (root/'catalog.json').write_bytes((root/'catalog.json').read_bytes()+b' ')
    with pytest.raises(installer.InstallError, match='signature'):
        installer.verify_catalog(root/'catalog.json', root/'catalog.json.sig', key)


def test_install_noop_update_rollback_preserve_external_state(installer, signed, tmp_path):
    source, key, publish = signed
    target = tmp_path/'install'
    sentinel = tmp_path/'hook_key'
    sentinel.write_bytes(b'enrollment untouched')
    calls = []
    manager = lambda host, market, updating: calls.append((host, market, updating))
    publish()
    result = installer.install(source, key, target, 'claude-code', manager=manager)
    assert result['status'] == 'installed_awaiting_activation'
    assert result['installed_version'] == '2.4.0'
    assert len(calls) == 1
    assert installer.install(source, key, target, 'claude-code', manager=manager)['changed'] is False
    assert len(calls) == 1
    publish('2.4.1')
    assert installer.install(source, key, target, 'claude-code', manager=manager)['installed_version'] == '2.4.1'
    assert installer.rollback(target, 'claude-code', manager=manager)['installed_version'] == '2.4.0'
    assert sentinel.read_bytes() == b'enrollment untouched'


@pytest.mark.parametrize('entry', ['../escape', '/absolute', 'C:/escape', 'scripts/../../escape', 'scripts\\escape', 'CON', 'a:b', 'scripts/AUX.txt'])
def test_unsafe_archive_rejected_before_manager(installer, signed, tmp_path, entry):
    source, key, publish = signed
    publish(extra=(entry, 'bad'))
    def manager(*args):
        pytest.fail('Manager must not run')
    with pytest.raises(installer.InstallError, match='archive'):
        installer.install(source, key, tmp_path/'install', 'claude-code', manager=manager)


def test_tampered_package_and_failed_manager_do_not_commit(installer, signed, tmp_path):
    source, key, publish = signed
    cat = publish()
    target = tmp_path/'install'
    def fail(*args):
        raise RuntimeError('host manager failed')
    with pytest.raises(RuntimeError):
        installer.install(source, key, target, 'claude-code', manager=fail)
    assert not (target/'current.json').exists()
    assert installer.status(target, 'claude-code')['status'] == 'recovery_needed'
    artifact = source/'artifacts'/(cat['releases']['claude-code']['sha256']+'.zip')
    artifact.write_bytes(b'tampered')
    with pytest.raises(installer.InstallError, match='digest|size'):
        installer.install(source, key, target, 'claude-code', manager=fail)


def test_scope_expansion_downgrade_and_key_change_fail_closed(installer, signed, tmp_path):
    source, key, publish = signed
    target = tmp_path/'install'
    manager = lambda *args: None
    publish()
    installer.install(source, key, target, 'claude-code', manager=manager)
    publish('2.3.0')
    with pytest.raises(installer.InstallError, match='downgrade'):
        installer.install(source, key, target, 'claude-code', manager=manager)
    publish('2.5.0', scope={'events': ['SessionStart', 'PreToolUse'], 'network_hosts': [], 'write_roots': []})
    with pytest.raises(installer.InstallError, match='scope'):
        installer.install(source, key, target, 'claude-code', manager=manager)
    other = tmp_path/'other.pub'
    other.write_text('ssh-ed25519 AAAA changed')
    with pytest.raises(installer.InstallError, match='key'):
        installer.install(source, other, target, 'claude-code', manager=manager)


def test_changed_staged_bytes_never_reused(installer, signed, tmp_path):
    source, key, publish = signed
    target = tmp_path/'install'
    publish()
    def fail(*args):
        raise RuntimeError('interrupted')
    with pytest.raises(RuntimeError):
        installer.install(source, key, target, 'claude-code', manager=fail)
    for path in target.glob('releases/**/scripts/dispatcher.py'):
        path.write_text('malicious mutation')
    observed = []
    def inspect(host, market, updating):
        observed.append((market/'plugin/scripts/dispatcher.py').read_text())
    installer.install(source, key, target, 'claude-code', manager=inspect)
    assert observed == ['pass\n']


def test_same_version_can_repair_missing_host_install(installer, signed, tmp_path, monkeypatch):
    source, key, publish = signed
    root = tmp_path/'install'
    publish()
    calls = []
    fake = lambda *args: calls.append(args)
    monkeypatch.setattr(installer, 'manager_present', lambda *args: len(calls) >= 2)
    installer.install(source, key, root, 'claude-code', manager=fake)
    # Explicitly model the actual manager, whose presence is checked on reconnect.
    monkeypatch.setattr(installer, 'host_manager', fake)
    installer.install(source, key, root, 'claude-code', manager=fake)
    assert len(calls) == 2


def test_profile_switch_does_not_reuse_another_profiles_receipt(installer, signed, tmp_path, monkeypatch):
    source, key, publish = signed
    root = tmp_path/'install'
    publish()
    installer.install(source, key, root, 'claude-code', manager=lambda *args: None)
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path/'different-profile'))
    assert installer.status(root, 'claude-code')['status'] == 'repair_needed'
    with pytest.raises(installer.InstallError, match='profile'):
        installer.install(source, key, root, 'claude-code', manager=lambda *args: None)


def test_noop_does_not_accumulate_release_directories(installer, signed, tmp_path):
    source, key, publish = signed
    root = tmp_path/'install'
    publish()
    manager = lambda *args: None
    installer.install(source, key, root, 'claude-code', manager=manager)
    before = sorted((root/'releases').iterdir())
    installer.install(source, key, root, 'claude-code', manager=manager)
    assert sorted((root/'releases').iterdir()) == before


def test_interrupted_first_install_reuses_pending_identity(installer, signed, tmp_path):
    source, key, publish = signed
    root = tmp_path/'install'
    publish()
    observed = []
    def interrupted(host, market, updating):
        observed.append(json.loads((market/'plugin/.boswell-install.json').read_text())['installation_id'])
        raise RuntimeError('Simulated host success then interruption')
    with pytest.raises(RuntimeError):
        installer.install(source, key, root, 'claude-code', manager=interrupted)
    def retry(host, market, updating):
        observed.append(json.loads((market/'plugin/.boswell-install.json').read_text())['installation_id'])
    installer.install(source, key, root, 'claude-code', manager=retry)
    assert len(observed) == 2 and observed[0] == observed[1]

@pytest.mark.parametrize('failure', ['executable', 'exit', 'timeout', 'permission', 'json', 'shape'])
def test_unobservable_manager_is_not_missing_or_ready(installer, tmp_path, monkeypatch, failure):
    root = tmp_path/'install'
    current = {'host': 'codex', 'profile_root': str(installer.profile_root('codex')),
               'version': '2.4.0', 'sha256': 'a'*64, 'installation_id': 'test-install'}
    installer.atomic_json(root/'current.json', current)
    monkeypatch.setattr(installer.shutil, 'which', lambda name: None if failure == 'executable' else 'codex')
    def inspect(*args, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired('codex', 30)
        if failure == 'permission':
            raise PermissionError('sensitive environment detail must not escape')
        return subprocess.CompletedProcess([], 1 if failure == 'exit' else 0,
                                           '{}' if failure == 'shape' else 'not json', 'sensitive stderr')
    monkeypatch.setattr(installer.subprocess, 'run', inspect)
    result = installer.status(root, 'codex')
    assert result['status'] == result['installation_state'] == 'inspection_unavailable'
    assert 'startup_ok' not in result
    assert 'sensitive' not in json.dumps(result)
    assert result['reason_code']


def test_verified_absent_plugin_still_needs_repair(installer, tmp_path, monkeypatch):
    root = tmp_path/'install'
    installer.atomic_json(root/'current.json', {'host': 'codex', 'profile_root': str(installer.profile_root('codex')),
                           'version': '2.4.0', 'sha256': 'a'*64, 'installation_id': 'test-install'})
    monkeypatch.setattr(installer.shutil, 'which', lambda name: 'codex')
    monkeypatch.setattr(installer.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess([], 0, '{"installed": []}', ''))
    assert installer.status(root, 'codex')['status'] == 'repair_needed'


def test_unobservable_manager_cannot_trigger_repair(installer, signed, tmp_path, monkeypatch):
    source, key, publish = signed
    root = tmp_path/'install'
    publish()
    installer.install(source, key, root, 'claude-code', manager=lambda *args: None)
    before = (root/'current.json').read_bytes()
    stages = sorted((root/'releases').iterdir())
    real_run = installer.subprocess.run
    def run(command, **kwargs):
        if command[1:3] == ['plugin', 'list']:
            return subprocess.CompletedProcess(command, 1, '', 'unavailable')
        return real_run(command, **kwargs)
    monkeypatch.setattr(installer.subprocess, 'run', run)
    calls = []
    manager = lambda *args: calls.append(args)
    monkeypatch.setattr(installer, 'host_manager', manager)
    with pytest.raises(installer.InstallError, match='inspect'):
        installer.install(source, key, root, 'claude-code', manager=manager)
    assert calls == []
    assert (root/'current.json').read_bytes() == before
    assert not (root/'pending.json').exists()
    assert sorted((root/'releases').iterdir()) == stages

@pytest.mark.parametrize('marker_mode', ['missing', 'mismatch', 'invalid', 'unreadable'])
def test_marker_failure_distinguishes_damage_from_unobservable(installer, tmp_path, monkeypatch, marker_mode):
    profile = tmp_path/'profile'
    monkeypatch.setenv('CODEX_HOME', str(profile))
    root = tmp_path/'install'
    current = {'host': 'codex', 'profile_root': str(profile.resolve()), 'version': '2.4.0',
               'sha256': 'a'*64, 'installation_id': 'test-install'}
    installer.atomic_json(root/'current.json', current)
    path = profile/'plugins/cache/boswell-init/boswell-hooks/2.4.0/.boswell-install.json'
    if marker_mode != 'missing':
        installer.atomic_json(path, [1] if marker_mode == 'invalid' else {**current, 'sha256': 'b'*64})
    monkeypatch.setattr(installer.shutil, 'which', lambda name: 'codex')
    row = {'pluginId': 'boswell-hooks@boswell-init', 'enabled': True, 'version': '2.4.0'}
    monkeypatch.setattr(installer.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess([], 0, json.dumps({'installed': [row]}), ''))
    if marker_mode == 'unreadable':
        real_read = installer.read
        def read(path, limit):
            if path.name == '.boswell-install.json':
                raise PermissionError('private diagnostic')
            return real_read(path, limit)
        monkeypatch.setattr(installer, 'read', read)
    expected = 'inspection_unavailable' if marker_mode == 'unreadable' else 'repair_needed'
    assert installer.status(root, 'codex')['status'] == expected


@pytest.mark.parametrize('rows', [[{}], [{'pluginId': 'boswell-hooks@boswell-init', 'version': '2.4.0'}]])
def test_incomplete_inventory_cannot_establish_absence(installer, tmp_path, monkeypatch, rows):
    root = tmp_path/'install'
    installer.atomic_json(root/'current.json', {'host': 'codex', 'profile_root': str(installer.profile_root('codex')),
                           'version': '2.4.0', 'sha256': 'a'*64, 'installation_id': 'test-install'})
    monkeypatch.setattr(installer.shutil, 'which', lambda name: 'codex')
    monkeypatch.setattr(installer.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess([], 0, json.dumps({'installed': rows}), ''))
    assert installer.status(root, 'codex')['status'] == 'inspection_unavailable'
