"""Application identity and durable history, shared by the CLI and packet workflow."""
import time

from .db import audit, connect, digest
from .discovery import normalize_url


BLOCKED_STATES = frozenset({'APPLIED', 'SUBMISSION_UNKNOWN', 'IN_PROGRESS', 'SKIPPED'})
STATE_PRIORITY = {'APPLIED': 5, 'SUBMISSION_UNKNOWN': 4, 'IN_PROGRESS': 3,
                  'SKIPPED': 2, 'NOT_APPLIED': 1}


def init_history():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS application_history(url TEXT PRIMARY KEY, state TEXT NOT NULL, evidence TEXT NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS url_aliases(alias TEXT PRIMARY KEY, canonical TEXT NOT NULL, evidence TEXT NOT NULL);
        ''')


def canonical(c, url):
    current = normalize_url(url)
    seen = set()
    while True:
        if current in seen:
            raise ValueError('Application URL alias cycle')
        seen.add(current)
        row = c.execute('SELECT canonical FROM url_aliases WHERE alias=?', (current,)).fetchone()
        if not row:
            return current
        current = row[0]


def history_index(c):
    """Read history once per scan, preserving the strictest record across aliases."""
    result = {}
    for row in c.execute('SELECT * FROM application_history'):
        record = dict(row)
        target = canonical(c, record['url'])
        previous = result.get(target)
        if previous is None or (STATE_PRIORITY[record['state']], record['updated']) > (
                STATE_PRIORITY[previous['state']], previous['updated']):
            result[target] = record
    return result


def history_for(c, url):
    return history_index(c).get(canonical(c, url))


def link_urls(alias, destination, evidence):
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError('Record the observed redirect/final posting evidence')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        left, right = canonical(c, alias), canonical(c, destination)
        if left != right:
            c.execute('INSERT INTO url_aliases VALUES (?,?,?)', (left, right, evidence))
        audit(c, 'APPLICATION_URL_LINKED', digest([left, right]))
    return {'canonical_url': right}


def record_application_in_transaction(c, url, state, evidence):
    """Caller owns the write transaction so packet and history updates stay atomic."""
    if not isinstance(state, str) or state not in STATE_PRIORITY or not isinstance(evidence, str) or not evidence.strip():
        raise ValueError('Provide a valid state and observed receipt or user-confirmed evidence')
    target = canonical(c, url)
    old = history_for(c, target)
    if old and ((old['state'] == 'APPLIED' and state != 'APPLIED') or (
            old['state'] == 'SUBMISSION_UNKNOWN' and state not in {'APPLIED', 'SUBMISSION_UNKNOWN'})):
        raise ValueError('Applied/unknown outcomes cannot be reset automatically; reconcile with the employer first')
    if old and old['state'] == 'IN_PROGRESS' and state == 'IN_PROGRESS':
        raise ValueError('Another application attempt is already in progress; take over the existing attempt explicitly')
    c.execute('INSERT INTO application_history VALUES (?,?,?,?) ON CONFLICT(url) DO UPDATE SET state=excluded.state,evidence=excluded.evidence,updated=excluded.updated',
              (target, state, evidence, time.time()))
    audit(c, 'APPLICATION_' + state, digest(target))
    return {'url': target, 'state': state, 'note': 'History record only; this tool did not submit an application.'}


def record_application(url, state, evidence):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        return record_application_in_transaction(c, url, state, evidence)


def import_applied_links(urls, evidence):
    if not isinstance(urls, list) or not 1 <= len(urls) <= 500 or any(not isinstance(url, str) for url in urls):
        raise ValueError('Import 1 to 500 user-confirmed application links at a time')
    normalized = [normalize_url(url) for url in urls]
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        return {'imported': [record_application_in_transaction(c, url, 'APPLIED', evidence)
                             for url in normalized]}
