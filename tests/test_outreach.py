from pathlib import Path

import pytest

from backend import outreach, tool
from backend.postings import save_snapshot


def test_outreach_draft_evidence_versions_dedup_and_manual_history(tmp_path, monkeypatch):
    tool.initialize()
    monkeypatch.setattr(outreach, 'ROOT', tmp_path)
    monkeypatch.setattr(outreach, 'load_master', lambda: ('synthetic', {'sha256': 'a' * 64, 'identity': 'Synthetic Student'}))
    monkeypatch.setattr(outreach, 'source_context', lambda source: {'entries': [], 'bullets': [{'id': 'b1', 'entry_id': 'e1', 'text': 'Built a Python application.'}]})
    job = tool.add_application_link('https://example.com/jobs/intern', company='Synthetic Company', title='Intern')['job_id']
    with pytest.raises(ValueError, match='job description'):
        outreach.get_outreach_context(job)
    snapshot = save_snapshot(job, 'Synthetic internship for software engineering students.', 'SYNTHETIC')
    draft = dict(job_id=job, snapshot_id=snapshot, master_sha256='a' * 64,
                 contact_name='Synthetic Recruiter', contact_role='Recruiter',
                 contact_url='https://example.com/team/recruiter', contact_evidence='Synthetic public team page fixture.',
                 subject='Software internship', email_body='I built a Python application. Could you point me to the internship team?',
                 linkedin_message='I am interested in your software internship. May I ask about the team?', source_bullet_ids=['b1'])
    with pytest.raises(ValueError, match='exact email'):
        outreach.save_outreach_draft(**draft, email='recruiter@example.com')
    with pytest.raises(ValueError, match='bullet IDs'):
        outreach.save_outreach_draft(**(draft | {'source_bullet_ids': ['invented']}))
    with pytest.raises(ValueError, match='changed'):
        outreach.save_outreach_draft(**(draft | {'master_sha256': 'b' * 64}))
    saved = tool.dispatch({'tool': 'save_outreach_draft', 'arguments': draft})
    assert saved['state'] == 'DRAFT' and saved['sent'] is False
    assert outreach.save_outreach_draft(**draft)['outreach_id'] == saved['outreach_id']
    assert len(outreach.list_outreach()) == 1
    assert 'Not sent' in Path(saved['artifact']).read_text(encoding='utf-8')
    with pytest.raises(ValueError, match='confirmation'):
        outreach.record_outreach_result(saved['outreach_id'], 'SENT', '')
    outreach.record_outreach_result(saved['outreach_id'], 'SENT', 'Synthetic user confirmation of manual send.')
    with pytest.raises(ValueError, match='history'):
        outreach.save_outreach_draft(**draft)
    outreach.record_outreach_result(saved['outreach_id'], 'REPLIED', 'Synthetic received reply.')
    with pytest.raises(ValueError, match='backwards'):
        outreach.record_outreach_result(saved['outreach_id'], 'SENT', 'Old receipt')
