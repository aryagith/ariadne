from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import auto_tailor as tailoring
from backend import subscription_models
from backend.application_history import record_application
from backend.db import connect
from backend.postings import save_snapshot
from test_latex_workflow import setup_master


class Models:
    def __init__(self, ready=True):
        self.roles = []
        self.ready = ready

    def call(self, role, instruction, data, schema, images=()):
        self.roles.append(role)
        if role == 'final_review':
            assert images and data['draft'] and data['pdf_text']
            assert 'master_tex' not in data
            assert 'layout_feedback' not in data and data['current_layout']['vertical_fill_ratio'] == .96
            assert data['verified_skills']
            assert data['draft']['interview_notes']
            return schema(ready=self.ready, issues=[] if self.ready else ['Unsupported outcome'])
        return schema(selected_bullet_ids=['b2', 'b3', 'b4'], edits=[],
                      ranked_skills=data['skills'], layout='standard', skill_lines=3,
                      selection_reason='Synthetic test selection',
                      interview_notes='## Metric evidence\n- b2: 20 endpoints. Measurement method needs confirmation.')


def compiler(folder):
    (folder / 'resume.pdf').write_bytes(b'SYNTHETIC TEST PDF')
    image = folder / 'page-1.png'
    image.write_bytes(b'SYNTHETIC TEST IMAGE')
    return {'passed': True, 'page_count': 1, 'vertical_fill_ratio': .96,
            'bounds_ok': True, 'pages': [{'text': 'Synthetic test resume', 'vertical_fill_ratio': .96}]}, [image]


def prepare(setup_master, monkeypatch):
    job_id, master = setup_master
    monkeypatch.setattr(tailoring, 'ARCHIVE', master.parent / 'job apps')
    with connect() as c:
        snapshot = c.execute('SELECT id FROM snapshots WHERE job_id=?', (job_id,)).fetchone()[0]
    return {'job_id': job_id, 'snapshot_id': snapshot,
            'verification': 'Synthetic official posting verified for this test only.'}


def test_pins_sol_high_and_subscription_auth(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(subscription_models.shutil, 'which', lambda _: 'codex.exe')
    def run(command, **kwargs):
        commands.append(command)
        assert 'OPENAI_API_KEY' not in kwargs['env']
        assert Path(kwargs['env']['CODEX_HOME']).is_absolute()
        Path(command[command.index('-o') + 1]).write_text('{"ready":true,"issues":[]}')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(subscription_models.subprocess, 'run', run)
    models = subscription_models.SubscriptionModels(tmp_path, router=tailoring.SolOnlyRouter())
    models.call('final_review', 'Synthetic test', {}, tailoring.SelfCheck)
    assert commands[0][commands[0].index('-m') + 1] == 'gpt-6-sol'
    assert 'model_reasoning_effort="high"' in commands[0]
    assert 'forced_login_method="chatgpt"' in commands[0]
    for schema in (tailoring.Draft, tailoring.SelfCheck):
        subscription_models.validate_output_schema(schema.model_json_schema())


def test_packet_cache_and_changed_file_detection(setup_master, monkeypatch):
    args = prepare(setup_master, monkeypatch)
    models = Models()
    result = tailoring.tailor_alert_resume(**args, _runner=models, _compiler=compiler)
    assert result['state'] == 'MOCKED'
    assert models.roles == ['tailor', 'final_review']
    assert all(Path(p).exists() for p in (result['pdf'], result['source']))
    notes = Path(result['interview_notes']).read_text(encoding='utf-8')
    assert 'b2: 20 endpoints' in notes and args['snapshot_id'] in notes
    assert 'https://example.com/jobs/123' in notes
    assert tailoring.tailor_alert_resume(**args, _runner=models, _compiler=compiler) == result
    assert len(models.roles) == 2
    Path(result['source']).write_text('User edited draft')
    with pytest.raises(ValueError, match='Saved resume changed'):
        tailoring.tailor_alert_resume(**args, _runner=models, _compiler=compiler)


@pytest.mark.parametrize('failure', ['underfill', 'self-check'])
def test_failed_quality_never_ready_or_repeated(setup_master, monkeypatch, failure):
    args = prepare(setup_master, monkeypatch)
    models = Models(ready=failure != 'self-check')
    def render(folder):
        qa, images = compiler(folder)
        if failure == 'underfill':
            qa.update(passed=False, underfilled=True, vertical_fill_ratio=.8)
        return qa, images
    result = tailoring.tailor_alert_resume(**args, _runner=models, _compiler=render)
    assert result['state'] == 'NEEDS_ATTENTION' and 'pdf' not in result
    assert models.roles.count('revise') == 3
    with pytest.raises(ValueError, match='already attempted'):
        tailoring.tailor_alert_resume(**args, _runner=models, _compiler=render)


def test_history_and_stale_jd_block_before_model(setup_master, monkeypatch):
    args = prepare(setup_master, monkeypatch)
    models = Models()
    save_snapshot(args['job_id'], 'A materially changed official JD', 'TEST')
    with pytest.raises(ValueError, match='latest saved'):
        tailoring.tailor_alert_resume(**args, _runner=models, _compiler=compiler)
    record_application('https://example.com/jobs/123', 'APPLIED', 'User confirmed manual submission')
    with pytest.raises(ValueError, match='history blocks'):
        tailoring.tailor_alert_resume(**args, _runner=models, _compiler=compiler)
    assert not models.roles
