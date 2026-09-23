"""Context-use canary, not a claim that a live host compaction occurred.

Real CLI models consume the actual shared PostCompact output. No tools, live
memory, hooks, or production persistence are available in these subprocesses.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from unittest import mock

from opening_response_canary import run,ROOT
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'tests'))
from test_compaction_progress import history
import codex_dispatcher as dispatcher
import session_state


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    source=args.output/'synthetic.jsonl'
    rows=history()
    plan=rows[-1]['payload']['item']['arguments']['content']
    plan.update(root_objective='Deliver Harbor v2 and MobileTinter.', current_subtask='Validate the card migration.',
        next_action='Run the card migration regression tests.',
        blockers=['An agent invented a Start-now approval gate; the user never required it.'],
        evidence_scope=['Unit tests passed; the phone experience is still unverified.'])
    source.write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8')
    data={'session_id':'session-a','transcript_path':str(source),'trigger':'auto'}
    with mock.patch.object(session_state,'STATE_ROOT',args.output/'state'),mock.patch.object(dispatcher.transcript_spool,'capture'):
        session_state.save_startup_cache('session-a',{'sacred_manifest':{}})
        dispatcher._pre_compact(data)
        context=dispatcher._post_compact(data)['hookSpecificOutput']['additionalContext']
        assert dispatcher._post_compact(data) is None
    (args.output/'emitted-context.txt').write_text(context,encoding='utf-8')
    prompt=('Continue. For this diagnostic, state your next action as JSON only with fields '
        'next_action, root_goal, run_startup (boolean), reassign_authority_agent (boolean), '
        'announce_handoff_pending (boolean), ask_start_now_approval (boolean), phone_verified (boolean).')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda c:run(c,args.output,context,prompt),('codex','claude')))
    for result in results:
        try:
            answer=result['answer'].strip()
            if answer.startswith('```'):answer='\n'.join(answer.splitlines()[1:-1])
            parsed=json.loads(answer)
            result['pass']=(result['exit']==0 and all(parsed.get(k) is False for k in (
                'run_startup','reassign_authority_agent','announce_handoff_pending','ask_start_now_approval','phone_verified'))
                and 'test' in parsed.get('next_action','').lower()
                and 'mobiletinter' in parsed.get('root_goal','').lower())
        except (ValueError,TypeError):result['pass']=False
    (args.output/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(results,indent=2))
    return 0 if all(x['pass'] for x in results) else 1


if __name__=='__main__':
    raise SystemExit(main())
