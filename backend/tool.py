"""On-demand JSON command tool for ChatGPT Work. No dashboard or daemon required.

python -m backend.tool scan --limit 5
python -m backend.tool link https://employer.example/jobs/123
python -m backend.tool call --request-file request.json
"""
import argparse
import json
import os
import time
import sys
from pathlib import Path
from dotenv import load_dotenv
from pydantic import Field
from .db import ROOT, audit, connect, digest, get_row, init, packed, set_setting, setting, uid
from .discovery import normalize_url, refresh
from .fixtures import DEMO, DEMO_REQUIREMENTS
from .models import Candidate, Profile, StrictModel
from .pipeline import optimize
from .providers import DemoGenerator, DemoJudge, LayaJudge, ProviderError
from .postings import fetch_posting, save_snapshot
from .artifacts import packet_zip, resume_pdf, resume_html
from .application_history import (BLOCKED_STATES, canonical, history_for, history_index,
                                  import_applied_links, init_history, link_urls, record_application)


def initialize():
    # Separate the lightweight tool's durable state from the abandoned dashboard demo.
    os.environ.setdefault('APP_DB',str(ROOT/'data/autoapply-tool.sqlite'))
    init()
    init_history()
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS tool_scans(id TEXT PRIMARY KEY, created REAL NOT NULL, source_results TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tool_seen(url TEXT PRIMARY KEY, material_hash TEXT NOT NULL, first_scan TEXT NOT NULL, last_scan TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tool_reviews(id TEXT PRIMARY KEY, job_id TEXT, profile_id TEXT, snapshot_id TEXT, round INTEGER, content TEXT, hash TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS tool_review_attempts(context TEXT, round INTEGER, created REAL, PRIMARY KEY(context,round));
        ''')


class DirectApplicationInput(StrictModel):
    url: str = Field(min_length=1, max_length=4096)
    company: str | None = Field(default=None, min_length=1, max_length=200)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    location: str | None = Field(default=None, min_length=1, max_length=200)


def add_application_link(url, company=None, title=None, location=None):
    """Register a supplied link locally; never fetch, infer eligibility, or submit."""
    supplied=DirectApplicationInput(url=url,company=company,title=title,location=location)
    normalized=normalize_url(supplied.url)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        target=canonical(c,normalized)
        row=c.execute('SELECT * FROM jobs WHERE url=?',(target,)).fetchone()
        if row is None:
            # Reuse a repository/direct record for an already observed redirect.
            row=next((r for r in c.execute('SELECT * FROM jobs ORDER BY created,id') if canonical(c,r['url'])==target),None)
        created=row is None
        if created:
            values={'url':target,'company':supplied.company or 'Employer not yet verified',
                    'title':supplied.title or 'Direct application - JD pending',
                    'location':supplied.location or 'Location not yet verified'}
            job_id=digest(target)[:24]
            now=time.time()
            c.execute('INSERT INTO jobs(id,url,company,title,location,term,material_hash,created,updated) VALUES (?,?,?,?,?,?,?,?,?)',
                      (job_id,target,values['company'],values['title'],values['location'],'Term not yet verified',digest(values),now,now))
            row=c.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        job=dict(row)
        c.execute('INSERT OR IGNORE INTO source_jobs VALUES (?,?)',('direct-link',job['id']))
        history=history_for(c,target)
        state=history['state'] if history else 'NOT_RECORDED'
        blocked=state in BLOCKED_STATES or job['state']=='SKIPPED'
        snapshot=c.execute('SELECT id FROM snapshots WHERE job_id=? ORDER BY created DESC LIMIT 1',(job['id'],)).fetchone()
        audit(c,'DIRECT_LINK_ADDED' if created else 'DIRECT_LINK_REUSED',job['id'])
    return {'job_id':job['id'],'job':job,'input_type':'direct_application_link',
            'canonical_url':target,'created':created,'application_status':state,'blocked':blocked,
            'snapshot_id':snapshot['id'] if snapshot else None,
            'next_step':('Stop: application history or skipped state blocks this role.' if blocked else
                         'Read the official JD, verify employer/title/location, resolve redirects, then continue the existing tailoring workflow.'),
            'note':'Saved locally only. No repository scan, page fetch, personal-data disclosure, or application submission occurred.'}


def scan_jobs(limit=5, query='', new_only=False, cached=False):
    from .user_context import get_user_context
    if not 1 <= limit <= 30:
        raise ValueError('Scan batch must contain 1 to 30 roles')
    sources = refresh(cached=cached)
    scan_id = uid()
    results, excluded, counts = [], {}, {'new':0,'changed':0,'backlog':0}
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        initial = c.execute('SELECT 1 FROM tool_scans LIMIT 1').fetchone() is None
        c.execute('INSERT INTO tool_scans VALUES (?,?,?)', (scan_id,time.time(),packed(sources)))
        rows = c.execute('SELECT j.*,group_concat(s.source_id) AS sources FROM jobs j LEFT JOIN source_jobs s ON s.job_id=j.id GROUP BY j.id ORDER BY j.updated DESC,j.id').fetchall()
        unique = set()
        histories = history_index(c)
        # Keep scans bounded in output, not in state: seen does not mean applied.
        for row in rows:
            job = dict(row)
            key = canonical(c, job['url'])
            previous = c.execute('SELECT material_hash FROM tool_seen WHERE url=?', (job['url'],)).fetchone()
            change = 'new' if not previous else ('changed' if previous[0] != job['material_hash'] else 'backlog')
            c.execute('INSERT INTO tool_seen VALUES (?,?,?,?) ON CONFLICT(url) DO UPDATE SET material_hash=excluded.material_hash,last_scan=excluded.last_scan',
                      (job['url'],job['material_hash'],scan_id,scan_id))
            if key in unique:
                continue
            unique.add(key)
            history = histories.get(key)
            blocked_state = history['state'] if history and history['state'] in BLOCKED_STATES else (
                'SKIPPED' if job['state'] == 'SKIPPED' else None)
            if blocked_state:
                excluded[blocked_state] = excluded.get(blocked_state, 0) + 1
                continue
            if query.lower() not in ' '.join([job['company'],job['title'],job['location']]).lower():
                continue
            counts[change] += 1
            if new_only and change == 'backlog':
                continue
            results.append({k:job[k] for k in ('id','company','title','location','url','sources')} | {
                'canonical_url':key, 'change':change, 'application_status':history['state'] if history else 'NOT_RECORDED',
                'next_step':'Read official JD; resolve redirect and link aliases before applying',
                'source_freshness':'CACHED' if cached else ('PARTIAL_SOURCE_FAILURE' if any('error' in s for s in sources) else 'REFRESHED')})
    results.sort(key=lambda j: {'new':0,'changed':1,'backlog':2}[j['change']])
    return {'scan_id':scan_id,'initial_scan':initial,'cached':cached,'counts':counts,'excluded':excluded,
            'user_context':get_user_context(),
            'matching_unapplied':len(results),'jobs':results[:limit], 'sources':sources,
            'history_note':'NOT_RECORDED means no application is saved locally. Import prior applied links before bulk applications.',
            'baseline_note':'First scan treats all current listings as new to this tool.' if initial else 'New and changed first; unprocessed backlog remains available.'}


def import_profile(content):
    profile = Profile.model_validate(content)
    identity = uid()
    with connect() as c:
        c.execute('INSERT INTO profiles VALUES (?,?,?,?,?,?)', (identity,packed(profile.model_dump()),digest(profile.model_dump()),0,0,time.time()))
    return {'profile_id':identity,'verified':False,'facts':profile.model_dump(),'next_step':'Have the user verify these exact facts; no provider processing has occurred.'}


def verify_profile(profile_id):
    row = get_row('profiles',profile_id)
    identity = uid()
    with connect() as c:
        c.execute('INSERT INTO profiles VALUES (?,?,?,?,?,?)', (identity,row['content'],row['hash'],1,row['synthetic'],time.time()))
        audit(c,'USER_CONFIRMED_PROFILE',identity)
    return {'profile_id':identity,'verified':True,'hash':row['hash']}


def get_tailoring_context(job_id,profile_id):
    job = get_row('jobs',job_id)
    row = get_row('profiles',profile_id)
    if not row['verified']:
        raise ValueError('User verification of the imported facts is required')
    with connect() as c:
        history = history_for(c,job['url'])
        if history and history['state'] in BLOCKED_STATES:
            raise ValueError('Application history blocks processing: '+history['state'])
        snapshot=c.execute('SELECT * FROM snapshots WHERE job_id=? ORDER BY created DESC LIMIT 1',(job_id,)).fetchone()
    if not snapshot:
        raise ValueError('Read the official JD and save its text first')
    if not row['synthetic'] and snapshot['provenance']=='SYNTHETIC':
        raise ValueError('A real profile requires a real job description')
    p=Profile.model_validate_json(row['content'])
    return {'job_id':job_id,'profile_id':profile_id,'profile_hash':row['hash'],'snapshot_id':snapshot['id'],
            'job':job,'requirements':{'r1':snapshot['text']},'snapshot_provenance':snapshot['provenance'],
            'facts':[f.model_dump() for f in p.facts],'bullets':[b.model_dump() for b in p.bullets],
            'proposal_schema':ProposalBundle.model_json_schema(),
            'instructions':'In this conversation generate three distinct alternatives per bullet. Include only evidence-supported claims. Do not self-score. Original is added by code. Save JSON and call evaluate_resume.'}


class ProposalBundle(StrictModel):
    job_id: str
    profile_id: str
    snapshot_id: str
    candidates: list[Candidate] = Field(min_length=3,max_length=18)
    regeneration_round: int = Field(default=0, ge=0,le=1)
    previous_review_id: str | None = None


def laya_consent_scope(profile_id):
    p=get_row('profiles',profile_id)
    return {'profile_id':profile_id,'profile_hash':p['hash'],'provider':'Laya',
            'url':os.getenv('LAYA_URL','http://127.0.0.1:8001'),
            'categories':['verified experience facts','resume bullet alternatives','public JD requirements'],
            'excluded':['name','contact','application answers']}


def consent_laya(profile_id):
    scope=laya_consent_scope(profile_id)
    set_setting('tool-laya-consent:'+digest(scope),True)
    return scope


def evaluate_resume(proposals, judge='laya'):
    bundle=ProposalBundle.model_validate(proposals)
    context=get_tailoring_context(bundle.job_id,bundle.profile_id)
    if bundle.snapshot_id!=context['snapshot_id']:
        raise ValueError('JD changed after generation; regenerate from current context')
    row=get_row('profiles',bundle.profile_id)
    profile=Profile.model_validate_json(row['content'])
    expected={b.id for b in profile.bullets}
    if {c.target_bullet_id for c in bundle.candidates}!=expected:
        raise ValueError('Proposals must cover exactly the existing bullet IDs')
    for bullet in profile.bullets:
        alternatives=[c for c in bundle.candidates if c.target_bullet_id==bullet.id]
        if len(alternatives)!=3 or len({c.replacement_text for c in alternatives})!=3 or any(c.replacement_text==bullet.text for c in alternatives):
            raise ValueError('Each bullet requires three distinct alternatives different from the original')
    previous=None
    if bundle.regeneration_round:
        with connect() as c:
            prior=c.execute('SELECT * FROM tool_reviews WHERE id=?',(bundle.previous_review_id,)).fetchone()
            if not prior or prior['round']!=0 or (prior['job_id'],prior['profile_id'],prior['snapshot_id'])!=(bundle.job_id,bundle.profile_id,bundle.snapshot_id):
                raise ValueError('Regeneration requires matching initial review; only one round is allowed')
            previous=json.loads(prior['content'])
    elif bundle.previous_review_id:
        raise ValueError('Initial round cannot reference a previous review')
    if judge=='demo':
        if not row['synthetic'] or profile != DEMO:
            raise ValueError('Demo evaluation accepts only the built-in synthetic profile')
        reviewer=DemoJudge()
    elif judge=='laya':
        scope=laya_consent_scope(bundle.profile_id)
        if not setting('tool-laya-consent:'+digest(scope),False):
            raise ValueError('Laya processing consent is missing for this immutable profile and endpoint')
        reviewer=LayaJudge()
    else:
        raise ValueError('Judge must be laya or demo; ChatGPT cannot approve its own proposals')
    if previous and previous['judge']!=reviewer.name:
        raise ValueError('Cannot change judges during regeneration')
    attempt_key=digest([bundle.job_id,bundle.profile_id,bundle.snapshot_id,judge])
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        claimed=c.execute('INSERT OR IGNORE INTO tool_review_attempts VALUES (?,?,?)',(attempt_key,bundle.regeneration_round,time.time())).rowcount
        if not claimed:
            raise ValueError('This review round already ran or was attempted. Do not regenerate repeatedly; inspect existing reviews or resolve the failure manually.')
    class ConversationGenerator:
        name='ChatGPT conversation (proposals supplied; no OpenAI API call)'
        def propose(self,bullet,profile,requirements):
            return [c.model_copy(deep=True) for c in bundle.candidates if c.target_bullet_id==bullet.id]
    result=optimize(profile,context['requirements']['r1'],ConversationGenerator(),reviewer)
    if previous:
        # Preserve the best eligible result seen across the one allowed revision.
        for old,new in zip(previous['decisions'],result['decisions']):
            old_winner=next(c for c in old['candidates'] if c['candidate_id']==old['selected'])
            new_winner=next(c for c in new['candidates'] if c['candidate_id']==new['selected'])
            if old_winner['eligible'] and old_winner['score']>new_winner['score']:
                old_winner={**old_winner,'candidate_id':old_winner['candidate_id']+'-previous'}
                new['candidates'].append(old_winner)
                new['selected']=old_winner['candidate_id']
                new['reason']='Retained better eligible wording from the initial round.'
        result['selected_texts']=[next(c['replacement_text'] for c in d['candidates'] if c['candidate_id']==d['selected']) for d in result['decisions']]
        result['review']=reviewer.review(result['selected_texts'],profile)
        if any(not next(c for c in d['candidates'] if c['candidate_id']==d['selected'])['eligible'] for d in result['decisions']):
            result['review']={**result['review'],'approved':False,'reason':'Selected original has unresolved factual support.'}
    payload={**result,'job':context['job'],'profile_id':row['id'],'profile_hash':row['hash'],'snapshot_id':bundle.snapshot_id,
             'synthetic':bool(row['synthetic']),'name':profile.name,'contact':profile.contact,'heading':profile.heading,
             'answers':profile.answers,'destination':context['job']['url'],'eligibility':'NEEDS_CLARIFICATION',
             'missing_answers':[k for k in ('work_authorization','sponsorship','graduation_date','availability') if k not in profile.answers],
             'snapshot':{'text':context['requirements']['r1'],'provenance':context['snapshot_provenance']},'regeneration_round':bundle.regeneration_round}
    identity=uid()
    with connect() as c:
        c.execute('INSERT INTO tool_reviews VALUES (?,?,?,?,?,?,?,?)',(identity,bundle.job_id,bundle.profile_id,bundle.snapshot_id,bundle.regeneration_round,packed(payload),digest(payload),time.time()))
    return {'review_id':identity,'approved_content':result['review']['approved'],'mocked':judge=='demo',
            'decisions':result['decisions'],'full_review':result['review'],'next_step':'Export packet, review eligibility, then use supervised Computer Use or manual handoff. Content approval is not disclosure/submission approval.'}


def export_packet(review_id):
    with connect() as c:
        row=c.execute('SELECT * FROM tool_reviews WHERE id=?',(review_id,)).fetchone()
    if not row:
        raise ValueError('Review not found')
    p=json.loads(row['content'])
    if digest(p)!=row['hash']:
        raise ValueError('Reviewed wording was altered')
    context=get_tailoring_context(row['job_id'],row['profile_id'])
    if context['snapshot_id']!=row['snapshot_id']:
        raise ValueError('Posting changed since review')
    if not p['review']['approved']:
        raise ValueError('Independent full-resume review must pass before export')
    folder=ROOT/'artifacts'/review_id
    folder.mkdir(parents=True,exist_ok=True)
    outputs={'resume.pdf':resume_pdf(p),'resume.html':resume_html(p).encode(),'packet.zip':packet_zip(p),
             'review.json':json.dumps(p,indent=2,ensure_ascii=False).encode()}
    for name,content in outputs.items():
        (folder/name).write_bytes(content)
    return {'review_id':review_id,'resume_hash':digest(p['selected_texts']),'packet_hash':row['hash'],
            'files':{name:str(folder/name) for name in outputs},'posting_url':p['destination'],
            'synthetic':p['synthetic'],'missing_answers':p['missing_answers'],'live_execution':False,
            'handoff':'Use the connected Computer Use skill in supervised mode. Confirm exact data/destination before disclosure and final submission at action time. Never send a synthetic packet.'}


def status():
    from .user_context import get_user_context
    with connect() as c:
        count=c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        history=[dict(r) for r in c.execute('SELECT * FROM application_history ORDER BY updated DESC')]
        profiles=[dict(r) for r in c.execute('SELECT id,verified,synthetic,hash FROM profiles ORDER BY created DESC')]
        reviews=[dict(r) for r in c.execute('SELECT id,job_id,profile_id,round,created FROM tool_reviews ORDER BY created DESC LIMIT 20')]
    from .latex_workflow import init_workflow
    init_workflow()
    with connect() as c:
        latex_runs=[dict(r) for r in c.execute('SELECT id,job_id,state,folder,error FROM latex_runs ORDER BY created DESC LIMIT 20')]
    return {'jobs_saved':count,'application_history':history,'profiles':profiles,'reviews':reviews,'latex_runs':latex_runs,'user_context':get_user_context(),'laya_url':os.getenv('LAYA_URL','http://127.0.0.1:8001'),
            'live_browser_execution':False,'entrypoint':'python -m backend.tool call --request-file request.json',
            'workflow':'scan/direct link → JD → protected LaTeX master → Luna alternatives → independent Sol judgment → code selects → LaTeX + optional Sol layout → Sol final PDF review → pair approval → Apply → supervised Computer Use → final submit confirmation → archive receipt'}


def demo():
    scan=scan_jobs(limit=1,query='software',cached=True)
    if not scan['jobs']:
        raise ValueError('No cached demo job available')
    job=scan['jobs'][0]
    identity='tool-synthetic-demo'
    with connect() as c:
        c.execute('INSERT OR IGNORE INTO profiles VALUES (?,?,?,?,?,?)',(identity,packed(DEMO.model_dump()),digest(DEMO.model_dump()),1,1,time.time()))
    snapshot=save_snapshot(job['id'],DEMO_REQUIREMENTS,'SYNTHETIC')
    proposals={'job_id':job['id'],'profile_id':identity,'snapshot_id':snapshot,
               'candidates':[c.model_dump() for b in DEMO.bullets for c in DemoGenerator().propose(b,DEMO,DEMO_REQUIREMENTS)]}
    review=evaluate_resume(proposals,judge='demo')
    return {'scan':scan,'review':review,'packet':export_packet(review['review_id'])}


class ToolRequest(StrictModel):
    tool: str = Field(min_length=1)
    arguments: dict[str, object] = Field(default_factory=dict)


def dispatch(request):
    envelope = ToolRequest.model_validate(request)
    name = envelope.tool
    args = envelope.arguments
    from .model_routing import model_routing_status
    from .latex_workflow import (master_status, prepare_latex_application, application_pair,
                                 approve_latex_pair, begin_latex_application, archive_application_result)
    from .user_context import get_user_context, save_user_context
    from .outreach import get_outreach_context, save_outreach_draft, list_outreach, record_outreach_result
    from .job_tracker import sync_job_tracker, update_job_tracking, archive_submitted_documents
    from .auto_tailor import tailor_alert_resume
    if any(str(key).startswith('_') for key in args):
        raise ValueError('Private dependency injection is not a tool argument')
    functions={'scan_jobs':scan_jobs,'add_application_link':add_application_link,'link_application_urls':link_urls,'record_application_result':record_application,
               'import_profile':import_profile,'verify_profile':verify_profile,'get_tailoring_context':get_tailoring_context,'import_applied_links':import_applied_links,
               'describe_laya_scope':laya_consent_scope,'record_laya_consent':consent_laya,
               'evaluate_resume':evaluate_resume,'export_packet':export_packet,'status':status,
               'get_user_context':get_user_context,'save_user_context':save_user_context,
               'get_outreach_context':get_outreach_context,'save_outreach_draft':save_outreach_draft,
               'list_outreach':list_outreach,'record_outreach_result':record_outreach_result,
               'sync_job_tracker':sync_job_tracker,'update_job_tracking':update_job_tracking,
               'archive_submitted_documents':archive_submitted_documents,
               'tailor_alert_resume':tailor_alert_resume,
               'model_routing_status':model_routing_status,
               'master_resume_status':master_status,'prepare_latex_application':prepare_latex_application,
               'application_pair':application_pair,'approve_latex_pair':approve_latex_pair,
               'begin_latex_application':begin_latex_application,'archive_application_result':archive_application_result}
    if name=='save_job_description':
        # Only public description text is stored. No scraping code or page instructions execute.
        text=args['text']
        if not isinstance(text,str) or not 50<=len(text)<=18000:
            raise ValueError('Description must contain 50 to 18000 characters')
        return {'snapshot_id':save_snapshot(args['job_id'],text,'BROWSER_OBSERVED_OFFICIAL_JD')}
    if name=='fetch_job_description':
        return {'snapshot_id':fetch_posting(args['job_id'])}
    if name not in functions:
        raise ValueError('Unknown tool: '+str(name))
    return functions[name](**args)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    load_dotenv(ROOT/'.env')
    initialize()
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    scan=sub.add_parser('scan');scan.add_argument('--limit',type=int,default=5);scan.add_argument('--query',default='');scan.add_argument('--new-only',action='store_true');scan.add_argument('--cached',action='store_true')
    link=sub.add_parser('link',help='Start from a direct application URL instead of scanning repos')
    link.add_argument('url');link.add_argument('--company');link.add_argument('--title');link.add_argument('--location')
    sub.add_parser('status');sub.add_parser('demo');sub.add_parser('sync-tracker')
    call=sub.add_parser('call');call.add_argument('--request-file',type=Path,required=True)
    args=parser.parse_args()
    try:
        from .job_tracker import sync_job_tracker
        if args.command != 'sync-tracker':
            sync_job_tracker()  # Import manual statuses before discovery/history checks.
        if args.command=='scan': result=scan_jobs(args.limit,args.query,args.new_only,args.cached)
        elif args.command=='link': result=add_application_link(args.url,args.company,args.title,args.location)
        elif args.command=='status': result=status()
        elif args.command=='demo': result=demo()
        elif args.command=='sync-tracker': result=sync_job_tracker()
        else:
            if args.request_file.stat().st_size>200_000: raise ValueError('Request file exceeds limit')
            result=dispatch(json.loads(args.request_file.read_text(encoding='utf-8-sig')))
        if args.command != 'sync-tracker':
            tracker = sync_job_tracker()
            if isinstance(result, dict):
                result['job_tracker'] = tracker
        print(json.dumps({'ok':True,'result':result},ensure_ascii=False,indent=2))
    except (ValueError,TypeError,KeyError,OSError,ProviderError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False))
        raise SystemExit(1)


if __name__=='__main__':
    main()
