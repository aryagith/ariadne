import json
import time
from pathlib import Path
import pytest
from backend import latex_source as source
from backend import latex_workflow as flow
from backend.db import connect
from backend.tool import initialize, add_application_link, dispatch
from backend.postings import save_snapshot
from backend.tiering import route_for


def test_source_text_preserves_currency_and_removes_math_delimiters():
    assert source.plain(r'Awarded \$4,000; tested $+30^{\circ}$ rotation.') == 'Awarded $4,000; tested +30 degrees rotation.'


TEX = r'''\documentclass[letterpaper,10pt]{article}
\pdfgentounicode=1
\begin{document}
\section{Education}
\resumeSubHeadingListStart
\resumeSubheading{Example University}{2028}{Engineering}{City}
\resumeItemListStart
\resumeItem{Coursework: Algorithms and Databases.}
\resumeItemListEnd
\resumeSubHeadingListEnd
\section{Experience}
\resumeSubHeadingListStart
\resumeSubheading{Developer}{2025}{Example Company}{Remote}
\resumeItemListStart
\resumeItem{Built a \textbf{Python} API with 20 endpoints.}
\resumeItem{Wrote automated tests using PyTest.}
\resumeItem{Designed SQL queries for a database.}
\resumeItem{Integrated a React interface with the API.}
\resumeItem{Maintained Docker deployment scripts.}
\resumeItemListEnd
\resumeSubHeadingListEnd
\section{Skills}
Python, SQL, React
\end{document}'''


@pytest.fixture
def setup_master(tmp_path, monkeypatch):
    initialize()
    folder = tmp_path/'master'
    folder.mkdir()
    (folder/'master.tex').write_text(TEX)
    monkeypatch.setattr(source,'MASTER',folder)
    source.register_master()
    monkeypatch.setattr(flow,'ARCHIVE',tmp_path/'job apps')
    job = add_application_link('https://example.com/jobs/123',company='Example',title='Developer')
    save_snapshot(job['job_id'],'Official software engineering internship: Python APIs, SQL and automated testing.','BROWSER_OBSERVED_OFFICIAL_JD')
    return job['job_id'], folder


class MockModels:
    mocked = True
    def __init__(self, final_ok=True):
        self.roles=[]
        self.final_ok=final_ok
    def call(self, role, instruction, data, schema, images=()):
        self.roles.append(role)
        if role=='skills':
            return schema(ranked_terms=data['skill_terms'])
        if role=='master_audit':
            from backend.premium_strategy import Audit
            return Audit(strongest_evidence=[],weak_explanations=[],questions_for_user=[])
        if role=='gap_analysis':
            from backend.premium_strategy import GapPlan,Requirement
            return GapPlan(requirements=[Requirement(requirement=x,support='SUPPORTED',source_bullet_ids=['b2'],treatment='Use source')
                                         for x in ('Python','SQL','testing')],
                           selected_bullet_ids=['b1','b2','b3','b4','b5','b6'],
                           edit_targets=[],omitted_material=[],skills_focus=[])
        if role=='tailor':
            return flow.Tailoring(selected_bullet_ids=['b1','b2','b3','b4','b5','b6'],
                edits=[flow.Edit(bullet_id='b2',alternatives=['Developed a Python API with 20 endpoints.','Built 20 Python API endpoints.','Implemented a Python API containing 20 endpoints.'])])
        if role=='judge':
            return flow.Judgment(selection_advice=flow.SelectionAdvice(drop_bullet_ids=[],add_bullet_ids=[],reason='MOCKED no change'), evaluations=[flow.Score(candidate_id=c['candidate_id'],support='SUPPORTED',
                relevance=70+i*8,clarity=70+i*8,specificity=70+i*8,concision=70+i*8,reason='MOCKED') for i,c in enumerate(data['candidates'])])
        if role=='layout':
            return flow.FitPlan(preset='compact',skill_lines=2,reason='MOCKED physical fit')
        if role=='content_repair':
            return flow.Layout(drop_bullet_ids=['b1'],add_bullet_ids=[],preset='compact',reason='MOCKED reviewer removal')
        return flow.FinalReview(factual_support='SUPPORTED' if self.final_ok else 'UNCLEAR',writing_ok=True,layout_ok=True,job_alignment_ok=True,issues=[])


def fake_compiler(folder):
    (folder/'resume.pdf').write_bytes(b'%PDF-mocked')
    (folder/'page-1.png').write_bytes(b'mocked-image')
    return {'passed':True,'pages':[{'text':'MOCKED rendered text'}],'page_count':1,'bounds_ok':True},[folder/'page-1.png']


def prepare(job, runner=None, compiler=None):
    return flow.prepare_latex_application(job,_runner=runner or MockModels(),_compiler=compiler or fake_compiler)


def allow_mock_for_approval_test(pair):
    # Approval-path unit tests only. Production rejects mock provenance.
    from backend.db import digest
    path = Path(pair['folder'])/'manifest.json'
    manifest=json.loads(path.read_text())
    manifest['mocked']=False
    path.write_text(json.dumps(manifest))
    with connect() as c:
        c.execute('UPDATE latex_runs SET manifest_hash=? WHERE id=?',(digest(manifest),pair['run_id']))


def test_master_protected_and_nested_parser(setup_master):
    _, folder=setup_master
    original=(folder/'master.tex').read_bytes()
    entries,bullets=source.index_master(TEX)
    assert len(entries)==2 and len(bullets)==6
    assert bullets[1]['text']=='Built a Python API with 20 endpoints.'
    result=source.render_source(TEX,['b2','b3'],{'b2':r'Use 20% & \input{secret}'})
    assert r'20\% \& \textbackslash{}input\{secret\}' in result
    assert 'Coursework:' not in result
    assert (folder/'master.tex').read_bytes()==original
    (folder/'master.tex').write_text(TEX+'changed')
    with pytest.raises(ValueError,match='Master changed'):
        source.load_master()


def test_packet_archive_and_cache(setup_master):
    job,_=setup_master
    runner=MockModels()
    result=prepare(job,runner)
    assert runner.roles==['tailor','judge','skills','final_review']
    folder=Path(result['folder'])
    assert all((folder/name).exists() for name in ['posting.txt','resume.tex','resume.pdf','job-description.txt','answers.json','manifest.json','final-review.json'])
    assert 'https://example.com/jobs/123' in (folder/'posting.txt').read_text()
    assert prepare(job,runner)['run_id']==result['run_id']
    assert len(runner.roles)==4
    with pytest.raises(ValueError,match='Mock-reviewed'):
        flow.approve_latex_pair(result['run_id'],'approved')


def test_packet_runs_one_feedback_revision_and_final_independent_review(setup_master):
    job, _ = setup_master
    class FeedbackModels(MockModels):
        def call(self, role, instruction, data, schema, images=()):
            if role == 'revise':
                self.roles.append(role)
                return flow.Revision(edits=[flow.Edit(bullet_id='b2', alternatives=[
                    'Implemented 20 endpoints in a Python API.',
                    'Created a Python API with 20 endpoints.',
                    'Developed 20 endpoints for a Python API.'])])
            if role == 'judge':
                self.roles.append(role)
                result = schema(selection_advice=flow.SelectionAdvice(
                    drop_bullet_ids=[], add_bullet_ids=[], reason='MOCKED'), evaluations=[
                    flow.Score(candidate_id=c['candidate_id'], support='SUPPORTED',
                               relevance=60, clarity=60, specificity=60, concision=60, reason='MOCKED')
                    for c in data['candidates']])
                for score in result.evaluations:
                    value = 60 if score.candidate_id.endswith('-0') else 70
                    if score.candidate_id.endswith('-r1'):
                        value = 90
                    score.relevance = score.clarity = score.specificity = score.concision = value
                return result
            return super().call(role, instruction, data, schema, images)
    runner = FeedbackModels()
    result = prepare(job, runner)
    assert runner.roles == ['tailor', 'judge', 'revise', 'judge', 'skills', 'final_review']
    folder = Path(result['folder'])
    review = json.loads((folder/'candidate-review.json').read_text())
    assert review['wording_regenerations'] == 1
    assert review['decisions'][0]['selected'].endswith('-r1')
    manifest = json.loads((folder/'manifest.json').read_text())
    assert 'wording-revision.json' in manifest['files'] and manifest['mocked']
    assert manifest['models']['judge']['model'] != manifest['models']['revise']['model']


def test_preparation_binds_observed_destination_and_invalidates_old_pair(setup_master):
    from backend.tool import link_urls
    job, _ = setup_master
    first = prepare(job)
    destination = 'https://employer.example/apply/123'
    link_urls('https://example.com/jobs/123', destination, 'Observed employer redirect')
    with pytest.raises(ValueError, match='destination changed'):
        flow.application_pair(first['run_id'])
    second = prepare(job)
    assert second['run_id'] != first['run_id']
    assert second['posting_url'] == destination
    assert Path(second['folder'], 'posting.txt').read_text().strip() == destination
    runner = MockModels()
    assert prepare(job, runner)['run_id'] == second['run_id']
    assert runner.roles == []


def test_archive_setup_failure_marks_run_failed_and_prevents_retry(setup_master, monkeypatch):
    job, _ = setup_master
    original = flow.write_json
    def fail_answers(path, value):
        if path.name == 'answers.json':
            raise OSError('Simulated disk full')
        return original(path, value)
    monkeypatch.setattr(flow, 'write_json', fail_answers)
    runner = MockModels()
    with pytest.raises(OSError, match='disk full'):
        prepare(job, runner)
    with connect() as c:
        row = c.execute('SELECT state,error FROM latex_runs').fetchone()
    assert row['state'] == 'FAILED' and 'disk full' in row['error']
    with pytest.raises(ValueError, match='Previous preparation failed'):
        prepare(job, runner)
    assert runner.roles == []


@pytest.mark.parametrize('answers', [[], '', False, 0])
def test_empty_non_mapping_answers_rejected_before_preparation(setup_master, answers):
    job, _ = setup_master
    with pytest.raises(ValueError, match='bounded string mapping'):
        flow.prepare_latex_application(job, answers, _runner=MockModels(), _compiler=fake_compiler)
    with connect() as c:
        assert c.execute('SELECT COUNT(*) FROM latex_runs').fetchone()[0] == 0


def test_premium_route_uses_same_single_reviewer_workflow(setup_master):
    _, _ = setup_master
    job = add_application_link('https://example.com/jobs/google-123',company='Google',title='Software Intern')
    save_snapshot(job['job_id'],'Official software internship: Python, SQL and testing.',
                  'BROWSER_OBSERVED_OFFICIAL_JD')
    runner = MockModels()
    result = prepare(job['job_id'],runner)
    assert result['routing']['tier']=='premium'
    assert runner.roles==['tailor','judge','skills','final_review']
    folder=Path(result['folder'])
    assert not (folder/'master-audit.json').exists() and not (folder/'gap-plan.json').exists()
    assert json.loads((folder/'manifest.json').read_text())['routing']['reason']=='explicit_top_company'


def test_one_high_level_reviewer_and_small_fit_route():
    from backend.subscription_models import ROLES
    assert ROLES['judge'] == ROLES['content_repair'] == ROLES['final_review'] == {
        'model':'gpt-6-sol','effort':'medium'}
    assert ROLES['layout'] == {'model':'gpt-6-luna','effort':'low'}
    with pytest.raises(ValueError):
        flow.FitPlan.model_validate({'preset':'compact','skill_lines':2,'reason':'fit',
                                     'drop_bullet_ids':['b2']})


def test_tier_routing_requires_clear_premium_evidence():
    job={'company':'Axon'}
    assert route_for(job,{'text':'Base Pay Range $45 - $45 USD'})['tier']=='standard'
    assert route_for(job,{'text':'Pay is $65 to $80 USD per hour.'})['tier']=='premium'
    assert route_for(job,{'text':'Pay is $50 to $80 USD per hour.'})['tier']=='standard'
    assert route_for(job,{'text':'Pay is up to $80 USD per hour.'})['tier']=='standard'
    assert route_for(job,{'text':'Pay is $80 per hour (currency unspecified).'})['tier']=='standard'
    assert route_for({'company':'Google staffing agency'},{'text':''})['tier']=='standard'


def test_layout_only_when_needed_and_one_repair(setup_master):
    job,_=setup_master
    calls=[]
    def compiler(folder):
        qa,images=fake_compiler(folder)
        calls.append(1)
        qa['passed']=len(calls)>1
        return qa,images
    runner=MockModels()
    prepare(job,runner,compiler)
    assert runner.roles==['tailor','judge','skills','layout','final_review']
    assert len(calls)==2


def test_failed_review_never_exported(setup_master):
    job,_=setup_master
    with pytest.raises(ValueError,match='final review'):
        prepare(job,MockModels(False))
    with pytest.raises(ValueError,match='Previous preparation failed'):
        prepare(job)


def test_layout_failure_records_all_reviewer_rounds_then_stops(setup_master):
    job,_=setup_master
    class DistinctRepairs(MockModels):
        def __init__(self):
            super().__init__()
            self.next_drop = 1
        def call(self,role,instruction,data,schema,images=()):
            if role == 'content_repair':
                self.roles.append(role)
                result = flow.Layout(drop_bullet_ids=[f'b{self.next_drop}'],
                                     add_bullet_ids=[],preset='compact',reason='MOCKED distinct repair')
                self.next_drop += 1
                return result
            return super().call(role,instruction,data,schema,images)
    def compiler(folder):
        qa,images=fake_compiler(folder)
        qa['passed']=False
        return qa,images
    runner=DistinctRepairs()
    with pytest.raises(ValueError,match='6 reviewer repairs'):
        prepare(job,runner,compiler)
    assert runner.roles==['tailor','judge','skills','layout',*(['content_repair']*6)]


def test_repeated_reviewer_repair_stops_without_wasting_all_calls(setup_master):
    job,_ = setup_master
    def compiler(folder):
        qa,images=fake_compiler(folder)
        qa['passed']=False
        return qa,images
    runner=MockModels()
    with pytest.raises(ValueError,match='repeated a repair'):
        prepare(job,runner,compiler)
    assert runner.roles == ['tailor','judge','skills','layout','content_repair','content_repair']


@pytest.mark.parametrize('mutation',['pdf','jd','expiry','wrong','replay'])
def test_pair_bound_approval(setup_master,mutation):
    job,_=setup_master
    pair=prepare(job)
    allow_mock_for_approval_test(pair)
    approval=flow.approve_latex_pair(pair['run_id'],'User approved exact displayed resume and JD')['approval_id']
    if mutation=='pdf':
        Path(pair['resume_pdf']).write_bytes(b'changed')
    elif mutation=='jd':
        time.sleep(.002)
        save_snapshot(job,'Changed official description with a different requirement for Java.','BROWSER_OBSERVED_OFFICIAL_JD')
    elif mutation=='expiry':
        with connect() as c:
            c.execute('UPDATE latex_pair_approvals SET expires=0')
    elif mutation=='wrong':
        approval='not-real'
    elif mutation=='replay':
        flow.begin_latex_application(pair['run_id'],approval,'Apply')
    with pytest.raises(ValueError):
        flow.begin_latex_application(pair['run_id'],approval,'Apply')


def test_unknown_then_receipt_archived_no_retry(setup_master):
    job,_=setup_master
    pair=prepare(job)
    allow_mock_for_approval_test(pair)
    approval=flow.approve_latex_pair(pair['run_id'],'Approved')['approval_id']
    flow.begin_latex_application(pair['run_id'],approval,'Apply')
    flow.archive_application_result(pair['run_id'],'SUBMISSION_UNKNOWN','About to perform user-confirmed submit')
    with pytest.raises(ValueError):
        flow.begin_latex_application(pair['run_id'],approval,'Apply again')
    flow.archive_application_result(pair['run_id'],'APPLIED','Observed confirmation reference 123')
    receipt=json.loads((Path(pair['folder'])/'receipt.json').read_text())
    assert receipt['state']=='APPLIED' and receipt['evidence'].endswith('123')


def test_receipt_history_and_packet_state_roll_back_together(setup_master):
    import sqlite3
    job, _ = setup_master
    pair = prepare(job)
    allow_mock_for_approval_test(pair)
    approval = flow.approve_latex_pair(pair['run_id'], 'Approved')['approval_id']
    flow.begin_latex_application(pair['run_id'], approval, 'Apply')
    with connect() as c:
        c.execute("""CREATE TRIGGER fail_receipt BEFORE UPDATE OF state ON latex_runs
                     WHEN NEW.state = 'APPLIED'
                     BEGIN SELECT RAISE(ABORT, 'Simulated packet update failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='packet update failure'):
        flow.archive_application_result(pair['run_id'], 'APPLIED', 'Observed receipt')
    with connect() as c:
        assert c.execute('SELECT state FROM application_history').fetchone()[0] == 'IN_PROGRESS'
        assert c.execute('SELECT state FROM latex_runs').fetchone()[0] == 'IN_PROGRESS'
    assert not Path(pair['folder'], 'receipt.json').exists()


def test_unsupported_high_score_and_numeric_invention_rejected():
    proposal=flow.Tailoring(selected_bullet_ids=['b1','b2','b3','b4','b5'],edits=[flow.Edit(bullet_id='b1',alternatives=['Built 999 Python endpoints.','Architected all company systems.','Built a Python API with 20 endpoints carefully.'])])
    context={'bullets':[{'id':f'b{i}','entry_id':'e1','text':'Built a Python API with 20 endpoints.'} for i in range(1,6)]}
    candidates=flow.candidates_for(proposal,context)
    judgments=flow.Judgment(selection_advice=flow.SelectionAdvice(drop_bullet_ids=[],add_bullet_ids=[],reason='MOCKED no change'), evaluations=[flow.Score(candidate_id=c['candidate_id'],support='UNSUPPORTED' if i==2 else 'SUPPORTED',relevance=70 if i in (0,3) else 100,clarity=70 if i in (0,3) else 100,specificity=70 if i in (0,3) else 100,concision=70 if i in (0,3) else 100,reason='test') for i,c in enumerate(candidates)])
    replacements,decisions=flow.choose(candidates,judgments)
    assert replacements=={} and decisions[0]['selected']=='b1-0'


def test_private_mock_injection_blocked():
    with pytest.raises(ValueError,match='Private'):
        dispatch({'tool':'prepare_latex_application','arguments':{'_runner':None}})


@pytest.fixture
def project_master(setup_master):
    job,folder=setup_master
    extra=r'''\section{Personal Projects}
\resumeProjectListStart
\resumeProjectHeading{Example Project}{Code}
\resumeProjectItemListStart
\resumeItem{Built a detailed Python data processing project with automated checks.}
\resumeItem{Added SQL integration tests.}
\resumeItemListEnd
\resumeProjectListEnd
'''
    (folder/'master.tex').write_text(TEX.replace(r'\section{Skills}',extra+r'\section{Skills}'))
    metadata=json.loads((folder/'source.json').read_text())
    metadata['sha256']=source.sha(folder/'master.tex')
    (folder/'source.json').write_text(json.dumps(metadata))
    return job,folder


class AddProjectModels(MockModels):
    def call(self,role,instruction,data,schema,images=()):
        if role=='layout':
            assert 'master' not in data and 'job_description' not in data
        if role=='content_repair':
            self.roles.append(role)
            assert 'master' in data
            return flow.Layout(drop_bullet_ids=[],add_bullet_ids=['b7','b8'],preset='standard',reason='Add relevant omitted project')
        if role=='final_review':
            self.final_data=data
        return super().call(role,instruction,data,schema,images)


def test_reviewer_selection_advice_adds_source_project_before_layout(project_master):
    job, folder = project_master
    original = (folder/'master.tex').read_bytes()
    class AdviceModels(MockModels):
        def call(self,role,instruction,data,schema,images=()):
            result = super().call(role,instruction,data,schema,images)
            if role=='judge':
                return result.model_copy(update={'selection_advice':flow.SelectionAdvice(
                    drop_bullet_ids=[],add_bullet_ids=['b7','b8'],reason='Relevant source-backed project')})
            return result
    runner = AdviceModels()
    pair = prepare(job,runner)
    manifest = json.loads((Path(pair['folder'])/'manifest.json').read_text())
    assert {'b7','b8'} <= set(manifest['selected_bullet_ids'])
    assert 'Example Project' in Path(pair['folder'],'resume.tex').read_text()
    assert runner.roles == ['tailor','judge','skills','final_review']
    assert (folder/'master.tex').read_bytes() == original


def test_underfilled_adds_project_and_final_review_sees_it(project_master):
    job,folder=project_master
    before=(folder/'master.tex').read_bytes()
    runner=AddProjectModels()
    def compiler(path):
        qa,images=fake_compiler(path)
        tex=(path/'resume.tex').read_text()
        full='Added SQL integration tests.' in tex
        qa.update(technical_passed=True,vertical_fill_ratio=.96 if full else .83,passed=full)
        qa['pages']=[{'text':tex}]
        return qa,images
    pair=prepare(job,runner,compiler)
    assert runner.roles==['tailor','judge','skills','layout','content_repair','final_review']
    assert 'Example Project' in runner.final_data['rendered_text']
    assert 'Added SQL integration tests.' in runner.final_data['rendered_text']
    manifest=json.loads((Path(pair['folder'])/'manifest.json').read_text())
    assert {'b7','b8'} <= set(manifest['selected_bullet_ids'])
    assert (folder/'master.tex').read_bytes()==before


def test_failed_fit_returns_specific_feedback_to_reviewer_until_it_passes(project_master):
    job,_ = project_master
    class IterativeModels(MockModels):
        def __init__(self):
            super().__init__()
            self.feedback = []
        def call(self,role,instruction,data,schema,images=()):
            if role == 'content_repair':
                self.roles.append(role)
                self.feedback.append(data['layout_feedback'])
                if not data['prior_repairs']:
                    return flow.Layout(drop_bullet_ids=[],add_bullet_ids=['b7'],
                                       preset='standard',reason='Add the project method')
                assert data['prior_repairs'][0]['outcome']['vertical_fill_ratio'] == .88
                assert any('b7' in trial['selected'] and
                           trial['feedback']['vertical_fill_ratio'] == .88
                           for trial in data['prior_repairs'][0]['trials'])
                return flow.Layout(drop_bullet_ids=[],add_bullet_ids=['b8'],
                                   preset='standard',reason='Add the source-backed test')
            return super().call(role,instruction,data,schema,images)
    runner = IterativeModels()
    def compiler(path):
        qa,images = fake_compiler(path)
        tex = (path/'resume.tex').read_text()
        ratio = .96 if 'Added SQL integration tests.' in tex else (.88 if 'Built a detailed Python data processing' in tex else .83)
        qa.update(technical_passed=True,vertical_fill_ratio=ratio,
                  underfilled=ratio<.94,passed=ratio>=.94)
        qa['pages']=[{'text':'Experience\nDeveloper\nPersonal Projects\nExample Project' if ratio>.83 else 'Experience\nDeveloper',
                      'vertical_fill_ratio':ratio,'bottom_gap_points':60}]
        return qa,images
    pair = prepare(job,runner,compiler)
    assert runner.roles == ['tailor','judge','skills','layout','content_repair','content_repair','final_review']
    assert runner.feedback[0]['issues'] == ['page ends before 94% of printable height']
    assert runner.feedback[1]['vertical_fill_ratio'] == .88
    assert 'Example Project' in runner.feedback[1]['pages'][0]['visible_entries']
    archive = Path(pair['folder'])
    assert (archive/'content-repair-01.json').exists()
    assert (archive/'content-repair-02.json').exists()
    assert (archive/'layout-decision-02.json').exists()


def test_overflowing_addition_skipped_without_another_model_call(project_master):
    job,_=project_master
    runner=AddProjectModels()
    calls=[]
    def compiler(path):
        qa,images=fake_compiler(path)
        tex=(path/'resume.tex').read_text()
        too_long='Built a detailed Python data processing' in tex
        full='Added SQL integration tests.' in tex
        calls.append(1)
        qa.update(technical_passed=not too_long,vertical_fill_ratio=.96 if full else .82,passed=full and not too_long)
        return qa,images
    pair=prepare(job,runner,compiler)
    final_tex=Path(pair['folder'],'resume.tex').read_text()
    assert 'Added SQL integration tests.' in final_tex and 'Built a detailed Python data processing' not in final_tex
    assert len(calls)==5 and len(runner.roles)==6


def test_underfilled_still_blocks_if_additions_do_not_fill(project_master):
    job,_=project_master
    def compiler(path):
        qa,images=fake_compiler(path)
        qa.update(technical_passed=True,vertical_fill_ratio=.80,passed=False)
        return qa,images
    runner=AddProjectModels()
    with pytest.raises(ValueError,match='repeated a repair'):
        prepare(job,runner,compiler)
    assert 'final_review' not in runner.roles


@pytest.mark.parametrize('additions',[['invented'],['b2'],['b7','b7']])
def test_additions_must_come_from_omitted_master(project_master,additions):
    _,folder=project_master
    text,_=source.load_master()
    with pytest.raises(ValueError,match='distinct omitted'):
        flow.apply_layout_plan(text,source.source_context(text),['b1','b2','b3','b4','b5'],{},
            flow.Layout(drop_bullet_ids=[],add_bullet_ids=additions,preset='standard',reason='test'),folder,fake_compiler,
            {'passed':False,'technical_passed':True,'vertical_fill_ratio':.8},[])


def test_layout_quality_requires_fullness_and_safe_fit():
    from backend.latex_render import assess_layout
    qa={'page_count':1,'bounds_ok':True,'pages':[{'vertical_fill_ratio':.84}]}
    assert not assess_layout(qa,0)['passed'] and qa['underfilled']
    qa['pages'][0]['vertical_fill_ratio']=.96
    assert assess_layout(qa,0)['passed']
    assert not assess_layout(qa,1)['passed']
    qa['pages'][0]['vertical_fill_ratio']=1.1
    assert not assess_layout(qa,0)['passed']
    qa['page_count']=2
    assert not assess_layout(qa,0)['passed']


def test_layout_feedback_identifies_overflow_page_entry(project_master):
    _, folder = project_master
    context = source.source_context((folder/'master.tex').read_text())
    qa = {'passed':False,'page_count':2,'bounds_ok':False,'overfull_boxes':1,
          'vertical_fill_ratio':1.01,'pages':[
              {'text':'Experience\nDeveloper','vertical_fill_ratio':1.01},
              {'text':'Personal Projects\nExample Project\nAdded SQL integration tests.',
               'vertical_fill_ratio':.12,'clipped_characters':2,'overlapping_words':1}]}
    feedback = flow.layout_feedback(qa,context,['b2','b3','b4','b5','b6','b7','b8'])
    assert 'content spills onto another page' in feedback['issues']
    assert feedback['pages'][1]['visible_entries'] == ['Example Project']
    assert feedback['pages'][1]['first_lines'][1] == 'Example Project'
    assert feedback['pages'][1]['clipped_characters'] == 2


@pytest.fixture
def team_master(project_master):
    job,folder=project_master
    teams=r'''\section{Engineering Design Teams}
\resumeSubHeadingListStart
\resumeSubheading{Motorsports}{Ongoing}{Example University}{City}
\resumeItemListStart
\resumeItem{Built embedded telemetry in C.}
\resumeItemListEnd
\resumeSubheading{Rocketry}{Ongoing}{Example University}{City}
\resumeItemListStart
\resumeItem{Developed flight computer software.}
\resumeItemListEnd
\resumeSubHeadingListEnd
'''
    path=folder/'master.tex'
    path.write_text(path.read_text().replace(r'\section{Personal Projects}',teams+r'\section{Personal Projects}'))
    metadata=json.loads((folder/'source.json').read_text())
    metadata['sha256']=source.sha(path)
    (folder/'source.json').write_text(json.dumps(metadata))
    return job,folder


def test_role_relevance_allows_omitting_design_teams(team_master):
    _, folder = team_master
    context = source.source_context((folder/'master.tex').read_text())
    selected = ['b1','b2','b3','b4','b5','b6','b9']
    flow.validate_section_priority(context, selected, final=True)
    summary = flow.resume_selection_summary(context, selected)
    assert summary['design_teams'] == []
    assert summary['omitted_design_teams'] == ['Motorsports', 'Rocketry']
    assert summary['projects'] == ['Example Project']


def test_summary_names_only_selected_team_and_project(team_master):
    _, folder = team_master
    context = source.source_context((folder/'master.tex').read_text())
    selected = ['b1','b2','b3','b4','b5','b6','b8','b9']
    flow.validate_section_priority(context, selected, final=True)
    summary = flow.resume_selection_summary(context, selected)
    assert summary['design_teams'] == ['Rocketry']
    assert summary['omitted_design_teams'] == ['Motorsports']
    assert summary['projects'] == ['Example Project']


def test_personal_projects_limited_to_one_or_two():
    context={'entries':[{'id':f'e{i}','section':'Personal Projects','bullets':[f'b{i}']} for i in range(1,4)]}
    flow.validate_section_priority(context,['b1'],final=True)
    flow.validate_section_priority(context,['b1','b2'],final=True)
    with pytest.raises(ValueError,match='one or two'):
        flow.validate_section_priority(context,['b1','b2','b3'])
    with pytest.raises(ValueError,match='one or two'):
        flow.validate_section_priority(context,[],final=True)


def test_three_project_selection_drops_only_rocket_duplicate():
    context = {'entries': [
        {'section':'Engineering Design Teams','context':'Arbalest Rocketry','bullets':['b3']},
        {'section':'Personal Projects','context':'Body tracking','bullets':['b4']},
        {'section':'Personal Projects','context':'Real-Time Rocket Tracking System -- Arbalest Rocketry','bullets':['b5']},
        {'section':'Personal Projects','context':'Chatbot','bullets':['b6']},
    ]}
    proposal = flow.Tailoring(selected_bullet_ids=['b1','b2','b3','b4','b5','b6'],
                              edits=[flow.Edit(bullet_id='b5', alternatives=['A','B','C'])])
    repaired, record = flow.prune_duplicate_rocket_project(context, proposal)
    assert repaired.selected_bullet_ids == ['b1','b2','b3','b4','b6']
    assert repaired.edits == []
    assert record['dropped_bullet_ids'] == ['b5']
    assert proposal.selected_bullet_ids[-2:] == ['b5','b6']


def test_new_metrics_require_explicit_source_evidence():
    candidates=[{'bullet_id':'b1','candidate_id':'b1-0','text':'Built a document review platform.'},
                {'bullet_id':'b1','candidate_id':'b1-1','text':'Built a document review platform used across 2–3 plants.'},
                {'bullet_id':'b1','candidate_id':'b1-2','text':'Built a document review platform used across 500 plants.'}]
    judgments=flow.Judgment(selection_advice=flow.SelectionAdvice(drop_bullet_ids=[],add_bullet_ids=[],reason='MOCKED no change'), evaluations=[flow.Score(candidate_id=c['candidate_id'],support='SUPPORTED',
        relevance=60+i*15,clarity=60+i*15,specificity=60+i*15,concision=60+i*15,reason='MOCKED') for i,c in enumerate(candidates)])
    assert flow.choose(candidates,judgments)[0]=={}
    replacements,_=flow.choose(candidates,judgments,{'b1':'User confirmed early rollout across 2–3 plants.'})
    assert replacements['b1']==candidates[1]['text']
    assert flow.choose(candidates,judgments,{'unrelated':'2–3 plants'})[0]=={}


def test_review_contract_change_creates_new_run_without_overwriting_failure(setup_master, monkeypatch):
    job, _ = setup_master
    with pytest.raises(ValueError, match='Independent final review did not pass'):
        prepare(job, MockModels(final_ok=False))
    with connect() as c:
        failed = dict(c.execute('SELECT * FROM latex_runs WHERE job_id=?', (job,)).fetchone())
    failed_review = Path(failed['folder'], 'final-review.json').read_bytes()
    with pytest.raises(ValueError, match='Previous preparation failed'):
        prepare(job)
    contract = flow.Judgment.model_json_schema()
    contract['title'] = 'ChangedReviewContract'
    monkeypatch.setattr(flow.Judgment, 'model_json_schema', classmethod(lambda cls: contract))
    pair = prepare(job)
    assert pair['run_id'] != failed['id']
    with connect() as c:
        preserved = dict(c.execute('SELECT * FROM latex_runs WHERE id=?', (failed['id'],)).fetchone())
    assert preserved == failed
    assert Path(failed['folder'], 'final-review.json').read_bytes() == failed_review
