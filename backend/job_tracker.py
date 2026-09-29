"""Readable job folders, editable status notes, and immutable submission copies."""
import hashlib
import html
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo

from .application_history import canonical, history_index, record_application_in_transaction
from .db import ROOT, connect, get_row, packed

ARCHIVE = Path(os.getenv('AUTOAPPLY_ARCHIVE', str(ROOT / 'job apps')))
STATUSES = {'DISCOVERED', 'SHORTLISTED', 'DRAFTED', 'READY', 'IN_PROGRESS', 'APPLIED',
            'OA', 'INTERVIEW', 'OFFER', 'REJECTED', 'WITHDRAWN', 'SKIPPED', 'SUBMISSION_UNKNOWN'}
AFTER_APPLY = {'APPLIED', 'OA', 'INTERVIEW', 'OFFER', 'REJECTED', 'WITHDRAWN'}


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec='seconds')


def toronto_time(timestamp):
    local = datetime.fromtimestamp(timestamp, ZoneInfo('America/Toronto'))
    return f'{local:%b} {local.day}, {local.year}, {local.hour % 12 or 12}:{local:%M %p %Z}'


def write_changed(path, text):
    if path.exists() and path.read_text(encoding='utf-8-sig') == text:
        return
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)


def validate_details(details):
    if details['status'] not in STATUSES:
        raise ValueError('Unknown status. Use: ' + ', '.join(sorted(STATUSES)))
    posted = details['posted']
    if posted != 'UNKNOWN':
        # A date-only source stays a date; never invent midnight or a timezone.
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', posted):
            datetime.strptime(posted, '%Y-%m-%d')
        elif datetime.fromisoformat(posted.replace('Z', '+00:00')).utcoffset() is None:
            raise ValueError('Posting timestamps require a timezone, or supply YYYY-MM-DD only')
        if details['posted_source'] in {'', 'UNKNOWN'}:
            raise ValueError('Provide the source/evidence for the posting date')
    if any('\n' in details[k] or '\r' in details[k] for k in ('posted', 'posted_source')):
        raise ValueError('Posting date and source must each fit on one line')
    if len(details['notes']) > 20000 or len(details['posted_source']) > 2000:
        raise ValueError('Tracker notes or posting evidence too long')
    return details


def status_text(details):
    return f'Status: {details["status"]}\nPosted: {details["posted"]}\nPosted source: {details["posted_source"]}\nNotes:\n{details["notes"]}\n'


def read_status(path):
    text = path.read_text(encoding='utf-8-sig').replace('\r\n', '\n')
    header, notes = text.split('Notes:\n', 1)
    fields = dict(line.split(': ', 1) for line in header.strip().splitlines())
    if set(fields) != {'Status', 'Posted', 'Posted source'}:
        raise ValueError('Keep the Status, Posted, Posted source, and Notes labels')
    return validate_details({'status': fields['Status'].strip().upper(), 'posted': fields['Posted'].strip(),
                             'posted_source': fields['Posted source'].strip(), 'notes': notes.rstrip()})


def sync_job_tracker():
    """Import user status edits, mirror history, and refresh all local job folders."""
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    existing = {p.name[-8:]: p for p in ARCHIVE.iterdir() if p.is_dir()}
    errors, rows = [], []
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        histories = history_index(c)
        saved = {r['key']: json.loads(r['value']) for r in c.execute("SELECT * FROM settings WHERE key LIKE 'job-tracker:%'")}
        snapshots = {r['job_id']: dict(r) for r in c.execute('SELECT * FROM snapshots ORDER BY created')}
        source_rows = c.execute('SELECT job_id,group_concat(source_id) AS sources FROM source_jobs GROUP BY job_id').fetchall()
        sources = {r['job_id']: r['sources'] for r in source_rows}
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        runs, outreach = {}, {}
        if 'latex_runs' in tables:
            for run in c.execute('SELECT job_id,id,state,folder FROM latex_runs ORDER BY created'):
                runs.setdefault(run['job_id'], []).append(dict(run))
        if 'outreach' in tables:
            for contact in c.execute('SELECT job_id,state,artifact FROM outreach'):
                outreach.setdefault(contact['job_id'], []).append(dict(contact))
        for raw in c.execute('SELECT * FROM jobs ORDER BY updated DESC,id').fetchall():
            job = dict(raw)
            key = 'job-tracker:' + job['id']
            old = saved.get(key)
            slug = re.sub(r'[^a-zA-Z0-9-]+', '-', f'{job["company"]}-{job["title"]}').strip('-')[:80] or 'application'
            folder = ARCHIVE / old['folder'] if old else existing.get(job['id'][:8], ARCHIVE / (slug + '-' + job['id'][:8]))
            if not folder.resolve().is_relative_to(ARCHIVE.resolve()):
                raise ValueError('Job folder must stay within job apps')
            folder.mkdir(exist_ok=True)
            history = histories.get(canonical(c, job['url']))
            initial = history['state'] if history and history['state'] != 'NOT_APPLIED' else ('SKIPPED' if job['state'] == 'SKIPPED' else 'DISCOVERED')
            if initial == 'DISCOVERED' and any(r['state'] == 'READY_FOR_REVIEW' for r in runs.get(job['id'], [])):
                initial = 'DRAFTED'
            details = {k: old[k] for k in ('status', 'posted', 'posted_source', 'notes')} if old else {
                'status': initial, 'posted': 'UNKNOWN', 'posted_source': 'UNKNOWN', 'notes': ''}
            status_file = folder / 'STATUS.txt'
            valid = True
            if status_file.exists():
                try:
                    edited = read_status(status_file)
                    if edited != details:
                        if history and history['state'] == 'APPLIED' and edited['status'] not in AFTER_APPLY:
                            raise ValueError('Previously applied job cannot be reset to an unapplied status')
                        if history and history['state'] == 'SUBMISSION_UNKNOWN' and edited['status'] not in AFTER_APPLY | {'SUBMISSION_UNKNOWN'}:
                            raise ValueError('Resolve the unknown submission before resetting its status')
                        target = 'APPLIED' if edited['status'] in AFTER_APPLY else edited['status']
                        if target in {'APPLIED', 'IN_PROGRESS', 'SKIPPED', 'SUBMISSION_UNKNOWN'} and (not history or history['state'] != target):
                            evidence = f'User-maintained STATUS.txt: {edited["status"]}. {edited["notes"]}'
                            record_application_in_transaction(c, job['url'], target, evidence)
                            history = {'state': target, 'evidence': evidence, 'updated': time.time()}
                            histories[canonical(c, job['url'])] = history
                        elif edited['status'] != 'SKIPPED' and history and history['state'] == 'SKIPPED':
                            record_application_in_transaction(c, job['url'], 'NOT_APPLIED', 'User reopened STATUS.txt')
                            histories.pop(canonical(c, job['url']), None)
                            history = None
                        details = edited
                        if job['state'] == 'SKIPPED' and edited['status'] != 'SKIPPED':
                            c.execute("UPDATE jobs SET state='DISCOVERED' WHERE id=?", (job['id'],))
                except (ValueError, KeyError, UnicodeError) as exc:
                    errors.append({'job_id': job['id'], 'file': str(status_file), 'error': str(exc)})
                    valid = False  # Preserve invalid edits for correction, never overwrite them.
            if history and history['state'] == 'APPLIED' and details['status'] not in AFTER_APPLY:
                details['status'] = 'APPLIED'
            elif history and history['state'] in {'SUBMISSION_UNKNOWN', 'IN_PROGRESS', 'SKIPPED'}:
                details['status'] = history['state']
            record = details | {'folder': folder.name}
            record['updated_at'] = old.get('updated_at', iso(time.time())) if old and all(old.get(k) == v for k, v in record.items()) else iso(time.time())
            if old != record:
                c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, packed(record)))
            if valid:
                write_changed(status_file, status_text(details))
            for name in ('drafts', 'submitted'):
                (folder / name).mkdir(exist_ok=True)
            snapshot = snapshots.get(job['id'])
            if snapshot:
                write_changed(folder / 'job-description.txt', snapshot['text'])
            info = (f'# {job["company"]} — {job["title"]}\n\n'
                    f'Status: {details["status"]}\n\nTracker updated (UTC): {record["updated_at"]}\n\nLocation: {job["location"]}\n\nTerm: {job["term"]}\n\n'
                    f'Posting: {job["url"]}\n\nPosted: {details["posted"]}\n\nPosting date evidence: {details["posted_source"]}\n\n'
                    f'First discovered (Toronto): {toronto_time(job["created"])}\n\nLast listing change (UTC): {iso(job["updated"])}\n\n'
                    f'Sources: {sources.get(job["id"], "unknown")}\n\nJob ID: {job["id"]}\n\n'
                    f'Notes: {details["notes"]}\n\nApplication history: {history["state"] if history else "NOT_RECORDED"}\n\n'
                    f'Evidence: {history["evidence"] if history else "No confirmed application recorded."}\n\n'
                    'Edit STATUS.txt to update status, posting date/evidence, or notes. This file is generated.\n\n'
                    '[Working drafts](drafts/) · [Submitted copies](submitted/) · [Editable status](STATUS.txt)\n\n'
                    'drafts/ holds working documents. submitted/ holds confirmed exact copies with manifests.\n'
                    'Existing version folders are retained. APPLIED alone does not prove which documents were submitted.\n')
            for run in runs.get(job['id'], []):
                info += f'\nResume version {run["id"]}: {run["state"]} — [{run["id"]}/]({quote(run["id"])}/)\n'
            for contact in outreach.get(job['id'], []):
                relative = Path(os.path.relpath(contact['artifact'], folder)).as_posix()
                info += f'\n[Outreach draft]({quote(relative)}): {contact["state"]}\n'
            write_changed(folder / 'JOB.md', info)
            rows.append(job | details | {'folder': folder.name})
    render_index(rows, errors)
    return {'jobs': len(rows), 'index': str(ARCHIVE / 'APPLICATIONS.html'), 'errors': errors}


def render_index(rows, errors):
    rows.sort(key=lambda r: (r['status'] == 'DISCOVERED', -r['updated']))
    rendered = []
    for row in rows:
        folder = quote(row['folder'])
        cells = [f'<a href="{folder}/JOB.md">{html.escape(row["company"])}</a>', html.escape(row['title']),
                 html.escape(row['location'] or ''), html.escape(row['status']), html.escape(row['posted']),
                 toronto_time(row['created']), f'<a href="{html.escape(row["url"], quote=True)}">Posting</a>',
                 f'<button type="button" data-folder="{html.escape(row["folder"], quote=True)}">Edit status / posted</button><br>'
                 f'<a href="{folder}/STATUS.txt">STATUS.txt</a> · <a href="{folder}/">Documents</a>', html.escape(row['notes'])]
        rendered.append('<tr>' + ''.join('<td>' + v + '</td>' for v in cells) + '</tr>')
    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Application tracker</title>'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<style>*{box-sizing:border-box}body{font:15px/1.5 system-ui;margin:28px;color:#253247;background:#f6f8fb}'
            'h1{font-size:28px;margin-bottom:8px}p{color:#526176}a{color:#245ca6;text-underline-offset:3px}'
            'input,select,textarea,button{font:inherit;border:1px solid #cbd5e1;border-radius:7px;padding:9px 12px}'
            'input,select,textarea{background:white;color:inherit}#q{display:block;width:min(650px,100%);margin-top:8px}'
            'button{cursor:pointer;background:white;color:#245ca6;font-weight:600}button:hover{background:#eef4fd}'
            '#connect,#save{background:#245ca6;color:white;border-color:#245ca6}button:disabled{opacity:.6;cursor:wait}'
            ':focus-visible{outline:3px solid #82b5fa;outline-offset:2px}'
            'table{border-collapse:collapse;width:100%;margin-top:20px;background:white}'
            'th,td{text-align:left;padding:12px;border-bottom:1px solid #e2e8f0;vertical-align:top}'
            'th{position:sticky;top:0;background:#eaf0f7;font-size:13px}tbody tr:hover{background:#f8faff}'
            'td:last-child{white-space:pre-wrap;min-width:180px;overflow-wrap:anywhere}td button{font-size:13px;margin-bottom:7px}'
            'dialog{width:min(560px,calc(100% - 32px));max-height:90vh;overflow:auto;border:1px solid #d7e0eb;'
            'border-radius:12px;padding:24px;box-shadow:0 16px 50px #172b4d33;color:inherit}'
            'dialog::backdrop{background:#172b4d66}dialog h2{font-size:20px;margin:0 0 18px;overflow-wrap:anywhere}'
            'dialog label{display:block;font-weight:600}dialog input,dialog select,dialog textarea{display:block;width:100%;margin-top:6px;font-weight:400}'
            'dialog textarea{resize:vertical;min-height:130px}dialog p{font-size:14px}#edit-error{color:#b42318}'
            '@media(max-width:640px){body{margin:14px}th,td{padding:9px}}</style>'
            f'<h1>Application tracker</h1><p>{len(rows)} saved jobs. Refreshed {iso(time.time())}. '
            'Posted UNKNOWN means the employer posting date has not been verified; first discovered is separate.</p>'
            '<p>Use Chrome or Edge. Select your job apps folder to enable editing, then use a row’s Edit button. '
            'Save writes STATUS.txt immediately. Run scripts/sync-job-tracker.ps1 or wait for the hourly check '
            'to update backend records and this generated overview.</p>'
            '<button id="connect" type="button">Select job apps folder</button><p id="message" role="status"></p>'
            f'<p>Status file errors: {len(errors)}. See TRACKER-ERRORS.json if nonzero.</p>'
            '<label for="q">Filter company, role, location, status, or notes: </label><input id="q" type="search">'
            '<table><thead><tr>' + ''.join('<th>' + h + '</th>' for h in ['Company', 'Role', 'Location', 'Status', 'Posted', 'First discovered (Toronto)', 'Link', 'Local files', 'Notes'])
            + '</tr></thead><tbody>' + ''.join(rendered) + '</tbody></table>'
            '<dialog id="editor" aria-labelledby="edit-title"><form id="edit-form"><h2 id="edit-title">Edit job</h2>'
            '<p><label>Status <select id="status">' + ''.join(f'<option>{s}</option>' for s in sorted(STATUSES)) + '</select></label></p>'
            '<p><label>Posted <input id="posted" required placeholder="UNKNOWN or YYYY-MM-DD"></label></p>'
            '<p>Use UNKNOWN, YYYY-MM-DD, or an ISO timestamp with timezone.</p>'
            '<p><label>Posting date source / evidence <input id="posted-source" maxlength="2000" required></label></p>'
            '<p><label>Notes <textarea id="notes" rows="5" maxlength="20000" placeholder="Follow-ups, deadlines, or anything to remember"></textarea></label></p>'
            '<p>Only select APPLIED or later stages after actually submitting.</p>'
            '<p id="edit-error" role="alert"></p><button id="save" type="submit">Save</button> '
            '<button id="cancel" type="button">Cancel</button></form></dialog>'
            '<script>const afterApply = ' + json.dumps(sorted(AFTER_APPLY)) + ';\n'
            + Path(__file__).with_name('tracker_editor.js').read_text(encoding='utf-8') + '</script></html>')
    write_changed(ARCHIVE / 'APPLICATIONS.html', page)
    write_changed(ARCHIVE / 'TRACKER-ERRORS.json', json.dumps(errors, indent=2))


def update_job_tracking(job_id, status=None, posted=None, posted_source=None, notes=None):
    sync_job_tracker()
    get_row('jobs', job_id)
    with connect() as c:
        record = json.loads(c.execute('SELECT value FROM settings WHERE key=?', ('job-tracker:' + job_id,)).fetchone()[0])
    for key, value in {'status': status, 'posted': posted, 'posted_source': posted_source, 'notes': notes}.items():
        if value is not None:
            if not isinstance(value, str):
                raise ValueError('Tracker fields must be text')
            record[key] = value
    validate_details(record)
    write_changed(ARCHIVE / record['folder'] / 'STATUS.txt', status_text(record))
    result = sync_job_tracker()
    failure = next((e for e in result['errors'] if e['job_id'] == job_id), None)
    if failure:
        raise ValueError(failure['error'])
    return {'job_id': job_id, 'status': record['status'], 'folder': str(ARCHIVE / record['folder'])}


def archive_submitted_documents(job_id, files, evidence, submitted_at=None):
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError('Actual user confirmation or a receipt is required')
    if not isinstance(files, list) or not 1 <= len(files) <= 10:
        raise ValueError('Supply one to ten exact submitted document paths')
    if submitted_at is not None and datetime.fromisoformat(submitted_at.replace('Z', '+00:00')).utcoffset() is None:
        raise ValueError('Submission timestamp requires a timezone; omit it if unknown')
    job = get_row('jobs', job_id)
    documents = []
    for value in files:
        path = Path(value).resolve(strict=True)
        if not path.is_file() or path.stat().st_size > 20_000_000:
            raise ValueError('Each document must be a local file of at most 20 MB')
        data = path.read_bytes()
        documents.append((path.name, data, hashlib.sha256(data).hexdigest()))
    sync_job_tracker()
    with connect() as c:
        record = json.loads(c.execute('SELECT value FROM settings WHERE key=?', ('job-tracker:' + job_id,)).fetchone()[0])
    base = ARCHIVE / record['folder'] / 'submitted'
    fingerprint = hashlib.sha256(packed({'files': [(n, h) for n, _, h in documents], 'submitted_at': submitted_at, 'evidence': evidence}).encode()).hexdigest()[:12]
    # Repeated archive requests reuse the same immutable copy.
    prior = next(base.glob('*-' + fingerprint), None)
    folder = prior or base / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + fingerprint)
    manifest = {'job_id': job_id, 'posting_url': job['url'], 'submitted_at': submitted_at,
                'archived_at': iso(time.time()), 'evidence': evidence, 'files': []}
    if prior:
        stored = json.loads((prior / 'manifest.json').read_text(encoding='utf-8'))
        if any(hashlib.sha256((prior / f['file']).read_bytes()).hexdigest() != f['sha256'] for f in stored['files']):
            raise ValueError('Archived documents changed; preserve and investigate the archive')
    else:
        folder.mkdir()
        for index, (name, data, sha) in enumerate(documents, 1):
            filename = f'{index:02d}-{name}'
            (folder / filename).write_bytes(data)
            manifest['files'].append({'file': filename, 'original_name': name, 'sha256': sha, 'bytes': len(data)})
        (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    with connect() as c:
        record_application_in_transaction(c, job['url'], 'APPLIED', evidence)
    sync_job_tracker()
    return {'job_id': job_id, 'folder': str(folder), 'files': len(documents), 'note': 'Confirmed manual submission archived; nothing was uploaded or submitted by this tool.'}
