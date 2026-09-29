"""Local outreach drafts and manual history. No sending or browser integration."""
import json
import time
from pydantic import Field, field_validator

from .db import ROOT, connect, digest, get_row, packed
from .discovery import normalize_url
from .latex_source import load_master, source_context
from .models import StrictModel


def initialize():
    with connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS outreach(id TEXT PRIMARY KEY, job_id TEXT NOT NULL, contact_url TEXT NOT NULL, state TEXT NOT NULL, content TEXT NOT NULL, artifact TEXT NOT NULL, evidence TEXT, updated REAL NOT NULL)')


def get_outreach_context(job_id):
    job = get_row('jobs', job_id)
    source, master = load_master()
    with connect() as c:
        snapshot = c.execute('SELECT id,text FROM snapshots WHERE job_id=? ORDER BY created DESC LIMIT 1', (job_id,)).fetchone()
    if snapshot is None:
        raise ValueError('Save the official job description before drafting outreach')
    return {'job': job, 'snapshot_id': snapshot['id'], 'job_description': snapshot['text'],
            'master_sha256': master['sha256'], 'sender_name': master['identity'],
            'source': source_context(source), 'history': list_outreach(job_id),
            'instructions': 'Use Sol to draft a short email and LinkedIn message. Cite source bullet IDs for personal claims. Find a relevant recruiter or team member through public sources; record evidence of their role and any email address. Do not invent relationships, hiring authority, referrals, or contact details. Save a draft for user review; nothing is sent.'}


class OutreachDraft(StrictModel):
    job_id: str = Field(min_length=1, max_length=100)
    snapshot_id: str = Field(min_length=1, max_length=100)
    master_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    contact_name: str = Field(min_length=1, max_length=150)
    contact_role: str = Field(min_length=1, max_length=250)
    contact_url: str = Field(min_length=1, max_length=2048)
    contact_evidence: str = Field(min_length=1, max_length=2000)
    email: str | None = Field(default=None, max_length=254)
    email_evidence: str | None = Field(default=None, max_length=1000)
    subject: str = Field(min_length=1, max_length=200)
    email_body: str = Field(min_length=1, max_length=4000)
    linkedin_message: str = Field(min_length=1, max_length=1500)
    source_bullet_ids: list[str] = Field(min_length=1, max_length=6)

    @field_validator('contact_url')
    @classmethod
    def public_url(cls, value):
        return normalize_url(value)

    @field_validator('contact_name', 'contact_role', 'subject', 'email')
    @classmethod
    def single_line(cls, value):
        if value is not None and (not value.strip() or any(ch in value for ch in '\r\n\x00')):
            raise ValueError('Expected a nonempty single line')
        if value is not None:
            return value.strip()
        return value

    @field_validator('contact_evidence', 'email_evidence', 'email_body', 'linkedin_message')
    @classmethod
    def nonempty_text(cls, value):
        if value is not None and not value.strip():
            raise ValueError('Expected nonempty text')
        return value.strip() if value is not None else value


def save_outreach_draft(**arguments):
    draft = OutreachDraft.model_validate(arguments)
    if draft.email and (not draft.email_evidence or not draft.email_evidence.strip()):
        raise ValueError('Record where this exact email was published or confirmed by the user; never guess addresses')
    if draft.email and (draft.email.count('@') != 1 or any(ch.isspace() for ch in draft.email)
                        or not all(draft.email.split('@'))):
        raise ValueError('Invalid email address')
    context = get_outreach_context(draft.job_id)
    if draft.master_sha256 != context['master_sha256'] or draft.snapshot_id != context['snapshot_id']:
        raise ValueError('Resume or JD changed; refresh context and check the draft again')
    bullets = {b['id']: b for b in context['source']['bullets']}
    if set(draft.source_bullet_ids) - bullets.keys() or len(set(draft.source_bullet_ids)) != len(draft.source_bullet_ids):
        raise ValueError('Use distinct bullet IDs from the protected master')
    content = draft.model_dump() | {'company': context['job']['company'], 'role': context['job']['title'],
                                  'posting_url': context['job']['url'],
                                  'source_bullets': [bullets[b] for b in draft.source_bullet_ids],
                                  'review': 'DRAFT: evidence references recorded; factual support and contact relevance require review.'}
    identity = digest([draft.job_id, draft.contact_url])[:24]
    artifact = ROOT / 'outreach' / identity / (digest(content)[:16] + '.md')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        prior = c.execute("SELECT id,state FROM outreach WHERE contact_url=? AND state != 'DRAFT'", (draft.contact_url,)).fetchone()
        if prior:
            raise ValueError(f'This contact already has outreach history ({prior["state"]}, {prior["id"]}); review it before further contact')
        artifact.parent.mkdir(parents=True, exist_ok=True)
        text = (f'# Outreach draft — {content["company"]}\n\nNot sent. Review all claims and recipient details.\n\n'
                f'Role: {content["role"]}\nPosting: {content["posting_url"]}\n\n'
                f'Contact: {draft.contact_name} — {draft.contact_role}\nSource: {draft.contact_url}\n'
                f'Contact evidence: {draft.contact_evidence}\nEmail: {draft.email or "Not provided; do not guess"}\n'
                f'Email evidence: {draft.email_evidence or "Not provided"}\n\n'
                f'## Email\n\nSubject: {draft.subject}\n\n{draft.email_body}\n\n'
                f'## LinkedIn message\n\n{draft.linkedin_message}\n\n## Resume evidence\n\n'
                + '\n'.join(f'- {b["id"]}: {b["text"]}' for b in content['source_bullets']) + '\n')
        if not artifact.exists():
            artifact.write_text(text, encoding='utf-8')
        c.execute('INSERT INTO outreach VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET content=excluded.content,artifact=excluded.artifact,updated=excluded.updated',
                  (identity, draft.job_id, draft.contact_url, 'DRAFT', packed(content), str(artifact), None, time.time()))
    return {'outreach_id': identity, 'state': 'DRAFT', 'artifact': str(artifact), 'sent': False,
            'note': 'Local draft only. No message sent, no recipient contacted, no independent factual approval.'}


def list_outreach(job_id=None):
    initialize()
    with connect() as c:
        rows = c.execute('SELECT * FROM outreach WHERE (? IS NULL OR job_id=?) ORDER BY updated DESC', (job_id, job_id)).fetchall()
    return [{k: row[k] for k in ('id', 'job_id', 'contact_url', 'state', 'artifact', 'evidence', 'updated')}
            | {'contact_name': json.loads(row['content'])['contact_name']} for row in rows]


def record_outreach_result(outreach_id, state, evidence):
    if state not in {'SENT', 'REPLIED', 'CLOSED'} or not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 2000:
        raise ValueError('Provide SENT, REPLIED, or CLOSED with actual user confirmation or observed evidence')
    initialize()
    with connect() as c:
        old = c.execute('SELECT state FROM outreach WHERE id=?', (outreach_id,)).fetchone()
        if old is None:
            raise ValueError('Unknown outreach draft')
        if {'DRAFT': 0, 'SENT': 1, 'REPLIED': 2, 'CLOSED': 3}[state] < {'DRAFT': 0, 'SENT': 1, 'REPLIED': 2, 'CLOSED': 3}[old['state']]:
            raise ValueError('Outreach history cannot move backwards')
        c.execute('UPDATE outreach SET state=?,evidence=?,updated=? WHERE id=?', (state, evidence.strip(), time.time(), outreach_id))
    return {'outreach_id': outreach_id, 'state': state, 'note': 'Recorded manual history only; this tool never sends messages.'}
