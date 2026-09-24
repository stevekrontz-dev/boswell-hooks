"""Tenant changes must never reuse context or drain another credential's queue."""
import importlib
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]/'scripts'))
import codex_config
import session_state
import transcript_spool
import transcript_monitor


@pytest.fixture
def identity(tmp_path, monkeypatch):
    import boswell_client
    monkeypatch.setattr(boswell_client, '_BOUND_HEADERS', None)
    monkeypatch.setattr(boswell_client, '_BOUND_IDENTITY', None)
    monkeypatch.setenv('BOSWELL_TENANT', '')
    monkeypatch.setenv('BOSWELL_API_KEY', 'test-tenant-A')
    monkeypatch.setattr(codex_config, 'DEFAULT_TENANT_FILE', tmp_path/'default')
    monkeypatch.setattr(codex_config, 'HOOK_KEY_FILE', tmp_path/'key')
    monkeypatch.setattr(codex_config, 'TENANT_PROFILE_ROOT', tmp_path/'profiles')
    monkeypatch.setattr(session_state, 'STATE_ROOT', tmp_path/'sessions')
    monkeypatch.setattr(transcript_spool, 'QUEUE_PATH', tmp_path/'raw-queue.json')
    monkeypatch.setattr(transcript_spool, 'ARCHIVE_ROOT', tmp_path/'archive')
    monkeypatch.setattr(transcript_monitor, 'STATE_FILE', tmp_path/'legacy-state.json')
    monkeypatch.setattr(transcript_monitor, 'ARCHIVE_ROOT', tmp_path/'legacy-archive')
    return tmp_path


@pytest.mark.parametrize('source', ['explicit', 'default'])
def test_invalid_selected_profile_never_uses_machine_key(identity, monkeypatch, source):
    if source == 'explicit':
        monkeypatch.setenv('BOSWELL_TENANT', 'other/invalid')
    else:
        (identity/'default').write_text('other/invalid')
    assert codex_config.auth_headers() == {}


def test_unreadable_default_profile_never_uses_machine_key(identity, monkeypatch):
    original = Path.read_text
    def read(path, *args, **kwargs):
        if path == identity/'default':
            raise PermissionError('test denial')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    assert codex_config.auth_headers() == {}


@pytest.mark.parametrize('failure', ['origin', 'filesystem'])
def test_binding_verification_errors_are_identity_failures(identity, monkeypatch, failure):
    import tenant_binding
    if failure == 'origin':
        monkeypatch.setattr(codex_config, 'API_BASE', 'https://example.invalid:badport')
    else:
        monkeypatch.setattr(Path, 'mkdir', Mock(side_effect=PermissionError('test denial')))
    with pytest.raises(tenant_binding.TenantBindingError):
        tenant_binding.bind_session({'session_id':'A'},create=True)


def test_changed_credential_or_origin_cannot_resume_bound_session(identity, monkeypatch):
    binding = importlib.import_module('tenant_binding')
    data = {'session_id': 'session-A'}
    original = binding.bind_session(data, create=True)
    assert binding.bind_session(data) == original
    monkeypatch.setenv('BOSWELL_API_KEY', 'test-tenant-B')
    with pytest.raises(binding.TenantBindingError):
        binding.bind_session(data, create=True)
    monkeypatch.setenv('BOSWELL_API_KEY', 'test-tenant-A')
    monkeypatch.setattr(codex_config, 'API_BASE', 'https://other.example')
    with pytest.raises(binding.TenantBindingError):
        binding.bind_session(data)


def test_unbound_legacy_cache_cannot_be_adopted_by_current_key(identity):
    binding = importlib.import_module('tenant_binding')
    session_state.save_startup_cache('legacy', {'sacred_manifest': {'identity': 'tenant-A'}})
    with pytest.raises(binding.TenantBindingError):
        binding.bind_session({'session_id': 'legacy'}, create=True)


def test_unbound_resume_cannot_adopt_imported_history(identity):
    binding = importlib.import_module('tenant_binding')
    with pytest.raises(binding.TenantBindingError):
        binding.bind_session({'session_id': 'imported', 'source': 'resume'}, create=True)


def test_queue_retains_unbound_or_other_tenant_entries(identity, monkeypatch):
    binding = importlib.import_module('tenant_binding')
    a = binding.bind_session({'session_id': 'A'}, create=True)
    transcript_spool._write_queue({'A': {'binding': a, 'index_card': {'session_id': 'A'}},
                                  'legacy': {'index_card': {'session_id': 'legacy'}}})
    monkeypatch.setenv('BOSWELL_API_KEY', 'test-tenant-B')
    commit = Mock()
    monkeypatch.setattr(transcript_spool.boswell_client, 'commit', commit)
    assert transcript_spool.flush_pending() == (0, 2)
    commit.assert_not_called()
    monkeypatch.setenv('BOSWELL_API_KEY', 'test-tenant-A')
    assert transcript_spool.flush_pending() == (1, 1)
    assert list(transcript_spool._read_queue()) == ['legacy']


def test_legacy_monitor_never_uploads_unbound_queue(identity, monkeypatch):
    import boswell_client
    commit = Mock()
    monkeypatch.setattr(boswell_client, 'commit', commit)
    transcript_monitor._write_queue([{'session_id': 'old', 'index_card': {'session_id': 'old'}}])
    assert transcript_monitor.flush_pending_transcripts() == (0, 1)
    commit.assert_not_called()


def test_capture_requires_exact_event_transcript_and_session_binding(identity, monkeypatch):
    binding = importlib.import_module('tenant_binding')
    source = identity/'mine.jsonl'
    source.write_text(json.dumps({'type': 'user', 'sessionId': 'A', 'message': 'schema variation'})+'\n')
    binding.bind_session({'session_id': 'A'}, create=True)
    data = {'session_id': 'A', 'transcript_path': str(source), 'client': 'claude'}
    card = transcript_spool.capture(data, 'session_end')
    assert card['session_id'] == 'A'
    assert transcript_spool._read_queue()['A']['binding'] == binding.current_binding()
    monkeypatch.setenv('BOSWELL_API_KEY', 'test-tenant-B')
    with pytest.raises(binding.TenantBindingError):
        transcript_spool.capture(data, 'session_end')
    monkeypatch.setenv('BOSWELL_API_KEY', 'test-tenant-A')
    source.write_text(json.dumps({'type': 'user', 'sessionId': 'other-session'})+'\n')
    with pytest.raises(ValueError):
        transcript_spool.capture(data, 'session_end')


def test_claude_capture_without_event_path_does_not_scan_default_profile(identity, monkeypatch):
    spy = Mock()
    monkeypatch.setattr(transcript_monitor, 'find_current_session', spy)
    transcript_monitor.capture()
    spy.assert_not_called()


def test_capture_rejects_foreign_identity_after_matching_header(identity):
    import tenant_binding
    source = identity/'mixed.jsonl'
    source.write_text('\n'.join(json.dumps({'type':'user','sessionId':sid}) for sid in ('A','B'))+'\n')
    tenant_binding.bind_session({'session_id':'A'},create=True)
    with pytest.raises(tenant_binding.TenantBindingError):
        transcript_spool.capture({'session_id':'A','transcript_path':str(source),'client':'claude'},'session_end')
    assert transcript_spool._read_queue() == {}
    assert not list((identity/'archive').rglob('*.*'))


def test_queue_formats_have_distinct_paths():
    assert transcript_monitor._queue_path().name != transcript_spool.QUEUE_PATH.name


def test_request_refuses_changed_key_before_network(identity, monkeypatch):
    import tenant_binding
    import boswell_client
    tenant_binding.bind_session({'session_id': 'A'}, create=True)
    monkeypatch.setenv('BOSWELL_API_KEY', 'test-tenant-B')
    network = Mock()
    monkeypatch.setattr(boswell_client.urllib.request, 'urlopen', network)
    with pytest.raises(boswell_client.BoswellAuthRejected):
        boswell_client.commit(branch='transcripts', content={}, content_type='transcript', message='test', tags=[])
    network.assert_not_called()


@pytest.mark.parametrize('client', ['codex_dispatcher', 'dispatcher'])
def test_dispatcher_blocks_tenant_switch_before_handler(identity, monkeypatch, capsys, client):
    import tenant_binding
    module = importlib.import_module(client)
    tenant_binding.bind_session({'session_id': 'A'}, create=True)
    monkeypatch.setenv('BOSWELL_API_KEY', 'test-tenant-B')
    monkeypatch.setenv('BOSWELL_HOOKS_FAIL_OPEN', '1')
    monkeypatch.setattr(sys, 'argv', ['dispatcher.py', 'PreToolUse'])
    monkeypatch.setattr(module, '_input' if client == 'codex_dispatcher' else '_read_input', lambda: {'session_id': 'A'})
    handler = Mock()
    monkeypatch.setitem(module.ROUTES if client == 'codex_dispatcher' else module._ROUTES, 'PreToolUse', handler)
    if client == 'codex_dispatcher':
        assert module.main() == 2
    else:
        with pytest.raises(SystemExit) as exit_info:
            module.main()
        assert exit_info.value.code == 2
    handler.assert_not_called()
    assert json.loads(capsys.readouterr().out)['hookSpecificOutput']['permissionDecision'] == 'deny'
