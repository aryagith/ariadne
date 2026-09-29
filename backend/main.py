import hashlib
import hmac
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from . import actions, artifacts, discovery, notifications, postings
from .db import ROOT, audit, connect, digest, get_row, init, packed, set_setting, setting, uid
from .fixtures import DEMO, DEMO_REQUIREMENTS
from .models import ApprovalRequest, Consent, FixtureAction, Login, Prepare, Profile, SnapshotInput
from .pipeline import enqueue, work_once
from .providers import consent_key, provider_configuration

load_dotenv(ROOT / '.env')


def password():
    configured = os.getenv('APP_PASSWORD')
    if configured:
        return configured
    path = ROOT / 'data/auth-token.txt'
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open('x', encoding='utf-8') as f:
                f.write(secrets.token_urlsafe(24))
        except FileExistsError:
            pass
    return path.read_text(encoding='utf-8').strip()


def save_profile(profile, verified=False, synthetic=False):
    data = profile.model_dump()
    identity = digest({'profile': data, 'verified': verified, 'synthetic': synthetic})[:24]
    with connect() as c:
        c.execute('INSERT OR IGNORE INTO profiles VALUES (?,?,?,?,?,?)',
                  (identity, packed(data), digest(data), int(verified), int(synthetic), time.time()))
    return identity


@asynccontextmanager
async def lifespan(app):
    init()
    password()
    with connect() as c:
        exists = c.execute('SELECT 1 FROM jobs LIMIT 1').fetchone()
    if not exists:
        discovery.refresh(cached=True)
    save_profile(DEMO, verified=True, synthetic=True)
    yield


app = FastAPI(title='Apply Desk', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
failed_logins = []


@app.middleware('http')
async def boundary(request: Request, call_next):
    origin = os.getenv('APP_ORIGIN', 'http://127.0.0.1:8000')
    expected = urlsplit(origin).netloc
    if request.headers.get('host') != expected:
        return JSONResponse({'detail': 'Unexpected host'}, status_code=403)
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        if request.headers.get('origin') != origin or request.headers.get('x-apply-desk') != '1':
            return JSONResponse({'detail': 'Origin/CSRF check failed'}, status_code=403)
        if int(request.headers.get('content-length', '0')) > 100_000:
            return JSONResponse({'detail': 'Request too large'}, status_code=413)
    if request.url.path.startswith('/api/') and request.url.path not in {'/api/login', '/api/health'}:
        token = request.cookies.get('apply_session', '')
        with connect() as c:
            session = c.execute('SELECT expires FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not session or session[0] < time.time():
            return JSONResponse({'detail': 'Sign in to your local dashboard'}, status_code=401)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=400)


@app.get('/api/health')
def health():
    return {'ok': True, 'live_execution': False}


@app.post('/api/login')
def login(body: Login, response: Response):
    now = time.time()
    failed_logins[:] = [t for t in failed_logins if now-t < 60]
    if len(failed_logins) >= 10:
        raise HTTPException(429, 'Wait a minute before retrying')
    if not hmac.compare_digest(body.password, password()):
        failed_logins.append(now)
        raise HTTPException(401, 'Incorrect passphrase')
    token = secrets.token_urlsafe(32)
    with connect() as c:
        c.execute('INSERT INTO sessions VALUES (?,?)', (hashlib.sha256(token.encode()).hexdigest(), now+43200))
    response.set_cookie('apply_session', token, httponly=True, samesite='strict', max_age=43200,
                        secure=os.getenv('APP_ORIGIN', '').startswith('https://'))
    return {'ok': True}


@app.post('/api/logout')
def logout(request: Request, response: Response):
    with connect() as c:
        c.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(request.cookies.get('apply_session', '').encode()).hexdigest(),))
    response.delete_cookie('apply_session')
    return {'ok': True}


@app.get('/api/dashboard')
def dashboard():
    with connect() as c:
        jobs = [dict(r) for r in c.execute('SELECT j.*,group_concat(s.source_id) AS sources FROM jobs j LEFT JOIN source_jobs s ON j.id=s.job_id GROUP BY j.id ORDER BY j.created DESC,j.company LIMIT 10000')]
        profiles = [dict(r) for r in c.execute('SELECT * FROM profiles ORDER BY created DESC')]
        packets = [dict(r) for r in c.execute('SELECT id,job_id,profile_id,status,created FROM packets ORDER BY created DESC')]
        tasks = [dict(r) for r in c.execute('SELECT * FROM tasks ORDER BY created DESC LIMIT 30')]
        sources = [dict(r) for r in c.execute('SELECT * FROM sources')]
        inbox = [dict(r) for r in c.execute('SELECT * FROM notifications ORDER BY created DESC LIMIT 30')]
    for p in profiles:
        p['content'] = json.loads(p['content'])
        p['provider_consent'] = setting(consent_key(p['id']), False)
    return {'jobs': jobs, 'profiles': profiles, 'packets': packets, 'tasks': tasks, 'sources': sources, 'notifications': inbox,
            'mode': os.getenv('PROVIDER_MODE', 'demo'), 'live_execution': False, 'worker_paused': setting('worker_paused', False),
            'provider_configuration': provider_configuration(), 'notifications_enabled': setting('notifications_enabled', False),
            'notification_destination': os.getenv('NTFY_URL', ''), 'public_app_url': os.getenv('PUBLIC_APP_URL', '')}


@app.post('/api/refresh')
def refresh():
    return discovery.refresh()


@app.get('/api/jobs/{job_id}')
def job_detail(job_id: str):
    job = get_row('jobs', job_id)
    with connect() as c:
        snapshot = c.execute('SELECT * FROM snapshots WHERE job_id=? ORDER BY created DESC LIMIT 1', (job_id,)).fetchone()
    return {**job, 'snapshot': dict(snapshot) if snapshot else None}


@app.post('/api/jobs/{job_id}/snapshot')
def snapshot(job_id: str, body: SnapshotInput):
    return {'id': postings.save_snapshot(job_id, body.text, 'USER_PASTED_OFFICIAL_TEXT_UNVERIFIED')}


@app.post('/api/jobs/{job_id}/fetch')
def fetch(job_id: str):
    try:
        return {'id': postings.fetch_posting(job_id)}
    except ValueError:
        raise
    except Exception:
        raise HTTPException(502, 'Official posting unavailable; paste its text for review')


@app.post('/api/jobs/{job_id}/practice')
def practice(job_id: str):
    return {'id': postings.save_snapshot(job_id, DEMO_REQUIREMENTS, 'SYNTHETIC')}


@app.post('/api/jobs/{job_id}/skip')
def skip(job_id: str):
    get_row('jobs', job_id)
    with connect() as c:
        c.execute("UPDATE jobs SET state='SKIPPED' WHERE id=?", (job_id,))
    return {'ok': True}


@app.post('/api/profiles')
def import_profile(body: Profile):
    return {'id': save_profile(body)}


@app.post('/api/profiles/{profile_id}/verify')
def verify(profile_id: str, body: Consent):
    row = get_row('profiles', profile_id)
    return {'id': save_profile(Profile.model_validate_json(row['content']), verified=True, synthetic=bool(row['synthetic']))}


@app.post('/api/profiles/{profile_id}/consent')
def consent(profile_id: str, body: Consent):
    row = get_row('profiles', profile_id)
    if not row['verified']:
        raise ValueError('Verify the facts first')
    set_setting(consent_key(profile_id), True)
    return {'ok': True}


@app.post('/api/jobs/{job_id}/prepare')
def prepare(job_id: str, body: Prepare, background: BackgroundTasks):
    if setting('worker_paused', False):
        raise ValueError('Preparation worker is paused')
    task = enqueue(job_id, body.profile_id)
    background.add_task(work_once)
    return {'task_id': task}


@app.get('/api/packets/{packet_id}')
def packet(packet_id: str):
    row = get_row('packets', packet_id)
    content = json.loads(row['content'])
    if digest(content) != row['hash']:
        raise ValueError('Packet integrity check failed')
    return {**row, 'content': content}


@app.get('/api/packets/{packet_id}/resume.html', response_class=HTMLResponse)
def preview(packet_id: str):
    return artifacts.resume_html(packet(packet_id)['content'])


@app.get('/api/packets/{packet_id}/resume.pdf')
def pdf(packet_id: str):
    return Response(artifacts.resume_pdf(packet(packet_id)['content']), media_type='application/pdf',
                    headers={'Content-Disposition': 'attachment; filename="resume-draft.pdf"'})


@app.get('/api/packets/{packet_id}/packet.zip')
def download(packet_id: str):
    return Response(artifacts.packet_zip(packet(packet_id)['content']), media_type='application/zip',
                    headers={'Content-Disposition': 'attachment; filename="application-packet.zip"'})


@app.post('/api/packets/{packet_id}/approvals')
def approval(packet_id: str, body: ApprovalRequest):
    return {'id': actions.approve(packet_id, body.action)}


@app.post('/api/packets/{packet_id}/fixture')
def fixture(packet_id: str, body: FixtureAction):
    return actions.execute_fixture(packet_id, body.operation, body.approval_id, body.simulate_timeout)


@app.post('/api/worker/toggle')
def toggle():
    set_setting('worker_paused', not setting('worker_paused', False))
    return {'paused': setting('worker_paused')}


@app.post('/api/notifications/enable')
def enable_notifications(body: Consent):
    if not os.getenv('NTFY_URL') or not os.getenv('PUBLIC_APP_URL'):
        raise ValueError('Configure a private ntfy topic and a reachable HTTPS dashboard first')
    set_setting('notifications_enabled', True)
    return {'ok': True}


@app.post('/api/notifications/disable')
def disable_notifications():
    set_setting('notifications_enabled', False)
    return {'ok': True}


@app.post('/api/notifications/send')
def send_notifications():
    return notifications.dispatch()


if (ROOT / 'web/out').exists():
    app.mount('/', StaticFiles(directory=ROOT / 'web/out', html=True), name='dashboard')
