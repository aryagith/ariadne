import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
import httpx
from fastapi.testclient import TestClient
from backend import actions
from backend.artifacts import packet_zip, resume_html, resume_pdf
from backend.db import ROOT, connect, digest, get_row, packed, set_setting
from backend.discovery import SOURCES, import_rows, normalize_url, parse_source, refresh
from backend.fixtures import DEMO, DEMO_REQUIREMENTS
from backend.main import app, save_profile
from backend.models import Candidate, Evaluation, Profile
from backend.pipeline import enqueue, gate, optimize, work_once
from backend.postings import save_snapshot
from backend.providers import DemoGenerator, DemoJudge, LayaJudge, OpenAIGenerator, ProviderError, providers


def seed():
    source = '<table><tr><td>Example</td><td>Python intern</td><td>Remote</td><td><a href="https://job-boards.greenhouse.io/example/jobs/123">Apply</a></td><td>0d</td></tr></table>'
    import_rows('simplify-summer', source)
    with connect() as c:
        job = c.execute('SELECT id FROM jobs').fetchone()[0]
    profile = save_profile(DEMO, True, True)
    save_snapshot(job, DEMO_REQUIREMENTS, 'SYNTHETIC')
    return job, profile


def ready():
    job, profile = seed()
    task = enqueue(job, profile)
    assert work_once()
    assert get_row('tasks', task)['status'] == 'DONE'
    with connect() as c:
        return dict(c.execute('SELECT * FROM packets').fetchone())


@pytest.mark.parametrize('source', list(SOURCES))
def test_real_repository_formats(source):
    rows = parse_source((ROOT / f'data/sources/{source}.md').read_text(encoding='utf-8'), source)
    assert len(rows) > 10
    assert all(r['company'] != '↳' and r['url'].startswith('https://') for r in rows)
    if source == 'simplify-offseason':
        assert '2027' in rows[0]['term']
    if source == 'zapply':
        assert 'zapply.jobs' in rows[0]['url']


def test_import_unchanged_changed_and_cross_source_dedup():
    text = '<tr><td>Company</td><td>Intern</td><td>NY</td><td><a href="https://example.com/job?id=7&utm_source=foo">Apply</a></td><td>0d</td></tr>'
    assert import_rows('simplify-summer', text)['added'] == 1
    assert import_rows('simplify-summer', text)['added'] == 0
    assert import_rows('simplify-summer', text.replace('NY','SF'))['changed'] == 1
    z = '| **Company** | Intern | SF | 1h | | [Apply](https://example.com/job?id=7) |'
    assert import_rows('zapply', z)['added'] == 0
    with connect() as c:
        assert c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1
        assert c.execute('SELECT COUNT(*) FROM source_jobs').fetchone()[0] == 2


def test_source_outage_preserves_jobs(monkeypatch):
    seed()
    def fail(*a, **k):
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(httpx.Client, 'stream', fail)
    assert all('error' in r for r in refresh())
    with connect() as c:
        assert c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1


def test_conditional_request(monkeypatch):
    seed()
    with connect() as c:
        c.execute("UPDATE sources SET etag='known-etag'")
    seen = []
    from contextlib import contextmanager
    @contextmanager
    def response(self, method, url, headers):
        seen.append(headers)
        yield httpx.Response(304, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'stream', response)
    assert all(r['unchanged'] for r in refresh())
    assert seen[0]['If-None-Match'] == 'known-etag'


def test_url_job_parameters_and_invalid_urls():
    normalized='https://job-boards.greenhouse.io/acme/jobs/3'
    assert normalize_url(normalize_url(normalized))==normalized
    assert normalize_url('https://boards.greenhouse.io/acme/jobs/3?gh_jid=3&utm_source=foo') == 'https://job-boards.greenhouse.io/acme/jobs/3?gh_jid=3'
    for u in ['javascript:alert(1)', 'http://example.com', 'https://user:pw@example.com/x', 'https://localhost/a']:
        with pytest.raises(ValueError): normalize_url(u)


def test_required_four_candidates_and_unsupported_high_score():
    result = optimize(DEMO, DEMO_REQUIREMENTS, DemoGenerator(), DemoJudge())
    assert result['review']['approved']
    for decision in result['decisions']:
        assert len(decision['candidates']) == 4
        rejected = decision['candidates'][-1]
        assert rejected['score'] == 99 and not rejected['eligible']
        assert decision['selected'] != rejected['candidate_id']
        assert decision['selected'].endswith('-1')


@pytest.mark.parametrize('support', ['UNSUPPORTED', 'UNCLEAR'])
def test_quality_never_overrides_support(support):
    class Uncertain(DemoJudge):
        def evaluate(self, c, p, r):
            e = super().evaluate(c,p,r)
            if not c.candidate_id.endswith('original'):
                e.support = support
                e.relevance = e.clarity = e.specificity = e.concision = 100
            return e
    result = optimize(DEMO, DEMO_REQUIREMENTS, DemoGenerator(), Uncertain())
    assert all(d['selected'].endswith('original') for d in result['decisions'])


def test_margin_ties_and_weights():
    class Close(DemoJudge):
        def evaluate(self,c,p,r):
            e=super().evaluate(c,p,r)
            e.relevance=e.clarity=e.specificity=e.concision=64
            return e
    assert all(d['selected'].endswith('original') for d in optimize(DEMO, DEMO_REQUIREMENTS, DemoGenerator(), Close())['decisions'])
    e=Evaluation(support='SUPPORTED', relevance=100,clarity=0,specificity=0,concision=0,reason='',provider='test',mocked=True)
    assert e.score == 40


def test_reference_numeric_and_target_gates():
    b=DEMO.bullets[0]
    c=Candidate(target_bullet_id='b1',candidate_id='test',replacement_text='Built with Python.',source_fact_ids=['f1'],job_requirement_ids=['r1'])
    assert gate(c,b,DEMO) is None
    c.source_fact_ids=['missing']; assert gate(c,b,DEMO)
    c.source_fact_ids=['f1'];c.target_bullet_id='name';assert gate(c,b,DEMO)
    c.target_bullet_id='b1';c.replacement_text='Improved by 40%';assert gate(c,b,DEMO)


def test_strict_schema_and_unknown_fact():
    obj=DEMO.model_dump();obj['extra']='protected'
    with pytest.raises(ValueError): Profile.model_validate(obj)
    obj=DEMO.model_dump();obj['bullets'][0]['source_fact_ids']=['unknown']
    with pytest.raises(ValueError): Profile.model_validate(obj)


def test_one_round_limit_and_best_seen():
    r=optimize(DEMO, DEMO_REQUIREMENTS, DemoGenerator(), DemoJudge(),regeneration_rounds=1)
    assert len(r['decisions'][0]['candidates']) == 7
    with pytest.raises(ValueError): optimize(DEMO, '', DemoGenerator(), DemoJudge(),regeneration_rounds=2)


def test_full_document_failure_blocks():
    class Reject(DemoJudge):
        def review(self,*a): return {'approved':False,'mocked':True,'reason':'contradiction','provider':'test'}
    assert not optimize(DEMO, '', DemoGenerator(), Reject())['review']['approved']


def test_outage_and_demo_no_provider_calls(monkeypatch):
    def fail(*a,**k): raise AssertionError('Unexpected network')
    monkeypatch.setattr(httpx.Client,'post',fail)
    g,j=providers('x',True)
    assert optimize(DEMO,'',g,j)['review']['mocked']
    with pytest.raises(ProviderError): providers('x',False)
    class Down(DemoJudge):
        def evaluate(self,*a): raise ProviderError('outage')
    with pytest.raises(ProviderError): optimize(DEMO,'',g,Down())


def test_laya_contract_and_context_limit(monkeypatch):
    def post(self,url,json,headers):
        assert url.endswith('/v1/systemone') and json['model']=='typed-decisions'
        return httpx.Response(200,json={'answers':{'support':{'choice':'SUPPORTED','confidence':.95}}},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx.Client,'post',post)
    q={'support':{'type':'choice','criteria':{'SUPPORTED':'yes'},'instructions':'Check evidence'}}
    assert LayaJudge().ask({'text':'short'},q)['support']=='SUPPORTED'
    with pytest.raises(ProviderError): LayaJudge().ask({'text':'x'*1000},q)


def test_laya_uncertainty_and_missing_confidence_fail(monkeypatch):
    def post(self,url,**kwargs):
        return httpx.Response(200,json={'answers':{'s':{'choice':'SUPPORTED'}}},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx.Client,'post',post)
    with pytest.raises(ProviderError): LayaJudge().ask({}, {'s':{'criteria':{'SUPPORTED':'yes'}}})


def test_openai_contract(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','test-only')
    def post(self,url,json,headers):
        assert url=='https://api.openai.com/v1/responses'
        assert json['store'] is False and json['text']['format']['strict']
        assert 'Alex Example' not in json['input'] and 'example.invalid' not in json['input']
        candidates=[c.model_dump() for c in DemoGenerator().propose(DEMO.bullets[0],DEMO,'')]
        return httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':packed({'candidates':candidates})}]}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx.Client,'post',post)
    assert len(OpenAIGenerator().propose(DEMO.bullets[0],DEMO,'')) == 3


def test_durable_dedup_and_immutable_profile():
    job,profile=seed()
    t=enqueue(job,profile)
    assert enqueue(job,profile)==t
    assert work_once() and not work_once()
    assert get_row('tasks',t)['status']=='DONE'
    assert get_row('profiles',profile)['content']==packed(DEMO.model_dump())


def test_explicit_retry_and_worker_pause(monkeypatch):
    job,profile=seed();t=enqueue(job,profile)
    set_setting('worker_paused',True);assert not work_once()
    set_setting('worker_paused',False)
    monkeypatch.setattr('backend.pipeline.providers',lambda *a: (_ for _ in ()).throw(ProviderError('judge outage')))
    assert work_once()
    assert get_row('tasks',t)['status']=='FAILED'
    assert enqueue(job,profile)==t
    assert get_row('tasks',t)['status']=='QUEUED'


@pytest.mark.parametrize('change', ['expired','wrong-action','wrong-packet','wrong-hash','wrong-destination','wrong-identity','used'])
def test_approval_scope(change):
    p=ready();a=actions.approve(p['id'],'disclose')
    fields={'expired':('expires',0),'wrong-action':('action','submit'),'wrong-packet':('packet_id','other'),
            'wrong-hash':('packet_hash','x'),'wrong-destination':('destination','https://evil.invalid'),
            'wrong-identity':('identity','browser-worker'),'used':('used',1)}
    key,value=fields[change]
    with connect() as c: c.execute(f'UPDATE approvals SET {key}=? WHERE id=?',(value,a))
    with pytest.raises(ValueError): actions.execute_fixture(p['id'],'fill',a)


def test_missing_forged_approval_and_unsupported_operations():
    p=ready()
    for op in ['fill','upload','submit','click','Enter','javascript','network','shell','redirect']:
        with pytest.raises(ValueError): actions.execute_fixture(p['id'],op,'forged')
    with pytest.raises(ValueError): actions.execute_live()


def test_approval_double_use_and_unknown_submission():
    p=ready();a=actions.approve(p['id'],'disclose')
    assert actions.execute_fixture(p['id'],'fill',a)['status']=='WAITING_FOR_SUBMIT_APPROVAL'
    with pytest.raises(ValueError): actions.execute_fixture(p['id'],'upload',a)
    b=actions.approve(p['id'],'submit')
    assert actions.execute_fixture(p['id'],'submit',b,True)['status']=='SUBMISSION_UNKNOWN'
    with pytest.raises(ValueError): actions.execute_fixture(p['id'],'submit',b)
    with pytest.raises(ValueError): actions.approve(p['id'],'submit')


def test_concurrent_submission_only_once():
    p=ready();a=actions.approve(p['id'],'disclose');actions.execute_fixture(p['id'],'fill',a)
    approvals=[actions.approve(p['id'],'submit') for _ in range(2)]
    def submit(a):
        try: return actions.execute_fixture(p['id'],'submit',a)['status']
        except ValueError: return 'blocked'
    with ThreadPoolExecutor(2) as pool: states=list(pool.map(submit,approvals))
    assert sorted(states)==['DEMO_SUBMITTED','blocked']


def test_posting_and_packet_edits_invalidate():
    p=ready();a=actions.approve(p['id'],'disclose')
    content=json.loads(p['content']);content['selected_texts'][0]='Changed after review'
    with connect() as c: c.execute('UPDATE packets SET content=? WHERE id=?',(packed(content),p['id']))
    with pytest.raises(ValueError): actions.execute_fixture(p['id'],'fill',a)
    with connect() as c: c.execute('UPDATE packets SET content=? WHERE id=?',(p['content'],p['id']))
    save_snapshot(p['job_id'],'New job description','USER_PASTED_OFFICIAL_TEXT_UNVERIFIED')
    with pytest.raises(ValueError): actions.execute_fixture(p['id'],'fill',a)


def test_artifacts_escape_and_no_wording_change():
    p=json.loads(ready()['content'])
    html=resume_html(p)
    assert all(t in html for t in p['selected_texts'])
    p['name']='<script>alert(1)</script>'
    assert '<script>' not in resume_html(p)
    assert resume_pdf(p).startswith(b'%PDF')
    import zipfile
    with zipfile.ZipFile(io.BytesIO(packet_zip(p))) as z:
        assert {'resume.pdf','answers.json','APPLY.txt','review.json'} <= set(z.namelist())
        assert p['destination'] in z.read('APPLY.txt').decode()


def test_api_auth_origin_and_fixture_boundary():
    with TestClient(app,base_url='http://127.0.0.1:8000') as client:
        assert client.get('/api/dashboard').status_code==401
        assert client.post('/api/login',json={'password':'test-password'}).status_code==403
        client.headers.update({'Origin':'http://127.0.0.1:8000','X-Apply-Desk':'1'})
        assert client.post('/api/login',json={'password':'test-password'}).status_code==200
        assert client.get('/api/dashboard').status_code==200
        assert client.get('/api/dashboard',headers={'Host':'evil.invalid'}).status_code==403
        assert client.post('/api/worker/toggle',headers={'Origin':'https://evil.invalid'},json={}).status_code==403
        assert client.post('/api/packets/x/fixture',json={'operation':'click','approval_id':'x'}).status_code==422
        assert client.get('/api/health').json()['live_execution'] is False


def test_end_to_end_api():
    with TestClient(app,base_url='http://127.0.0.1:8000') as c:
        c.headers.update({'Origin':'http://127.0.0.1:8000','X-Apply-Desk':'1'})
        c.post('/api/login',json={'password':'test-password'})
        d=c.get('/api/dashboard').json()
        job=d['jobs'][0]['id'];profile=d['profiles'][0]['id']
        assert c.post(f'/api/jobs/{job}/practice',json={}).status_code==200
        assert c.post(f'/api/jobs/{job}/prepare',json={'profile_id':profile}).status_code==200
        packets=c.get('/api/dashboard').json()['packets'];assert len(packets)==1
        pid=packets[0]['id']
        assert c.get(f'/api/packets/{pid}/resume.pdf').content.startswith(b'%PDF')
        assert c.get(f'/api/packets/{pid}/packet.zip').content.startswith(b'PK')
        content=c.get(f'/api/packets/{pid}').json()['content']
        assert content['eligibility']=='NEEDS_CLARIFICATION'
        assert len(content['decisions'][0]['candidates'])==4
