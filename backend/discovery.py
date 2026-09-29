import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import httpx
from bs4 import BeautifulSoup
from .db import ROOT, connect, digest

SOURCES = {
    'simplify-summer': 'https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md',
    'simplify-offseason': 'https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README-Off-Season.md',
    'zapply': 'https://raw.githubusercontent.com/zapplyjobs/Internships-2027/main/README.md',
}


def normalize_url(url):
    p = urlsplit(url.strip())
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError('Expected a public HTTPS posting URL')
    if p.hostname in {'localhost', '127.0.0.1', '::1'} or '.' not in p.hostname:
        raise ValueError('Invalid posting host')
    host = p.hostname.lower()
    if host == 'boards.greenhouse.io':
        host = 'job-boards.greenhouse.io'
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and not (k.lower() == 'ref' and v.lower() == 'simplify')]
    return urlunsplit(('https', host, p.path.rstrip('/'), urlencode(sorted(query)), ''))


def parse_source(text, source):
    rows = []
    if source.startswith('simplify'):
        last_company = ''
        for tr in BeautifulSoup(text, 'html.parser').find_all('tr'):
            cells = tr.find_all('td', recursive=False)
            if len(cells) < 5:
                continue
            company = cells[0].get_text(' ', strip=True)
            if company == '↳':
                company = last_company
            else:
                last_company = company
            idx = 4 if source == 'simplify-offseason' else 3
            links = cells[idx].find_all('a', href=True)
            direct = [a['href'] for a in links if urlsplit(a['href']).hostname != 'simplify.jobs']
            if not company or not direct:
                continue  # closed listings have no application link
            rows.append(dict(company=company, title=cells[1].get_text(' ', strip=True),
                             location=cells[2].get_text(' ', strip=True), url=direct[0],
                             term=cells[3].get_text(' ', strip=True) if idx == 4 else 'Summer 2027 (source label)'))
    else:
        for line in text.splitlines():
            if not line.startswith('|'):
                continue
            cells = [x.strip() for x in line.strip('|').split('|')]
            if len(cells) < 6:
                continue
            links = re.findall(r'\]\((https://[^\s)]+)\)', cells[-1])
            if not links:
                continue
            company = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', cells[0]).replace('**', '')
            rows.append(dict(company=company, title=cells[1], location=cells[2], url=links[0], term='2027 (source label)'))
    clean = []
    for row in rows:
        try:
            row['url'] = normalize_url(row['url'])
            clean.append(row)
        except ValueError:
            pass
    if not clean:
        raise ValueError('No job rows parsed; source format may have changed')
    return clean


def import_rows(source, text, etag=None):
    rows = parse_source(text, source)
    added = changed = 0
    now = time.time()
    with connect() as c:
        for row in rows:
            job_id = digest(row['url'])[:24]
            # Term/age/source membership are not evidence of a new role.
            h = digest({k: row[k] for k in ('url', 'company', 'title', 'location')})
            old = c.execute('SELECT material_hash FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not old:
                added += 1
                c.execute('INSERT INTO jobs(id,url,company,title,location,term,material_hash,created,updated) VALUES (?,?,?,?,?,?,?,?,?)',
                          (job_id, row['url'], row['company'], row['title'], row['location'], row['term'], h, now, now))
            elif old[0] != h:
                changed += 1
                c.execute('UPDATE jobs SET company=?,title=?,location=?,material_hash=?,updated=? WHERE id=?',
                          (row['company'], row['title'], row['location'], h, now, job_id))
            c.execute('INSERT OR IGNORE INTO source_jobs VALUES (?,?)', (source, job_id))
        c.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,NULL)', (source, SOURCES[source], etag, digest(text), now))
    return {'source': source, 'added': added, 'changed': changed, 'parsed': len(rows)}


def refresh(cached=False):
    results = []
    for source, url in SOURCES.items():
        try:
            if cached:
                text = (ROOT / f'data/sources/{source}.md').read_text(encoding='utf-8')
                results.append(import_rows(source, text))
                continue
            with connect() as c:
                row = c.execute('SELECT etag FROM sources WHERE id=?', (source,)).fetchone()
            headers = {'User-Agent': 'ApplyDesk-MVP/0.1'}
            if row and row[0]:
                headers['If-None-Match'] = row[0]
            # Fixed GitHub raw endpoints only. Redirects are not followed.
            with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
                with client.stream('GET', url, headers=headers) as r:
                    if r.status_code == 304:
                        with connect() as c:
                            c.execute('UPDATE sources SET checked=?,error=NULL WHERE id=?', (time.time(), source))
                        results.append({'source': source, 'unchanged': True})
                        continue
                    r.raise_for_status()
                    body = bytearray()
                    for chunk in r.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 5_000_000:
                            raise ValueError('Source exceeds 5 MB limit')
                    results.append(import_rows(source, body.decode('utf-8'), r.headers.get('etag')))
        except Exception as exc:
            error = type(exc).__name__ + ': source unavailable or invalid; existing listings preserved'
            with connect() as c:
                c.execute('INSERT INTO sources(id,url,checked,error) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET checked=excluded.checked,error=excluded.error',
                          (source, url, time.time(), error))
            results.append({'source': source, 'error': error})
    return results
