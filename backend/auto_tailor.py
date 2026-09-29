"""One Sol-only resume draft for a verified alert; no application execution."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import httpx
from pydantic import Field

from .application_history import BLOCKED_STATES, canonical, history_for
from .db import connect, digest, get_row, packed, set_setting, setting
from .job_tracker import ARCHIVE, sync_job_tracker, update_job_tracking
from .latex_render import compile_resume
from .latex_source import load_master, render_source, sha, source_context
from .latex_workflow import layout_feedback, resume_selection_summary, validate_section_priority
from .model_routing import ask_laya
from .models import StrictModel
from .resume_skills import catalog, validate_ranking
from .resume_style import BULLET_STYLE
from .subscription_models import SubscriptionModels

POLICY = 'auto-tailor-sol-high-v2-interview-prep'
MODEL = 'gpt-6-sol'
EFFORT = 'high'


class Edit(StrictModel):
    bullet_id: str
    text: str = Field(min_length=1, max_length=700)


class Draft(StrictModel):
    selected_bullet_ids: list[str] = Field(min_length=1)
    edits: list[Edit]
    ranked_skills: list[str]
    layout: Literal['standard', 'compact']
    skill_lines: int = Field(ge=1, le=3)
    selection_reason: str
    interview_notes: str = Field(min_length=1, max_length=12000)


class SelfCheck(StrictModel):
    ready: bool
    issues: list[str]


class SolOnlyRouter:
    def cached_route(self, folder, role, data, images=()):
        return {'backend': 'codex', 'model': MODEL, 'effort': EFFORT,
                'reason': 'user_pinned_sol_only'}


DRAFT_INSTRUCTIONS = (
    'Tailor ONE resume directly from the authoritative master evidence to the official JD. '
    'Return source bullet IDs, replacement plain text only where stronger, and ALL exact supplied '
    'skill names ranked once each by JD relevance. Never add a skill, metric, date, responsibility, '
    'causal outcome or eligibility claim. Keep original wording when it is stronger. '
    'Select the strongest relevant experience and one or two personal projects; select design teams '
    'when relevant and avoid repeating the same work as both a team entry and project. '
    'Retain education. Use STAR principles or Google XYZ (accomplished X, measured by Y, by doing Z) '
    'naturally; omit unsupported measurements instead of inventing Y. ' + BULLET_STYLE +
    ' Fill one readable page: target 94-100% of printable height, .5 inch margins preferred, '
    '.45 inch only if needed. Add relevant supported substance for underfill; shorten or omit '
    'weaker content for overflow. Never pad with filler or keyword stuffing. Prefer 1-2 Skills lines; '
    'use 3 only when the additional terms are relevant. Skills occupy 1-3 lines '
    'and emphasize supported explicit JD keywords, retaining those even when also shown in experience. '
    'Use measured layout feedback when revising; output a complete revised selection each time. '
    'Also write concise interview_notes in Markdown for this exact job and selected resume. '
    'For each achievement with a metric, cite the original source bullet ID and what the source '
    'actually establishes, including qualifications such as reported, approximate or contributed. '
    'Explain the supported personal contribution, implementation and likely technical follow-up '
    'questions. Where measurement method, baseline, sample size or attribution is unknown, '
    'explicitly list what the user needs to confirm; never invent an explanation or measurement story. '
    'A possible estimate belongs only in a clearly labeled Needs confirmation section: show the '
    'calculation method and missing inputs without guessing values. Never insert an unconfirmed '
    'estimate into the resume. Include a short Things to know before interview checklist tied to '
    'the JD, distinguishing study topics from claims of past experience.'
)
CHECK_INSTRUCTIONS = (
    'Check your assembled resume against the authoritative source, official JD, extracted PDF text '
    'and attached page images. This is a Sol self-check, not independent approval or an ATS score. '
    'Check every rewritten claim, metric, scope and skill for support; relevant selection; readable '
    'STAR/XYZ-style achievement bullets; repetition; correct contact details and visible links; '
    'missing content and reading order; one full readable page without clipping or overlap. '
    'The verified_skills catalog is authoritative master evidence: a listed skill does not need '
    'to appear in a selected experience or project to be supported. Use current_layout for this '
    'render, never infer fill from earlier drafts. Source evidence is a superset: intentional '
    'omissions are expected in a tailored one-page resume, and the master must never be edited. '
    'The PDF was compiled from the selected draft; evaluate its supplied text and images. '
    'Optional additions or stylistic '
    'preferences alone are not failures. Set ready true and issues empty only when all '
    'checks pass. Check interview_notes too: metric evidence must match cited source bullet IDs, '
    'unknown details must remain questions, and estimates must be clearly unconfirmed and absent '
    'from the resume. Never treat a plausible interview story as factual evidence. '
    'Name concrete repairs otherwise.'
)


def _guard(job_id):
    report = sync_job_tracker()
    if any(e['job_id'] == job_id for e in report['errors']):
        raise ValueError('Resolve invalid STATUS.txt edits before tailoring')
    job = get_row('jobs', job_id)
    with connect() as c:
        history = history_for(c, job['url'])
    tracking = setting('job-tracker:' + job_id)
    if (job['state'] in BLOCKED_STATES or tracking['status'] in BLOCKED_STATES
            or history and history['state'] in BLOCKED_STATES):
        raise ValueError('Application history blocks automatic tailoring')
    return job, tracking


def tailor_alert_resume(job_id, snapshot_id, verification, *, _runner=None, _compiler=None):
    """Caller verifies the live official JD, eligibility and new/materially changed status."""
    if not isinstance(verification, str) or not 20 <= len(verification) <= 3000:
        raise ValueError('Supply official URL, availability, term and eligibility verification notes')
    job, tracking = _guard(job_id)
    snapshot = get_row('snapshots', snapshot_id)
    if snapshot['job_id'] != job_id:
        raise ValueError('Snapshot belongs to another job')
    with connect() as c:
        latest = c.execute('SELECT id FROM snapshots WHERE job_id=? ORDER BY created DESC LIMIT 1', (job_id,)).fetchone()
        url = canonical(c, job['url'])
        if snapshot_id != latest['id'] or canonical(c, snapshot['url']) != url:
            raise ValueError('Use the latest saved official JD and current canonical URL')
    source, master = load_master()
    context = source_context(source)
    contact = re.search(r'\\begin\{center\}(.*?)\\end\{center\}', source, re.S)
    mocked = _runner is not None or _compiler is not None
    key = 'auto-tailor:' + digest([url, snapshot['text'], master['sha256'], POLICY, mocked])
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        previous = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        if previous:
            result = json.loads(previous['value'])
            if result['state'] in {'READY', 'MOCKED'}:
                for path, expected in result['hashes'].items():
                    if not Path(path).is_file() or sha(path) != expected:
                        raise ValueError('Saved resume changed; inspect it before reuse')
                return result
            raise ValueError('This JD/master version was already attempted; inspect ' + result['folder'])
        stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%SZ')
        folder = ARCHIVE / tracking['folder'] / 'drafts' / (stamp + '-' + key[-12:])
        folder.mkdir(parents=True, exist_ok=False)
        result = {'state': 'RUNNING', 'job_id': job_id, 'snapshot_id': snapshot_id,
                  'official_url': url, 'folder': str(folder), 'model': MODEL, 'effort': EFFORT,
                  'master_sha256': master['sha256'], 'jd_sha256': digest(snapshot['text']),
                  'policy': POLICY, 'verification': verification, 'mocked': mocked}
        c.execute('INSERT INTO settings VALUES (?,?)', (key, packed(result)))
    try:
        (folder / 'job-description.txt').write_text(snapshot['text'], encoding='utf-8')
        (folder / 'posting.txt').write_text(url + '\n\n' + verification, encoding='utf-8')
        advice = {'status': 'unavailable', 'note': 'Sol-only draft'}
        if not mocked:
            try:
                tier, confidence = ask_laya(
                    {'task': 'Select relevant resume evidence and write factual achievement bullets.',
                     'input_characters': len(source) + len(snapshot['text']), 'images': 0},
                    SimpleNamespace(laya_url=os.getenv('LAYA_URL', 'http://127.0.0.1:8001').rstrip('/')))
                advice = {'status': 'available', 'tier': tier, 'confidence': confidence,
                          'note': 'Decision support only; Sol high remains pinned'}
            except (httpx.HTTPError, ValueError, OSError):
                pass
        result['laya'] = advice
        runner = _runner or SubscriptionModels(folder, router=SolOnlyRouter())
        compiler = _compiler or compile_resume
        data = {'source': context, 'job_description': snapshot['text'],
                'skills': [s['term'] for s in catalog(source)]}
        images = []
        check = SelfCheck(ready=False, issues=['Not checked yet'])
        for attempt in range(4):
            draft = runner.call('tailor' if attempt == 0 else 'revise', DRAFT_INSTRUCTIONS,
                                data, Draft, images=images)
            selected = draft.selected_bullet_ids
            if len({edit.bullet_id for edit in draft.edits}) != len(draft.edits):
                raise ValueError('Duplicate bullet edits')
            validate_ranking(draft.ranked_skills, catalog(source))
            validate_section_priority(context, selected, final=True)
            replacements = {edit.bullet_id: edit.text for edit in draft.edits}
            tex = render_source(source, selected, replacements, draft.layout, focus_skills=True,
                                job_description=snapshot['text'], skill_lines=draft.skill_lines,
                                ranked_skills=draft.ranked_skills)
            trial = folder / ('render-' + str(attempt))
            trial.mkdir(exist_ok=True)
            (trial / 'resume.tex').write_text(tex, encoding='utf-8')
            (trial / 'selection.json').write_text(draft.model_dump_json(indent=2), encoding='utf-8')
            (trial / 'INTERVIEW_PREP.md').write_text(
                '# Things to know before the interview\n\n'
                + f'Posting: {url}\n\nMaster version: {master["sha256"]}\n\n'
                + f'JD snapshot: {snapshot_id}\n\n' + draft.interview_notes + '\n', encoding='utf-8')
            qa, images = compiler(trial)
            feedback = layout_feedback(qa, context, selected)
            check = SelfCheck(ready=False, issues=feedback['issues'])
            if qa['passed']:
                if not images:
                    raise ValueError('Page images are required for the Sol visual self-check')
                check = runner.call('final_review', CHECK_INSTRUCTIONS,
                                    {'source': context, 'job_description': snapshot['text'],
                                     'draft': draft.model_dump(),
                                     'source_contact_tex': contact.group(1) if contact else '',
                                     'verified_skills': catalog(source),
                                     'current_layout': feedback,
                                     'pdf_text': '\n'.join(p['text'] for p in qa['pages'])},
                                    SelfCheck, images=images)
            (trial / 'self-check.json').write_text(check.model_dump_json(indent=2), encoding='utf-8')
            if qa['passed'] and check.ready and not check.issues:
                # Recheck user edits and protected source before announcing the finished draft.
                _, current_tracking = _guard(job_id)
                if load_master()[1]['sha256'] != master['sha256']:
                    raise ValueError('Master version changed while tailoring')
                result.update(state='MOCKED' if mocked else 'READY',
                              pdf=str(trial / 'resume.pdf'), source=str(trial / 'resume.tex'),
                              interview_notes=str(trial / 'INTERVIEW_PREP.md'),
                              selection=resume_selection_summary(context, selected),
                              selection_reason=draft.selection_reason, layout=feedback,
                              self_check='Sol self-check passed; not independent approval')
                result['hashes'] = {str(trial / name): sha(trial / name)
                                    for name in ('resume.pdf', 'resume.tex', 'selection.json', 'self-check.json')}
                if not mocked:
                    note = (f"Tailored with Sol high: {result['pdf']} | {result['source']}"
                            + f"\nThings to know before interview: {result['interview_notes']}")
                    update_job_tracking(job_id, status='DRAFTED',
                                        notes=(current_tracking['notes'] + '\n' + note).strip())
                break
            data.update(previous_draft=draft.model_dump(), layout_feedback=feedback,
                        self_check_issues=check.issues)
        else:
            result.update(state='NEEDS_ATTENTION', issues=check.issues,
                          note='No ready alert: layout or Sol self-check still failed after three repairs')
        (folder / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        set_setting(key, result)
        return result
    except Exception:
        result.update(state='FAILED', note='Inspect this folder and model-call failure receipts; no automatic duplicate attempt')
        set_setting(key, result)
        (folder / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        raise
