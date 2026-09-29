from backend.resume_skills import catalog, contains, plan_skills, apply_skills
from backend.latex_source import render_source
from test_latex_workflow import TEX

SOURCE=TEX.replace('Python, SQL, React',r'''\textbf{Programming:} Python, TypeScript, SQL (Postgres, SQLite), C, C\# \\
\textbf{Software Engineering \& Methodologies:} Git, REST APIs, leadership \\
\textbf{Data Science \& AI:} PyTorch, pandas, NumPy, ChatGPT \\
\textbf{Tools \& Soft Skills:} JIRA, problem solving, time management''')

def terms(plan):
    return [s for g in plan['groups'] for s in g['skills']]

def test_catalog_expands_only_existing_skills_and_drops_filler():
    skills={s['term'] for s in catalog(SOURCE)}
    assert {'SQL','Postgres','SQLite','C#','TypeScript'}<=skills
    assert not {'leadership','ChatGPT','problem solving','JIRA'}&skills

def test_job_keywords_remain_source_backed_and_keep_explicit_matches():
    plan=plan_skills(SOURCE,['b2','b3','b4','b5','b6'],job_description='Python SQL TypeScript Kubernetes PostgreSQL REST API')
    assert terms(plan)[:5]==['Python','TypeScript','SQL','Postgres','REST APIs']
    assert 'SQLite' in terms(plan)
    assert 'Python' in terms(plan)
    assert 'Kubernetes' not in terms(plan)

def test_skill_matching_does_not_confuse_language_names():
    assert not contains('JavaScript','Java')
    assert not contains('C# C++ CI/CD','C')
    assert contains('C, Python','C')
    assert contains('PostgreSQL','Postgres')

def test_no_exact_match_uses_related_supported_skills_and_general_mode_is_compact():
    plan=plan_skills(SOURCE,['b2'],job_description='Kubernetes only')
    assert terms(plan)
    assert 'Kubernetes' not in terms(plan)
    assert r'\section{Skills}' in apply_skills(SOURCE,plan)
    general=plan_skills(SOURCE,['b2'])
    assert len(terms(general))>0 and len(general['groups'])<=3

def test_layout_additions_retain_explicit_jd_skills():
    jd='SQL TypeScript'
    before=render_source(SOURCE,['b2'],focus_skills=True,job_description=jd)
    after=render_source(SOURCE,['b2','b4'],focus_skills=True,job_description=jd)
    assert 'SQL' in before.split(r'\section{Skills}')[1]
    assert contains(after.split(r'\section{Skills}')[1],'SQL')
    assert 'TypeScript' in after.split(r'\section{Skills}')[1]

def test_selected_replacement_and_project_heading_count_as_coverage():
    plan=plan_skills(SOURCE,['b2'],{'b2':'Built a TypeScript frontend.'},'Python TypeScript')
    assert terms(plan)[0]=='Python'
    assert 'TypeScript' in terms(plan)
    assert 'TypeScript' not in terms(plan_skills(SOURCE,['b2'],{'b2':'Built a TypeScript frontend.'},'Python'))


def test_related_skills_follow_exact_matches_without_inventing_jd_skills():
    plan=plan_skills(SOURCE,['b2'],job_description='TypeScript backend database software engineering')
    selected=terms(plan)
    assert selected[0]=='TypeScript'
    assert selected.index('SQL')<selected.index('PyTorch')
    assert selected.index('Postgres')<selected.index('PyTorch')
    assert 'Python' in selected  # A JD-relevant skill remains useful in Skills as well as the body.


def test_bolding_is_consistent_for_original_and_rewritten_bullets():
    from backend.latex_source import index_master, plain, regular_weight
    rendered=render_source(SOURCE,['b1','b2','b3'],{'b3':'Wrote automated tests using PyTest.'})
    _,bullets=index_master(rendered)
    for bullet in bullets:
        assert r'\textbf' not in rendered[bullet['arg_start']:bullet['arg_end']]
    original=r'Used \textbf{Azure \emph{Blob}} and 20\%.'
    assert plain(regular_weight(original))==plain(original)

def test_packing_uses_rendered_skills_instead_of_candidate_inventory():
    from backend.resume_skills import realized_plan
    plan=plan_skills(SOURCE,['b2'])
    realized=realized_plan(plan,{'rendered_skills':['TypeScript','SQL'],'skill_line_budget':1})
    assert terms(realized)==['TypeScript','SQL']
    assert realized['line_budget']==1
    tex=apply_skills(SOURCE,plan)
    assert r'\ifdim\wd\AutoapplySkillTry>\linewidth' in tex

def test_project_technology_requires_explicit_master_evidence():
    assert 'TensorRT' not in {s['term'] for s in catalog(SOURCE)}
    enriched=SOURCE.replace('Wrote automated tests using PyTest.','Optimized inference using TensorRT.')
    item=next(s for s in catalog(enriched) if s['term']=='TensorRT')
    assert item['source_evidence']=='Optimized inference using TensorRT.'
    assert 'TensorRT' in terms(plan_skills(enriched,['b2'],job_description='TensorRT deployment'))
    assert 'TensorRT' in terms(plan_skills(enriched,['b2','b3'],job_description='TensorRT deployment'))
    assert 'TensorRT' not in terms(plan_skills(enriched,['b2','b3'],job_description='deployment'))


def test_omitted_project_stack_is_supported_skill_evidence():
    project=r'''\section{Personal Projects}
\resumeProjectHeading{\textbf{Example App} $|$ \emph{NextAuth, Supabase, Prisma}}{}
\resumeItemListStart
\resumeItem{Built an application.}
\resumeItemListEnd
'''
    enriched=SOURCE.replace(r'\section{Skills}',project+r'\section{Skills}')
    plan=plan_skills(enriched,['b2'],job_description='Backend databases')
    assert {'NextAuth','Supabase','Prisma'}<=set(terms(plan))
    assert 'Example App' not in terms(plan)
    rendered=render_source(enriched,['b1','b2','b7'])
    assert r'\textbf{Example App}' in rendered
    assert r'\emph{NextAuth' not in rendered

def test_overflow_reduces_skill_rows_before_changing_experience(tmp_path,monkeypatch):
    from backend import latex_render
    tex=r'Experience remains identical.\def\AutoapplySkillLines{3} Skills'
    path=tmp_path/'resume.tex'
    path.write_text(tex)
    budgets=[]
    def compile_one(folder):
        current=path.read_text()
        assert current.replace('{2}','{3}')==tex
        budgets.append(3 if '{3}' in current else 2)
        return {'technical_passed':budgets[-1]==2},[]
    monkeypatch.setattr(latex_render,'_compile_resume',compile_one)
    qa,_=latex_render.compile_resume(tmp_path)
    assert qa['technical_passed'] and budgets==[3,2]
