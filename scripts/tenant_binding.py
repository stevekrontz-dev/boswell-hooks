"""Fail-closed local session/queue identity; fingerprints never contain credentials."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import codex_config
import session_state


class TenantBindingError(ValueError):
    pass


def current_binding(headers=None):
    headers = codex_config.auth_headers() if headers is None else headers
    key = headers.get('X-API-Key')
    if not key:
        return None
    url = urlsplit(codex_config.API_BASE)
    if url.scheme != 'https' or not url.hostname or url.username or url.password:
        return None
    origin = f'https://{url.hostname.lower()}:{url.port or 443}'
    return hashlib.sha256(('boswell-tenant-binding-v1\0'+origin+'\0'+key).encode()).hexdigest()


def bind_session(data, *, create=False):
    try:
        return _bind_session(data, create=create)
    except TenantBindingError:
        raise
    except Exception as exc:
        # A permissions/config/parser error must never enter a diagnostic
        # fail-open path in either host dispatcher.
        raise TenantBindingError('Tenant identity could not be verified') from exc


def _bind_session(data, *, create=False):
    sid = data.get('session_id')
    headers = codex_config.auth_headers()
    binding = current_binding(headers)
    if not isinstance(sid, str) or not sid or not binding:
        raise TenantBindingError('A session and valid tenant credential are required')
    path = session_state.STATE_ROOT/(session_state.safe_session_id(sid)+'.tenant.json')
    if not path.exists():
        # An old receipt/checkpoint has no provable tenant. Never adopt it under
        # whichever credential happens to be selected during an upgrade/resume.
        checkpoint = session_state.STATE_ROOT/'progress'/(hashlib.sha256(sid.encode()).hexdigest()+'.json')
        if (not create or data.get('source') in {'resume', 'compact'}
                or session_state.cache_path(sid).exists()
                or session_state.path_for(sid).exists() or checkpoint.exists()):
            raise TenantBindingError('Unbound historical session; start a fresh session')
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open('x', encoding='utf-8') as out:
                json.dump({'session_id': sid, 'binding': binding}, out)
        except FileExistsError:
            pass
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        raise TenantBindingError('Session tenant binding is unreadable') from exc
    if record != {'session_id': sid, 'binding': binding}:
        raise TenantBindingError('Tenant credential changed; start a fresh session')
    import boswell_client
    boswell_client.bind_auth(headers, binding)
    return binding


def validate_transcript(path: Path, session_id: str):
    # Check the entire archived snapshot, not just the first matching header.
    # Message bodies are data; only host envelope identity is authoritative.
    with path.open('rb') as stream:
        scanned = 0
        verified = False
        while True:
            raw = stream.readline(8 * 1024 * 1024 + 1)
            scanned += len(raw)
            if not raw:
                break
            if len(raw) > 8 * 1024 * 1024 or scanned > 128 * 1024 * 1024:
                raise TenantBindingError('Transcript exceeds bounded identity scan')
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            observed = event.get('sessionId')
            if event.get('type') == 'session_meta':
                payload = event.get('payload')
                if isinstance(payload, dict):
                    observed = payload.get('session_id') or payload.get('id')
            if observed:
                if observed != session_id:
                    raise TenantBindingError('Transcript belongs to another session')
                verified = True
    if not verified:
        raise TenantBindingError('Transcript session identity could not be verified')
