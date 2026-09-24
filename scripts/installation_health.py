"""Bounded lifecycle measurements; explicitly not tamper-proof attestation."""
import hashlib
import json
from pathlib import Path
import time


def note(event, data, *, plugin_root=None):
    # Measurement must not alter the lifecycle result or leak prompt content.
    try:
        if event not in {'startup', 'retrieval', 'checkpoint', 'restore'}:
            return
        root = Path(plugin_root) if plugin_root else Path(__file__).resolve().parents[1]
        marker = root/'.boswell-install.json'
        if not marker.is_file() or marker.stat().st_size > 65536:
            return
        record = json.loads(marker.read_text(encoding='utf-8'))
        sid = data.get('session_id')
        if not isinstance(sid, str) or not 0 < len(sid) <= 200:
            return
        directory = Path(record['state_root'])/'health'
        directory.mkdir(parents=True, exist_ok=True)
        session = hashlib.sha256(sid.encode()).hexdigest()
        path = directory/(session+'-'+event+'.json')
        from init_installer import atomic_json
        atomic_json(path, {'event': event, 'ok': True, 'session_id': sid,
                    'installation_id': record['installation_id'], 'sha256': record['sha256'],
                    'version': record['version'], 'host': record['host'], 'at': time.time()})
        # Retain enough recent measurements to troubleshoot, without transcripts.
        files = sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in files[128:]:
            stale.unlink(missing_ok=True)
    except Exception:
        pass


def measured_status(root, current):
    sessions = {}
    for path in (Path(root)/'health').glob('*.json'):
        try:
            if path.stat().st_size > 4096:
                continue
            value = json.loads(path.read_text(encoding='utf-8'))
            if (value.get('ok') is not True or value.get('at', 0) < current['installed_at']
                    or any(value.get(k) != current.get(k) for k in ('host', 'version', 'sha256', 'installation_id'))):
                continue
            sessions.setdefault(value['session_id'], {})[value['event']] = value
        except (OSError, ValueError, TypeError, KeyError):
            continue
    ready = [events for events in sessions.values() if 'startup' in events and 'retrieval' in events
             and events['retrieval']['at'] >= events['startup']['at']]
    if not ready:
        return {}
    latest = max(ready, key=lambda e: e['retrieval']['at'])
    compact = ('checkpoint' in latest and 'restore' in latest
               and latest['restore']['at'] >= latest['checkpoint']['at'])
    return {'status': 'current', 'session_id': latest['startup']['session_id'],
            'loaded_digest': current['sha256'], 'startup_ok': True, 'retrieval_ok': True,
            'compaction_ok': compact, 'evidence_kind': 'local_health_not_attestation'}
