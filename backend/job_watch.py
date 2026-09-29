"""Hourly public-repository discovery; no models, browser control, or applications."""
import json
import re
import time
from pathlib import Path

from dotenv import load_dotenv

from .application_history import BLOCKED_STATES, canonical, history_index
from .db import ROOT, connect, digest, packed
from .discovery import SOURCES, refresh
from .tool import initialize


def priority(job):
    location = job['location'] or ''
    # ponytail: conservative location hints; verify the official JD before tailoring.
    canada = bool(re.search(r'\bCanada\b|🇨🇦|,\s*(?:ON|QC|BC|AB|MB|SK|NS|NB|NL|PE|NT|NU|YT)\b|\b(?:Toronto|Ottawa|Montréal|Montreal|Calgary)\b', location))
    if canada:
        return 0, 'Canada location listed; verify term and permit eligibility in official JD'
    title = job['title'] or ''
    if '🛂' in title or '🇺🇸' in title or re.search(r'no (?:visa )?sponsorship|US citizenship required', title, re.I):
        return 3, 'Repository explicitly indicates unavailable sponsorship or US citizenship requirement'
    return 2, 'Immigration support unknown; check official JD before recommending'


def grouped_jobs(c):
    groups = {}
    for row in c.execute('SELECT j.*, group_concat(s.source_id) AS sources FROM jobs j JOIN source_jobs s ON s.job_id=j.id GROUP BY j.id ORDER BY j.updated DESC,j.id'):
        job = dict(row)
        if not set(job['sources'].split(',')) & SOURCES.keys():
            continue
        key = canonical(c, job['url'])
        group = groups.setdefault(key, {'job': job, 'hashes': [], 'sources': set(), 'skipped': False})
        group['hashes'].append(job['material_hash'])
        group['sources'].update(job['sources'].split(','))
        group['skipped'] |= job['state'] == 'SKIPPED'
    return groups


def check_jobs():
    initialize()
    from .job_tracker import sync_job_tracker
    sync_job_tracker()  # Respect user-edited application statuses before alerting.
    with connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS job_watch_seen(url TEXT PRIMARY KEY, hash TEXT NOT NULL)')
        c.execute('BEGIN IMMEDIATE')
        if not c.execute("SELECT 1 FROM settings WHERE key='job-watch-initialized'").fetchone():
            for url, group in grouped_jobs(c).items():
                c.execute('INSERT OR REPLACE INTO job_watch_seen VALUES (?,?)', (url, digest(sorted(group['hashes']))))
            c.execute('INSERT INTO settings VALUES (?,?)', ('job-watch-initialized', 'true'))
    sources = refresh()
    fresh = {s['source'] for s in sources if 'error' not in s}
    errors = {s['source']: s['error'] for s in sources if 'error' in s}
    jobs = []
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        histories = history_index(c)
        for url, group in grouped_jobs(c).items():
            if not group['sources'] & fresh:
                continue  # Failed fetches never establish new evidence or advance checkpoints.
            fingerprint = digest(sorted(group['hashes']))
            old = c.execute('SELECT hash FROM job_watch_seen WHERE url=?', (url,)).fetchone()
            c.execute('INSERT OR REPLACE INTO job_watch_seen VALUES (?,?)', (url, fingerprint))
            if old and old[0] == fingerprint:
                continue
            job = group['job']
            if group['skipped'] or histories.get(url, {}).get('state') in BLOCKED_STATES:
                continue
            rank, reason = priority(job)
            if rank == 3:
                continue
            jobs.append({k: job[k] for k in ('id', 'company', 'title', 'location', 'url')} | {
                'canonical_url': url, 'change': 'changed' if old else 'new',
                'priority': rank, 'reason': reason,
            })
        old_errors = c.execute("SELECT value FROM settings WHERE key='job-watch-errors'").fetchone()
        previous = json.loads(old_errors[0]) if old_errors else {}
        c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('job-watch-errors', packed(errors)))
    jobs.sort(key=lambda j: (j['priority'], j['company'], j['title']))
    return {'checked_at': time.time(), 'sources': sources, 'jobs': jobs,
            'job_tracker': sync_job_tracker(),
            'notification_needed': bool(jobs or errors != previous),
            'new_errors': {k: v for k, v in errors.items() if previous.get(k) != v},
            'recovered_sources': sorted(set(previous) - set(errors)),
            'note': 'New to this watcher, not necessarily posted today. Sponsorship is unknown until verified in the official JD.'}


def main():
    load_dotenv(ROOT / '.env')
    result = check_jobs()
    folder = ROOT / 'data' / 'job-watch'
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / 'latest.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if result['notification_needed']:
        (folder / f"{time.time_ns()}.json").write_text(output.read_text(encoding='utf-8'), encoding='utf-8')
    print(json.dumps({'report': str(output), 'new_or_changed': len(result['jobs']),
                      'notification_needed': result['notification_needed'],
                      'source_errors': len([s for s in result['sources'] if 'error' in s])}))
    return 0 if any('error' not in s for s in result['sources']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
