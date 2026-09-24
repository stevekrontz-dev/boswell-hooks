import importlib.util
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).parents[1]/'scripts'))


def test_health_requires_current_install_and_same_fresh_session(tmp_path):
    assert importlib.util.find_spec('installation_health'), 'Activation health is not implemented'
    import installation_health as health
    plugin = tmp_path/'plugin'
    plugin.mkdir()
    record = {'host': 'codex', 'version': '2.4.0', 'sha256': 'a'*64,
              'installation_id': 'install-1', 'installed_at': time.time()-1,
              'state_root': str(tmp_path)}
    (plugin/'.boswell-install.json').write_text(json.dumps(record))
    assert health.measured_status(tmp_path, record) == {}
    health.note('retrieval', {'session_id': 'old'}, plugin_root=plugin)
    health.note('startup', {'session_id': 'new'}, plugin_root=plugin)
    assert health.measured_status(tmp_path, record) == {}
    health.note('retrieval', {'session_id': 'new'}, plugin_root=plugin)
    measured = health.measured_status(tmp_path, record)
    assert measured['status'] == 'current'
    assert measured['session_id'] == 'new'
    assert measured['loaded_digest'] == 'a'*64
    assert health.measured_status(tmp_path, {**record, 'sha256': 'b'*64}) == {}
    assert health.measured_status(tmp_path, {**record, 'installed_at': time.time()+10}) == {}
    health.note('checkpoint', {'session_id': 'new'}, plugin_root=plugin)
    health.note('restore', {'session_id': 'new'}, plugin_root=plugin)
    assert health.measured_status(tmp_path, record)['compaction_ok'] is True


def test_unmanaged_checkout_does_not_invent_installation(tmp_path):
    assert importlib.util.find_spec('installation_health'), 'Activation health is not implemented'
    import installation_health as health
    health.note('startup', {'session_id': 'test'}, plugin_root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_new_release_builder_packages_both_hosts_without_private_state(tmp_path):
    assert importlib.util.find_spec('build_init_release'), 'Release builder is not implemented'
    import build_init_release as builder
    import zipfile
    root = Path(__file__).parents[1]
    for host in ('codex', 'claude-code'):
        release = builder.build_package(root, tmp_path, host)
        archive = tmp_path/'artifacts'/(release['sha256']+'.zip')
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            assert 'scripts/installation_health.py' in names
            assert 'scripts/codex_dispatcher.py' in names
            assert 'hooks/hooks.json' in names
            assert not any('hook_key' in n or '__pycache__' in n or n.endswith('.pyc') for n in names)
            path = '.codex-plugin/plugin.json' if host == 'codex' else '.claude-plugin/plugin.json'
            assert json.loads(z.read(path))['version'] == release['version']


def test_startup_measurement_requires_success_and_is_not_replayed(tmp_path, monkeypatch):
    import codex_dispatcher as dispatcher
    import session_state
    import installation_health
    monkeypatch.setattr(session_state, 'STATE_ROOT', tmp_path/'sessions')
    monkeypatch.setattr(dispatcher.transcript_spool, 'flush_pending', lambda **kw: None)
    payload = {'local_time': 'now', 'sacred_manifest': {'identity': 'Pilot', 'active_commitments': []},
               'recent_thread': [], 'open_tasks': [], 'my_tasks': [], 'behavioral_context': []}
    monkeypatch.setattr(dispatcher.boswell_client, 'startup', lambda: payload)
    events = []
    monkeypatch.setattr(installation_health, 'note', lambda event, data: events.append(event))
    data = {'session_id': 'fresh', 'source': 'startup'}
    assert dispatcher._session_start(data)['hookSpecificOutput']['additionalContext']
    assert events == ['startup']
    assert dispatcher._session_start(data) is None
    assert events == ['startup']


def test_retrieval_measurement_requires_successful_search(tmp_path, monkeypatch):
    import codex_dispatcher as dispatcher
    import session_state
    import installation_health
    monkeypatch.setattr(session_state, 'STATE_ROOT', tmp_path/'sessions')
    monkeypatch.setattr(dispatcher, 'prompt_startup_gate', lambda data: None)
    monkeypatch.setattr(dispatcher.opening_briefing, 'on_prompt', lambda data: None)
    monkeypatch.setattr(dispatcher.boswell_client, 'search', lambda *a, **kw: {'results': []})
    events = []
    monkeypatch.setattr(installation_health, 'note', lambda event, data: events.append(event))
    dispatcher._user_prompt({'session_id': 'fresh', 'prompt': 'Review the plugin installation readiness measurements for this tenant'})
    assert events == ['retrieval']
