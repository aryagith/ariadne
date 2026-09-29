import re
import time
from urllib.parse import urlsplit
import httpx
from bs4 import BeautifulSoup
from .db import connect, digest, get_row, uid


def save_snapshot(job_id, text, provenance):
    job = get_row('jobs', job_id)
    h = digest({'text': text, 'provenance': provenance, 'url': job['url']})
    with connect() as c:
        old = c.execute('SELECT id,hash FROM snapshots WHERE job_id=? ORDER BY created DESC LIMIT 1', (job_id,)).fetchone()
        if old and old['hash'] == h:
            return old['id']
        identity = uid()
        c.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)', (identity, job_id, h, text, provenance, job['url'], time.time()))
        return identity


def fetch_posting(job_id):
    job = get_row('jobs', job_id)
    p = urlsplit(job['url'])
    # One read-only adapter. Fixed hostname and strict path; no redirects, cookies or user data.
    if p.hostname != 'job-boards.greenhouse.io' or not re.fullmatch(r'/[A-Za-z0-9_-]+/jobs/\d+', p.path):
        raise ValueError('Automatic snapshot supports direct Greenhouse postings only. Open the link and paste the employer description.')
    with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
        with client.stream('GET', 'https://job-boards.greenhouse.io'+p.path) as r:
            r.raise_for_status()
            body = bytearray()
            for chunk in r.iter_bytes():
                body.extend(chunk)
                if len(body) > 2_000_000:
                    raise ValueError('Posting exceeds limit')
    soup = BeautifulSoup(bytes(body), 'html.parser')
    for node in soup(['script', 'style', 'form', 'nav', 'footer']):
        node.decompose()
    content = soup.select_one('.job__description') or soup.select_one('#content') or soup.select_one('main')
    if content is None:
        raise ValueError('Description not found; paste the official text for review')
    text = content.get_text('\n', strip=True)
    if not 100 <= len(text) <= 18000:
        raise ValueError('Description empty or too long; paste a reviewed description')
    return save_snapshot(job_id, text, 'FETCHED_OFFICIAL_GREENHOUSE')
