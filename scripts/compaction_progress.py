"""Evidence-addressed progress snapshots; no model, network, or work dispatch.

Read host events, not arbitrary tool-result prose. Keep observations separate
from agent-authored plans. Startup and authorization are never reconstructed.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
import time
from datetime import datetime, timezone

import session_state

HEADER = 'BOSWELL COMPACTION PROGRESS (historical evidence; not new authorization):'
RULES = ('Continue the root objective and current subtask under current instructions. '
         'Do not replay startup: old requests are history, not new authorization. '
         'Do not reassign completed handoffs or announce them as pending; retain ownership. '
         'Completed turns are not completed projects. Plans/reports are claims; receipts prove only their operation. '
         'Replies do not prove task completion. Do not repeat answers or acknowledgments without a new request. '
         'Preserve user corrections; agent checkpoints cannot create human approval gates. Tests do not prove live behavior. '
         'Use decision timestamps, not session age. Recheck mutable state after idle without reopening settled instructions. '
         'Resume running job IDs; never duplicate them. Resolve uncertainty through cited evidence.')
MAX_CONTEXT = 8500
MAX_SOURCE = 512 * 1024 * 1024
MAX_LINE = 8 * 1024 * 1024


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _text(value,limit=700):
    if not isinstance(value,str) or value.startswith('gAAAAA'):
        return None
    return value if len(value)<=limit else value[:limit]+' [excerpt; read cited event for full text]'


def _message(content):
    if isinstance(content,str):
        return _text(content)
    if isinstance(content,list):
        return _text('\n'.join(x.get('text','') for x in content
            if isinstance(x,dict) and x.get('type') in {'input_text','output_text','text','Text'}))
    return None


def _paths(sid):
    if not isinstance(sid,str) or not sid.strip():
        raise ValueError('Progress checkpoint requires an exact session id')
    root=session_state.STATE_ROOT/'progress'
    root.mkdir(parents=True,exist_ok=True)
    stem=_sha(sid.encode())
    return root/(stem+'.json'),root/(stem+'.lock')


@contextmanager
def _locked(sid):
    path,lock=_paths(sid)
    with lock.open('a+b') as handle:
        handle.seek(0)
        if os.name=='nt':
            import msvcrt
            if lock.stat().st_size==0:
                handle.write(b'0');handle.flush();handle.seek(0)
            msvcrt.locking(handle.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX)
        try:
            yield path
        finally:
            handle.seek(0)
            if os.name=='nt':
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def _read(path):
    if not path.exists():
        return None
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict):
        raise ValueError('Invalid progress checkpoint')
    return value


def _write(path,value):
    fd,name=tempfile.mkstemp(dir=path.parent,suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            stream.write(_json(value));stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _first_codex_id(path):
    with Path(path).open('rb') as stream:
        raw=stream.readline(MAX_LINE+1)
    if len(raw)>MAX_LINE:raise ValueError('Transcript event exceeds bounded progress scan')
    try:row=json.loads(raw)
    except ValueError:return None
    return (row.get('payload') or {}).get('id') if isinstance(row,dict) and row.get('type')=='session_meta' else None


def _source(data):
    source=Path(data['transcript_path'])
    expected=data['session_id']
    observed=_first_codex_id(source)
    if observed is None or observed==expected:return source
    # Some resumed host callbacks supply another thread's rollout path. Never
    # relax identity: resolve only a unique exact-id file in the host's store.
    if not re.fullmatch(r'[A-Za-z0-9_-]+',expected):
        raise ValueError('Transcript belongs to another session')
    root=Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')/'sessions'
    matches=[p for p in root.glob('**/rollout-*-'+expected+'.jsonl')
             if p.is_file() and _first_codex_id(p)==expected]
    if len(matches)!=1:raise ValueError('Transcript belongs to another session; no unique verified source')
    return matches[0]


def collect(data, *, max_bytes=None):
    sid=data.get('session_id')
    if not sid or not data.get('transcript_path'):
        raise ValueError('Progress checkpoint requires session and transcript')
    source=_source(data)
    size=source.stat().st_size
    if size>MAX_SOURCE:
        raise ValueError('Transcript exceeds bounded progress scan')
    if max_bytes is not None:
        if type(max_bytes) is not int or not 0<=max_bytes<=size:raise ValueError('Invalid reconstruction boundary')
        size=max_bytes
    record={'contract':'compaction-progress-v1','session_id':sid,'continuation_rules':RULES,
        'objective':{'text':'Active objective not structurally recorded; reconcile the latest request with the last stated work.', 'status':'unconfirmed'},
        'next_action':{'text':'Reconcile the last stated work and observed receipts before choosing the next action.', 'status':'unconfirmed'},
        'agents':{},'completed_steps':[],'failed_steps':[],'reported_completed':[],'recent_work':[],
        'addressed_requests':[],'user_directions':[],'running_jobs':{},'time_context':{},'authority':'historical_data'}
    digest=hashlib.sha256()
    offset=0
    session_verified=False
    ancestors=set()
    calls={}
    progress_offset=-1
    latest_work_offset=-1
    current_request=None
    request_responded=False
    event_timestamp=None
    def evidence(raw,item):
        return {'offset':offset,'bytes':len(raw),'sha256':_sha(raw),'event_id':item.get('id'), 'timestamp':event_timestamp}
    def message(role,text,ev):
        nonlocal current_request,request_responded
        if not text:return
        if role=='user':
            current_request={'text':text,'historical':True,'provenance':'user_message','evidence':ev}
            request_responded=False
            record['latest_request']=current_request
            record['user_directions']=(record['user_directions']+[current_request])[-6:]
        elif role=='assistant':
            stated={'text':text,'status':'agent_stated','evidence':ev}
            record['recent_work']=(record['recent_work']+[stated])[-2:]
            if current_request and not request_responded:
                record['addressed_requests']=(record['addressed_requests']+[
                    {'request':current_request,'response':stated,'status':'response_recorded_not_task_completion'}])[-6:]
                request_responded=True
    def observed(item,ev):
        nonlocal latest_work_offset
        kind=item.get('type')
        if kind in {'SubAgentActivity','CommandExecution','FileChange'}:
            latest_work_offset=offset
        if kind=='SubAgentActivity' and isinstance(item.get('agent_path'),str):
            name=item['agent_path']
            agent=record['agents'].setdefault(name,{'handoff':'already_assigned'})
            activity=item.get('kind')
            if activity in {'started','completed','errored','shutdown'}:
                agent.update(status={'started':'running','completed':'completed','errored':'failed','shutdown':'stopped'}[activity],
                             thread_id=item.get('agent_thread_id'),evidence=ev)
        elif kind=='CommandExecution' and type(item.get('exit_code')) is not int and item.get('process_id'):
            record['running_jobs'][str(item['process_id'])]={'status':'running_at_capture','evidence':ev}
        elif kind=='CommandExecution' and item.get('status') in {'completed','failed'} and type(item.get('exit_code')) is int:
            record['running_jobs'].pop(str(item.get('process_id')),None)
            step={'tool_id':item.get('id'),'fact':'Command exited with code '+str(item['exit_code']),
                  'output_sha256':_sha(str(item.get('stdout','')).encode()),'evidence':ev}
            name='completed_steps' if item['exit_code']==0 else 'failed_steps'
            record[name]=(record[name]+[step])[-5:]
        elif kind=='FileChange' and item.get('status')=='completed':
            step={'tool_id':item.get('id'),'fact':'File change applied; verification is separate',
                  'paths':list(item.get('changes') or {})[:5],'evidence':ev}
            record['completed_steps']=(record['completed_steps']+[step])[-5:]
        elif kind=='McpToolCall':
            progress(item,ev)
    def progress(item,ev):
        nonlocal progress_offset
        result=item.get('result') or {}
        if (item.get('tool')!='boswell_bookmark' or str(item.get('server','')).lower() not in {'boswell','boswell-atlas'}
            or item.get('status')!='completed' or not isinstance(result,dict) or result.get('isError')):
            return
        receipt=result.get('structuredContent')
        if not isinstance(receipt,dict):
            for block in result.get('content') or []:
                if isinstance(block,dict) and block.get('type')=='text':
                    try: receipt=json.loads(block.get('text',''))
                    except ValueError: continue
                    if isinstance(receipt,dict):break
        if not isinstance(receipt,dict) or receipt.get('status')!='bookmarked' or not receipt.get('candidate_id'):
            return
        content=(item.get('arguments') or {}).get('content')
        if not isinstance(content,dict) or not all(isinstance(content.get(k),str) for k in ('objective','next_action')):
            return
        record['objective']={'text':_text(content['objective']),'status':'agent_recorded','evidence':ev}
        for key in ('root_objective','current_subtask'):
            if isinstance(content.get(key),str):
                record[key]={'text':_text(content[key]),'status':'agent_recorded','evidence':ev}
        for key in ('blockers','outstanding_verification','accepted_corrections','evidence_scope'):
            if isinstance(content.get(key),list):
                record[key]=[{'text':_text(x,300),'status':'agent_reported_not_authority','evidence':ev}
                    for x in content[key][-6:] if isinstance(x,str)]
        record['next_action']={'text':_text(content['next_action']),'status':'agent_recorded','evidence':ev}
        record['reported_completed']=[{'text':_text(x,300),'status':'agent_reported','evidence':ev}
            for x in (content.get('completed') or [])[-5:] if isinstance(x,str)]
        record['reported_ownership']={'value':content.get('ownership') or {},'status':'agent_reported','evidence':ev}
        progress_offset=offset
    with source.open('rb') as stream:
        while offset<size:
            raw=stream.readline(min(MAX_LINE,size-offset)+1)
            if len(raw)>MAX_LINE:
                raise ValueError('Transcript event exceeds bounded progress scan')
            if not raw.endswith(b'\n'):
                # A concurrently appended partial event is not evidence yet.
                break
            digest.update(raw)
            try:
                row=json.loads(raw)
            except (ValueError,UnicodeError):
                offset+=len(raw)
                continue
            payload=row.get('payload') or {}
            event_timestamp=row.get('timestamp')
            if row.get('type')=='compacted':
                record['last_compaction']={'offset':offset,'timestamp':event_timestamp}
            if event_timestamp:
                record['time_context']['last_event_at']=event_timestamp
            if row.get('type')=='session_meta':
                if payload.get('id')!=sid:
                    if not session_verified or payload.get('id') not in ancestors:
                        raise ValueError('Transcript belongs to another session')
                    if payload.get('forked_from_id'):ancestors.add(payload['forked_from_id'])
                    offset+=len(raw)
                    continue
                if payload.get('forked_from_id'):ancestors.add(payload['forked_from_id'])
                session_verified=True
                record['time_context']['session_started_at']=payload.get('timestamp') or event_timestamp
            # Claude records session identity on each message.
            if row.get('sessionId'):
                if row['sessionId']!=sid:
                    raise ValueError('Transcript belongs to another session')
                session_verified=True
            if row.get('type')=='event_msg' and payload.get('type')=='item_completed':
                item=payload.get('item') or {}
                observed(item,evidence(raw,item))
            if row.get('type')=='response_item':
                ev=evidence(raw,payload)
                typ=payload.get('type')
                if typ=='message' and payload.get('role') in {'assistant','user'}:
                    text=_message(payload.get('content'))
                    message(payload['role'],text,ev)
                elif typ=='agent_message':
                    text=_message(payload.get('content')) or ''
                    if text.startswith('Message Type: FINAL_ANSWER'):
                        agent=record['agents'].setdefault(payload['author'],{'handoff':'already_assigned'})
                        agent.update(status='completed',report=_text(text,400),evidence=ev)
                elif typ=='function_call' and payload.get('namespace')=='collaboration':
                    try: args=json.loads(payload.get('arguments') or '{}')
                    except ValueError: args={}
                    calls[payload.get('call_id')]=(payload.get('name'),args)
                elif typ=='function_call_output' and payload.get('call_id') in calls:
                    name,args=calls.pop(payload['call_id'])
                    # Only a successful explicit follow-up starts another turn.
                    if name=='followup_task' and payload.get('output')=='':
                        target=args.get('target','')
                        matches=[a for a in record['agents'] if a==target or a.endswith('/'+target)]
                        if len(matches)==1:
                            record['agents'][matches[0]].update(status='running',evidence=ev)
            if (row.get('type') in {'assistant','user'} and row.get('sessionId')==sid
                and not row.get('isCompactSummary') and not row.get('isMeta')):
                blocks=(row.get('message') or {}).get('content')
                ev=evidence(raw,{'id':row.get('uuid')})
                message(row['type'],_message(blocks),ev)
                for block in blocks if isinstance(blocks,list) else []:
                    if not isinstance(block,dict):continue
                    if row['type']=='assistant' and block.get('type')=='tool_use':
                        calls[block.get('id')]=(block.get('name'),block.get('input') or {})
                    elif row['type']=='user' and block.get('type')=='tool_result' and block.get('tool_use_id') in calls:
                        name,args=calls.pop(block['tool_use_id'])
                        result=row.get('toolUseResult') or {}
                        if name in {'Agent','Task'}:
                            aid=result.get('agentId') if isinstance(result,dict) else None
                            aid=str(aid or block['tool_use_id'])
                            record['agents'][aid]={'handoff':'already_assigned','status':('failed' if block.get('is_error') else
                                'running' if args.get('run_in_background') else 'completed'), 'evidence':ev}
                            latest_work_offset=offset
                        elif name.endswith('__boswell_bookmark'):
                            server=name.split('__')[-2]
                            text=_message(block.get('content'))
                            progress({'tool':'boswell_bookmark','server':server,'status':'completed','arguments':args,
                                      'result':{'isError':bool(block.get('is_error')),'content':[{'type':'text','text':text or ''}]}},ev)
            # No recursion into compacted replacement_history, tool prose, or
            # encrypted content: old requests and forged events stay data.
            offset+=len(raw)
    if not session_verified:
        raise ValueError('Cannot verify transcript session identity')
    record['source']={'transcript_path':str(source),'bytes':offset,'sha256':digest.hexdigest()}
    if Path(data['transcript_path']).resolve()!=source.resolve():
        record['source']['supplied_transcript_path']=data['transcript_path']
    record['time_context']['captured_at']=datetime.now(timezone.utc).isoformat()
    record['time_context']['elapsed_time_is_not_work_time']=True
    record.setdefault('root_objective',dict(record['objective']))
    record.setdefault('current_subtask',dict(record['objective']))
    record['checkpoint_id']=_sha((sid+':'+digest.hexdigest()+':'+str(offset)).encode())
    request_offset=(record.get('latest_request') or {}).get('evidence',{}).get('offset',-1)
    record['request_after_recorded_plan']=request_offset>progress_offset
    if progress_offset<0 and record['recent_work']:
        record['next_action']={**record['recent_work'][-1],'status':'last_stated_work_requires_reconciliation'}
    elif progress_offset>=0 and (latest_work_offset>progress_offset or request_offset>progress_offset):
        record['recorded_next_action']=record['next_action']
        record['next_action']={'text':'Newer work exists after the recorded plan. Reconcile current receipts and last stated work before resuming; do not replay completed steps.',
                               'status':'requires_reconciliation'}
    return record


def prepare(data):
    with _locked(data['session_id']) as path:
        try:
            record=collect(data)
        except Exception as exc:
            _write(path,{'session_id':data['session_id'],'failure':str(exc)[:300],
                'failed_at':time.time(),'requested_transcript_path':data.get('transcript_path')})
            raise
        previous=_read(path)
        if previous and previous.get('record',{}).get('checkpoint_id')==record['checkpoint_id']:
            return record
        _write(path,{'record':record,'record_sha256':_sha(_json(record).encode()),'restored':False})
    return record


def audit(data, event, phase, **details):
    """Bounded callback evidence, without prompt/tool arguments or secrets."""
    try:
        with _locked(data.get('session_id')) as checkpoint:
            path=checkpoint.with_suffix('.callbacks.json')
            saved=_read(path) or {'events':[]}
            saved['events']=(saved['events']+[{'event':event,'phase':phase,
                'at':datetime.now(timezone.utc).isoformat(),**details}])[-30:]
            _write(path,saved)
    except (OSError, ValueError):
        pass


def _compacted_after(record, data):
    """Require an append-only host boundary after this captured source prefix."""
    source=record['source']
    path=Path(source['transcript_path'])
    if not data.get('transcript_path') or _source(data).resolve()!=path.resolve():
        return False
    size=path.stat().st_size
    captured=source['bytes']
    if size<=captured or size>MAX_SOURCE:
        return False
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        remaining=captured
        while remaining:
            chunk=stream.read(min(remaining,1024*1024))
            if not chunk:return False
            digest.update(chunk);remaining-=len(chunk)
        if digest.hexdigest()!=source['sha256']:
            raise ValueError('Progress source prefix changed before recovery')
        remaining=size-captured
        while remaining:
            raw=stream.readline(min(MAX_LINE,remaining)+1)
            if len(raw)>MAX_LINE:raise ValueError('Oversized post-checkpoint event')
            if not raw:return False
            remaining-=len(raw)
            try:item=json.loads(raw)
            except ValueError:continue
            if isinstance(item,dict) and item.get('type')=='compacted':
                return True
    return False


OVERFLOW_RULE = ('The full progress record exceeded the context budget and is NOT inlined. '
                 'Before any other action, read checkpoint_path in full; this digest only orients.')


def _overflow_digest(record, path, limit=360):
    """Bounded orientation when the full projection cannot fit context."""
    def short(item):
        if not isinstance(item,dict):return None
        value=item.get('text') if 'text' in item else item.get('value')
        if not isinstance(value,str):value=_json(value) if value is not None else None
        if not limit or value is None:return None
        return value if len(value)<=limit else value[:limit]+'?'
    digest={'checkpoint_path':str(path),'full_record_inlined':False,
        'overflow_rule':OVERFLOW_RULE,'continuation_rules':RULES}
    for key in ('root_objective','objective','current_subtask','next_action',
                'reported_ownership','latest_request'):
        value=short(record.get(key))
        if value:digest[key]=value
    agents=record.get('agents')
    if isinstance(agents,dict):
        digest['agent_status']={name:agent.get('status') for name,agent in agents.items()
                                if isinstance(agent,dict)}
    if record.get('running_jobs'):digest['running_jobs']=record['running_jobs']
    return digest


def restore(data, *, consume=True, after_compaction=False, max_context=MAX_CONTEXT):
    with _locked(data.get('session_id')) as path:
        saved=_read(path)
        if saved is None:
            if after_compaction:return None
            raise ValueError('Progress checkpoint is missing; recover current work from transcript evidence and existing agent ownership before continuing')
        if saved.get('failure'):
            if saved.get('session_id')!=data['session_id']:
                raise ValueError('Failed progress checkpoint session mismatch')
            if not after_compaction:
                raise ValueError('Progress capture failed: '+saved['failure'])
            # Retry capture from verified host evidence, never reuse the prior
            # invalidated checkpoint. If compaction actually followed failure,
            # reconstruct its pre-boundary prefix; otherwise remain pending.
            failed_at=saved.get('failed_at',path.stat().st_mtime)
            recovered=collect(data)
            boundary=recovered.get('last_compaction')
            if boundary:
                stamp=boundary.get('timestamp')
                if not isinstance(stamp,str):raise ValueError('Compaction boundary has no timestamp')
                occurred=datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
                if occurred>=failed_at:
                    recovered=collect(data,max_bytes=boundary['offset'])
            saved={'record':recovered,'record_sha256':_sha(_json(recovered).encode()),'restored':False,
                'reconstructed_after_failure':{'reason':saved['failure'],'failed_at':failed_at}}
            _write(path,saved)
        record=saved.get('record')
        if (not isinstance(record,dict) or record.get('session_id')!=data['session_id']
            or saved.get('record_sha256')!=_sha(_json(record).encode())):
            raise ValueError('Progress checkpoint integrity mismatch')
        if saved.get('restored'):
            return None
        if after_compaction and not _compacted_after(record,data):
            return None
        # Full evidence remains in the durable checkpoint; never drop ownership
        # or completed-handoff status merely to fit model context.
        projected=json.loads(_json(record))
        # Transport guidance belongs to this installed reader, not the history.
        projected['continuation_rules']=RULES
        projected['checkpoint_path']=str(path)
        def render():return HEADER+'\n'+_json(projected)
        if len(render())>max_context:
            # Source hashes remain in the full checkpoint. Compact repeated
            # citations before considering omission of any progress evidence.
            def compact(value):
                if isinstance(value,list):
                    for item in value:compact(item)
                elif isinstance(value,dict):
                    if isinstance(value.get('evidence'),dict):
                        value['evidence'].pop('sha256',None)
                        value['evidence'].pop('bytes',None)
                    if isinstance(value.get('text'),str):
                        value['text']=_text(value['text'],360)
                    for item in value.values():compact(item)
            compact(projected)
            for agent in projected['agents'].values():agent.pop('report',None)
            direction_offsets=set()
            for pair in projected['addressed_requests']:
                direction_offsets.add(pair['request']['evidence']['offset'])
            if projected.get('latest_request'):
                direction_offsets.add(projected['latest_request']['evidence']['offset'])
            projected['user_directions']=[
                {'request_offset':item['evidence']['offset']}
                if item['evidence']['offset'] in direction_offsets else item
                for item in projected['user_directions']]
            projected['addressed_request_columns']=['request_excerpt','response_excerpt',
                'request_offset','response_offset','request_timestamp']
            projected['addressed_request_status']='response_recorded_not_task_completion'
            projected['excerpt_notice']='Excerpts are historical; read cited events for full text.'
            def excerpt(text):
                return text if len(text)<=128 else text[:128]+'?'
            projected['addressed_requests']=[[
                excerpt(pair['request']['text']),excerpt(pair['response']['text']),
                pair['request']['evidence']['offset'],pair['response']['evidence']['offset'],
                pair['request']['evidence'].get('timestamp')]
                for pair in projected['addressed_requests']]
            projected['full_evidence_in_checkpoint']=True
            # Progress fields often cite the same bookmark event. Intern those
            # citations rather than shorten or discard their semantic content.
            citations={}
            def shared_citations(value):
                if isinstance(value,list):
                    for item in value:shared_citations(item)
                elif isinstance(value,dict):
                    ev=value.get('evidence')
                    if isinstance(ev,dict):
                        citations.setdefault(_json(ev),[]).append(value)
                    for key,item in value.items():
                        if key!='evidence':shared_citations(item)
            shared_citations(projected)
            refs={}
            for encoded,uses in citations.items():
                if len(uses)>1:
                    ref=str(len(refs))
                    refs[ref]=json.loads(encoded)
                    for use in uses:use['evidence']={'ref':ref}
            if refs:projected['evidence_refs']=refs
        for key in ('completed_steps','failed_steps','reported_completed','recent_work'):
            while len(render())>max_context and projected.get(key):
                projected[key].pop(0)
                projected['additional_evidence_in_checkpoint']=True
        if len(render())>max_context:
            # Failing closed here denied every prompt and tool call, so the
            # agent could never read the checkpoint it was told to read. Emit a
            # bounded digest instead; the full record stays on disk, unchanged.
            projected=_overflow_digest(record,path)
            for limit in (240,120,60,0):
                if len(render())<=max_context:break
                projected=_overflow_digest(record,path,limit)
            if len(render())>max_context:
                projected={'checkpoint_path':str(path),'full_record_inlined':False,
                    'overflow_rule':OVERFLOW_RULE}
        text=render()
        # The host has no delivery acknowledgment. Serialize and mark before
        # emitting: concurrent PostCompact/SessionStart cannot replay progress.
        if consume:
            saved.update(restored=True,restored_at=time.time())
            _write(path,saved)
        return text
