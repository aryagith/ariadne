import pytest
from backend import tool
from backend.db import connect, digest, packed, get_row
from backend.discovery import import_rows
from backend.fixtures import DEMO,DEMO_REQUIREMENTS
from backend.postings import save_snapshot
from backend.providers import DemoGenerator, ProviderError


@pytest.fixture(autouse=True)
def tool_db(isolated_db,monkeypatch):
    tool.initialize()
    monkeypatch.setattr(tool,'refresh',lambda **kwargs:[{'source':'test','unchanged':True}])


def insert_job(job_id=123,title='Software intern'):
    import_rows('simplify-summer', f'<tr><td>Acme</td><td>{title}</td><td>Remote</td><td><a href="https://job-boards.greenhouse.io/acme/jobs/{job_id}?utm_source=test">Apply</a></td><td>0d</td></tr>')


def proposals():
    insert_job()
    job=tool.scan_jobs()['jobs'][0]
    with connect() as c:
        c.execute('INSERT INTO profiles VALUES (?,?,?,?,?,?)',('demo',packed(DEMO.model_dump()),digest(DEMO.model_dump()),1,1,0))
    snap=save_snapshot(job['id'],DEMO_REQUIREMENTS,'SYNTHETIC')
    return {'job_id':job['id'],'profile_id':'demo','snapshot_id':snap,
            'candidates':[c.model_dump() for b in DEMO.bullets for c in DemoGenerator().propose(b,DEMO,'')]}


def test_first_scan_seen_is_not_applied_and_new_only():
    insert_job()
    first=tool.scan_jobs()
    assert first['initial_scan'] and first['jobs'][0]['change']=='new'
    assert tool.scan_jobs()['jobs'][0]['change']=='backlog'
    assert tool.scan_jobs(new_only=True)['jobs']==[]
    insert_job(124)
    assert tool.scan_jobs(new_only=True)['jobs'][0]['url'].endswith('/124')


def test_changed_link_excluded_if_applied():
    insert_job();first=tool.scan_jobs()['jobs'][0]
    tool.record_application(first['url'],'APPLIED','User confirmed previous application')
    insert_job(title='Backend intern')
    result=tool.scan_jobs()
    assert not result['jobs'] and result['excluded']['APPLIED']==1


@pytest.mark.parametrize('state',['APPLIED','IN_PROGRESS','SUBMISSION_UNKNOWN','SKIPPED'])
def test_history_states_excluded(state):
    insert_job();url=tool.scan_jobs()['jobs'][0]['url']
    tool.record_application(url,state,'Observed or user-confirmed state')
    assert tool.scan_jobs()['jobs']==[]


def test_scan_excludes_job_marked_skipped_without_history():
    insert_job()
    with connect() as c:
        c.execute("UPDATE jobs SET state='SKIPPED'")
    result = tool.scan_jobs()
    assert result['jobs'] == []
    assert result['excluded'] == {'SKIPPED': 1}


@pytest.mark.parametrize('payload', [None, [], 'status', {}, {'tool': []},
    {'tool': 'status', 'arguments': None}, {'tool': 'status', 'arguments': []},
    {'tool': 'status', 'unexpected': True}])
def test_request_envelope_rejects_malformed_input(payload):
    with pytest.raises(ValueError):
        tool.dispatch(payload)


def test_cli_reports_missing_file_as_json(tmp_path):
    import json
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, '-m', 'backend.tool', 'call', '--request-file', str(tmp_path/'missing.json')],
        capture_output=True, text=True, check=False)
    assert result.returncode == 1
    response = json.loads(result.stdout)
    assert response['ok'] is False and response['error']
    assert 'Traceback' not in result.stderr


def test_history_import_rolls_back_entire_batch_on_failure(monkeypatch):
    from backend import application_history as history
    original = history.audit
    calls = []
    def fail_second(c, event, subject):
        calls.append(subject)
        if len(calls) == 2:
            raise ValueError('Simulated persistence failure')
        original(c, event, subject)
    monkeypatch.setattr(history, 'audit', fail_second)
    with pytest.raises(ValueError, match='persistence failure'):
        tool.import_applied_links(['https://example.com/jobs/1', 'https://example.com/jobs/2'], 'Confirmed')
    with connect() as c:
        assert c.execute('SELECT COUNT(*) FROM application_history').fetchone()[0] == 0


def test_redirect_alias_dedup_and_applied_import():
    insert_job()
    url='https://job-boards.greenhouse.io/acme/jobs/123'
    redirected='https://zapply.jobs/l/d/acme-123?s=github'
    import_rows('zapply',f'| **Acme** | Software intern | Remote | 1h | | [Apply]({redirected}) |')
    assert len(tool.scan_jobs()['jobs'])==2
    tool.import_applied_links([redirected],'User confirmed applied before using tool')
    tool.link_urls(redirected,url,'Browser redirected to the same Acme application ID 123')
    assert not tool.scan_jobs()['jobs']


def test_alias_cycle_and_keep_restrictive_history():
    a='https://a.example/job/1';b='https://b.example/job/1'
    tool.record_application(a,'APPLIED','Receipt observed')
    tool.record_application(b,'NOT_APPLIED','User confirmed')
    tool.link_urls(a,b,'Observed same posting');tool.link_urls(b,a,'Same identity')
    with connect() as c:
        assert tool.history_for(c,b)['state']=='APPLIED'
    with pytest.raises(ValueError):tool.record_application(b,'IN_PROGRESS','Trying again')


def test_unknown_never_reset_or_retry():
    u='https://company.example/jobs/1'
    tool.record_application(u,'IN_PROGRESS','Disclosure authorized')
    with pytest.raises(ValueError):tool.record_application(u,'IN_PROGRESS','Second worker')
    tool.record_application(u,'SUBMISSION_UNKNOWN','Approved submit about to happen')
    for s in ['NOT_APPLIED','IN_PROGRESS','SKIPPED']:
        with pytest.raises(ValueError):tool.record_application(u,s,'Retry')
    tool.record_application(u,'APPLIED','Employer receipt 123 observed')
    with pytest.raises(ValueError):tool.record_application(u,'SKIPPED','Reset')


def test_profile_import_verification_is_immutable():
    imported=tool.import_profile(DEMO.model_dump())
    verified=tool.verify_profile(imported['profile_id'])
    assert imported['profile_id']!=verified['profile_id']
    assert get_row('profiles',imported['profile_id'])['verified']==0
    assert get_row('profiles',verified['profile_id'])['verified']==1


def test_user_context_persists_without_creating_application_approval():
    saved = tool.dispatch({'tool':'save_user_context','arguments':{
        'update':{'facts':{'residence_city':'Toronto','completed_internships':4},
                  'preferences':{'preferred_coop_months':4,'conditional_coop_months':8,
                                 'conditional_coop_rule':'Only when chained with summer term',
                                 'resume_selection_rule':'Choose team entries for the JD, not by default',
                                 'mobile_revision_summary':True}},
        'evidence':'User confirmed in conversation'}})
    tool.initialize()
    read = tool.dispatch({'tool':'get_user_context','arguments':{}})
    assert read == saved == tool.status()['user_context']
    assert tool.scan_jobs()['user_context'] == read
    assert read['facts']['completed_internships'] == 4
    assert read['preferences']['conditional_coop_rule'] == 'Only when chained with summer term'
    assert read['preferences']['mobile_revision_summary'] is True
    with connect() as c:
        assert c.execute('SELECT COUNT(*) FROM approvals').fetchone()[0] == 0
    with pytest.raises(ValueError):
        tool.dispatch({'tool':'save_user_context','arguments':{
            'update':{'facts':{'unconfirmed_claim':'yes'}},'evidence':'test'}})


def test_conversation_generator_and_independent_selection():
    req=proposals()
    r=tool.evaluate_resume(req,judge='demo')
    assert r['approved_content'] and r['mocked']
    assert all(len(d['candidates'])==4 for d in r['decisions'])
    assert all(not d['candidates'][-1]['eligible'] for d in r['decisions'])
    with pytest.raises(ValueError):tool.evaluate_resume(req,judge='chatgpt')


def test_regeneration_once_and_no_repeat():
    req=proposals()
    r=tool.evaluate_resume(req,judge='demo')
    with pytest.raises(ValueError):tool.evaluate_resume(req,judge='demo')
    req['regeneration_round']=1;req['previous_review_id']=r['review_id']
    assert tool.evaluate_resume(req,judge='demo')['approved_content']
    with pytest.raises(ValueError):tool.evaluate_resume(req,judge='demo')


def test_real_profile_cannot_use_demo_and_laya_needs_consent():
    req=proposals()
    real=tool.import_profile(DEMO.model_dump())
    verified=tool.verify_profile(real['profile_id'])
    req['profile_id']=verified['profile_id']
    save_snapshot(req['job_id'],'Actual official employer job text '*4,'BROWSER_OBSERVED_OFFICIAL_JD')
    req['snapshot_id']=tool.get_tailoring_context(req['job_id'],req['profile_id'])['snapshot_id']
    with pytest.raises(ValueError,match='built-in synthetic'):tool.evaluate_resume(req,judge='demo')
    with pytest.raises(ValueError,match='consent'):tool.evaluate_resume(req,judge='laya')


def test_laya_outage_no_approval_or_fallback(monkeypatch):
    req=proposals();tool.consent_laya('demo')
    def fail(*a):raise ProviderError('Laya offline')
    monkeypatch.setattr(tool.LayaJudge,'evaluate',fail)
    with pytest.raises(ProviderError):tool.evaluate_resume(req,judge='laya')
    with connect() as c: assert c.execute('SELECT COUNT(*) FROM tool_reviews').fetchone()[0]==0


def test_jd_change_invalidates_generated_context():
    req=proposals()
    save_snapshot(req['job_id'],'Changed official job text '*6,'BROWSER_OBSERVED_OFFICIAL_JD')
    with pytest.raises(ValueError,match='changed'):tool.evaluate_resume(req,judge='demo')


def test_tools_reject_browser_operations_and_malformed_requests():
    for name in ['submit','click','shell','network','approve_application']:
        with pytest.raises(ValueError):tool.dispatch({'tool':name,'arguments':{}})
    with pytest.raises(ValueError):tool.dispatch({'tool':'save_job_description','arguments':{'job_id':'x','text':'tiny'}})


def test_direct_link_without_repository_scan(monkeypatch):
    def forbidden(**kwargs): raise AssertionError('Direct link must not scan repositories')
    monkeypatch.setattr(tool,'refresh',forbidden)
    result=tool.dispatch({'tool':'add_application_link','arguments':{'url':'https://employer.example/jobs/42?utm_source=share&jobId=42'}})
    assert result['created'] and not result['blocked']
    assert result['canonical_url']=='https://employer.example/jobs/42?jobId=42'
    assert result['job']['company']=='Employer not yet verified'
    assert result['application_status']=='NOT_RECORDED'
    assert result['snapshot_id'] is None
    with connect() as c:
        assert c.execute('SELECT COUNT(*) FROM tool_scans').fetchone()[0]==0
        assert c.execute('SELECT source_id FROM source_jobs').fetchone()[0]=='direct-link'


def test_direct_link_reuses_repository_job_and_metadata():
    insert_job()
    with connect() as c: original=dict(c.execute('SELECT * FROM jobs').fetchone())
    result=tool.add_application_link('https://boards.greenhouse.io/acme/jobs/123?utm_source=phone',company='Do not overwrite')
    assert result['job_id']==original['id'] and not result['created']
    assert result['job']==original
    assert not tool.add_application_link(result['canonical_url'])['created']
    with connect() as c:
        assert c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM source_jobs').fetchone()[0]==2


@pytest.mark.parametrize('state',['APPLIED','IN_PROGRESS','SUBMISSION_UNKNOWN','SKIPPED'])
def test_direct_link_respects_aliased_history(state):
    source='https://redirect.example/job/42';destination='https://employer.example/jobs/42'
    existing=tool.add_application_link(source)
    tool.record_application(destination,state,'Previously observed outcome')
    tool.link_urls(source,destination,'Observed redirect')
    result=tool.add_application_link(destination)
    assert result['job_id']==existing['job_id'] and result['blocked']
    assert result['application_status']==state


def test_direct_link_continues_existing_tailoring_flow():
    req=proposals()
    result=tool.add_application_link('https://employer.example/jobs/42',company='Employer',title='Backend intern',location='Toronto')
    assert result['job']['company']=='Employer' and result['job']['location']=='Toronto'
    saved=tool.dispatch({'tool':'save_job_description','arguments':{'job_id':result['job_id'],'text':DEMO_REQUIREMENTS}})
    req.update(job_id=result['job_id'],snapshot_id=saved['snapshot_id'])
    assert tool.get_tailoring_context(result['job_id'],'demo')['job_id']==result['job_id']
    assert tool.evaluate_resume(req,judge='demo')['approved_content']


@pytest.mark.parametrize('url',['javascript:alert(1)','http://employer.example/jobs/42','https://user:secret@employer.example/jobs/42','https://localhost/jobs/42'])
def test_direct_link_rejects_invalid_urls(url):
    with pytest.raises(ValueError):tool.add_application_link(url)
    with connect() as c:assert c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0


def test_direct_link_cli():
    import json
    import subprocess
    import sys
    from backend.db import ROOT
    result=subprocess.run([sys.executable,'-m','backend.tool','link','https://employer.example/jobs/99','--title','Software intern'],
                          cwd=ROOT,capture_output=True,text=True,encoding='utf-8',check=True)
    payload=json.loads(result.stdout)
    assert payload['ok'] and payload['result']['job']['title']=='Software intern'
