from backend import job_watch, tool
from backend.discovery import import_rows


def add(number, location='Toronto, ON, Canada', title='Software Engineering Intern'):
    import_rows('simplify-summer', f'<tr><td>Acme</td><td>{title}</td><td>{location}</td><td><a href="https://example.com/jobs/{number}">Apply</a></td><td>0d</td></tr>')


def test_watch_baseline_dedup_manual_scan_failure_and_priority(monkeypatch):
    tool.initialize()
    add(1)
    monkeypatch.setattr(job_watch, 'refresh', lambda: [{'source': 'simplify-summer', 'unchanged': True}])
    assert job_watch.check_jobs()['jobs'] == []  # Do not flood with existing backlog.
    add(2)
    add(3, 'San Francisco, CA')
    add(4, 'New York, NY', 'Software Intern 🛂')
    monkeypatch.setattr(tool, 'refresh', lambda **kw: [])
    tool.scan_jobs()  # Manual scans must not consume hourly alerts.
    result = job_watch.check_jobs()
    assert [j['priority'] for j in result['jobs']] == [0, 2]
    assert not job_watch.check_jobs()['notification_needed']
    add(5)
    monkeypatch.setattr(job_watch, 'refresh', lambda: [{'source': 'simplify-summer', 'error': 'offline'}])
    assert job_watch.check_jobs()['new_errors'] == {'simplify-summer': 'offline'}
    assert not job_watch.check_jobs()['notification_needed']
    monkeypatch.setattr(job_watch, 'refresh', lambda: [{'source': 'simplify-summer', 'unchanged': True}])
    assert len(job_watch.check_jobs()['jobs']) == 1  # Failure did not lose new jobs.
    assert job_watch.priority({'location': 'Vancouver, WA', 'title': 'Intern'})[0] == 2
