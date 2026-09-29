import json
from datetime import datetime
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from backend import job_tracker as tracker, tool
from backend.application_history import history_for
from backend.db import connect


def test_discovered_toronto_time():
    for utc, expected in [
        ('2026-09-28T22:27:00+00:00', 'Sep 28, 2026, 6:27 PM EDT'),
        ('2026-01-15T05:05:00+00:00', 'Jan 15, 2026, 12:05 AM EST'),
        ('2026-01-15T02:05:00+00:00', 'Jan 14, 2026, 9:05 PM EST'),
    ]:
        assert tracker.toronto_time(datetime.fromisoformat(utc).timestamp()) == expected


def test_tracker_manual_edits_and_exact_submission_copies(tmp_path):
    tool.initialize()
    job = tool.add_application_link('https://example.com/jobs/123', company='Example', title='Software Intern', location='Toronto, ON')['job_id']
    result = tracker.sync_job_tracker()
    assert result['jobs'] == 1 and not result['errors']
    folder = next(p for p in tracker.ARCHIVE.iterdir() if p.is_dir())
    status = folder / 'STATUS.txt'
    assert 'Posted: UNKNOWN' in status.read_text()
    original = status.read_text()
    status.write_text(original.replace('DISCOVERED', 'SHORTLISTED').replace('Notes:\n', 'Notes:\nCheck four-month availability.\n'))
    assert not tracker.sync_job_tracker()['errors']
    assert 'SHORTLISTED' in (folder / 'JOB.md').read_text()
    assert 'Check four-month' in Path(result['index']).read_text()
    with pytest.raises(ValueError, match='source/evidence'):
        tracker.update_job_tracking(job, posted='2026-09-28')
    tracker.update_job_tracking(job, posted='2026-09-28', posted_source='Official posting date field')
    assert 'Posted: 2026-09-28\n' in status.read_text()
    document = tmp_path / 'resume.pdf'
    document.write_bytes(b'Synthetic PDF fixture: exact original bytes')
    with pytest.raises(ValueError, match='confirmation'):
        tracker.archive_submitted_documents(job, [str(document)], '')
    archive = tracker.archive_submitted_documents(job, [str(document)], 'Synthetic user confirmation of manual submission')
    copied = Path(archive['folder']) / '01-resume.pdf'
    document.write_bytes(b'Updated working resume')
    assert copied.read_bytes() == b'Synthetic PDF fixture: exact original bytes'
    manifest = json.loads((copied.parent / 'manifest.json').read_text())
    assert manifest['submitted_at'] is None
    with connect() as c:
        assert history_for(c, 'https://example.com/jobs/123')['state'] == 'APPLIED'
    assert 'Status: APPLIED' in status.read_text()
    status.write_text(status.read_text().replace('APPLIED', 'DISCOVERED'))
    assert tracker.sync_job_tracker()['errors']
    assert 'Status: DISCOVERED' in status.read_text()  # Invalid edits are preserved for correction.
    assert 'Status: APPLIED' in (folder / 'JOB.md').read_text()
    tracker.update_job_tracking(job, status='INTERVIEW')
    assert 'Status: INTERVIEW' in status.read_text()
    assert tracker.sync_job_tracker()['errors'] == []


def test_existing_version_folder_and_html_escape(tmp_path):
    tool.initialize()
    job = tool.add_application_link('https://example.com/jobs/456', company='<script>bad</script>', title='Intern')['job_id']
    old = tracker.ARCHIVE / ('Existing-Name-' + job[:8]) / 'old-version'
    old.mkdir(parents=True)
    (old / 'resume.pdf').write_bytes(b'Existing untouched draft')
    tracker.sync_job_tracker()
    assert (old.parent / 'STATUS.txt').exists()
    assert (old / 'resume.pdf').read_bytes() == b'Existing untouched draft'
    index = (tracker.ARCHIVE / 'APPLICATIONS.html').read_text()
    assert '&lt;script&gt;bad&lt;/script&gt;' in index
    assert '<script>bad</script>' not in index
    page = BeautifulSoup(index, 'html.parser')
    assert page.select_one('button[data-folder]')['data-folder'] == old.parent.name
    assert {o.text for o in page.select('#status option')} == tracker.STATUSES
    assert page.select_one('#posted') and page.select_one('#posted-source')
    assert page.select_one('textarea#notes')['maxlength'] == '20000'
    assert 'async function saveStatus' in page.script.text
