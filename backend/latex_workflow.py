"""Subscription-backed resume packets; supervised handoff, never a live browser worker."""
import json
import re
import shutil
import time
from pathlib import Path
from typing import Literal
from pydantic import Field
from .db import ROOT, connect, digest, get_row, packed, uid
from .application_history import (BLOCKED_STATES, canonical, history_for,
                                  record_application_in_transaction)
from .models import StrictModel
from .latex_source import load_master, source_context, render_source, sha
from .latex_render import compile_resume
from .subscription_models import SubscriptionModels
from .model_routing import TASKS, TaskRouter, routing_configuration
from .resume_style import BULLET_STYLE
from .resume_skills import POLICY as SKILLS_POLICY, catalog as skills_catalog, plan_skills, realized_plan, rank_with_model
from .tiering import route_for

ARCHIVE = ROOT / 'job apps'
MAX_LAYOUT_REPAIRS = 6


class Edit(StrictModel):
    bullet_id: str
    alternatives: list[str] = Field(min_length=3, max_length=3)


class Tailoring(StrictModel):
    selected_bullet_ids: list[str] = Field(min_length=5, max_length=18)
    edits: list[Edit] = Field(max_length=4)


class Score(StrictModel):
    candidate_id: str
    support: Literal['SUPPORTED', 'UNSUPPORTED', 'UNCLEAR']
    relevance: int = Field(ge=0, le=100)
    clarity: int = Field(ge=0, le=100)
    specificity: int = Field(ge=0, le=100)
    concision: int = Field(ge=0, le=100)
    reason: str = Field(max_length=500)

    @property
    def total(self):
        return self.relevance*.4 + self.clarity*.25 + self.specificity*.2 + self.concision*.15


class SelectionAdvice(StrictModel):
    drop_bullet_ids: list[str] = Field(max_length=5)
    add_bullet_ids: list[str] = Field(max_length=6)
    reason: str = Field(max_length=500)


class Judgment(StrictModel):
    evaluations: list[Score] = Field(max_length=16)
    selection_advice: SelectionAdvice


class RevisedJudgment(Judgment):
    evaluations: list[Score] = Field(max_length=28)


class Revision(StrictModel):
    edits: list[Edit] = Field(min_length=1, max_length=4)


class FitPlan(StrictModel):
    preset: Literal['standard', 'compact']
    skill_lines: int = Field(ge=1, le=3)
    reason: str = Field(max_length=500)


class Layout(StrictModel):
    drop_bullet_ids: list[str] = Field(max_length=5)
    add_bullet_ids: list[str] = Field(max_length=6)
    preset: Literal['standard', 'compact']
    reason: str = Field(max_length=500)


class FinalReview(StrictModel):
    factual_support: Literal['SUPPORTED', 'UNSUPPORTED', 'UNCLEAR']
    writing_ok: bool
    layout_ok: bool
    job_alignment_ok: bool
    issues: list[str] = Field(max_length=12)


def init_workflow():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS latex_runs(id TEXT PRIMARY KEY, job_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, folder TEXT NOT NULL, state TEXT NOT NULL, manifest_hash TEXT, error TEXT, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS latex_pair_approvals(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, binding TEXT NOT NULL, identity TEXT NOT NULL, evidence TEXT NOT NULL, expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
        ''')


def master_status():
    source, metadata = load_master()
    context = source_context(source)
    return {**metadata, 'path': str(ROOT/'master resume/master.tex'), 'entries': len(context['entries']),
            'bullets': len(context['bullets']),
            'model_routing': routing_configuration().model_dump(),
            'billing': 'configured CLI subscription; provider quotas apply',
            'max_model_calls_per_job': {'standard': 14, 'premium': 14}, 'max_edited_bullets': 4,
            'max_wording_regenerations': 1,
            'max_layout_repairs': MAX_LAYOUT_REPAIRS,
            'minimum_vertical_fill': 0.94, 'max_layout_additions': 6, 'max_selected_bullets': 18,
            'content_priority': 'Select experience, design teams, and one or two projects for JD relevance; no team is mandatory',
            'live_browser_execution': False}


def latest_snapshot(c, job_id):
    row = c.execute('SELECT * FROM snapshots WHERE job_id=? ORDER BY created DESC LIMIT 1', (job_id,)).fetchone()
    if not row or row['provenance'] == 'SYNTHETIC':
        raise ValueError('Save the official job description before preparing a real resume')
    return dict(row)


def candidates_for(proposal, context):
    bullets = {b['id']: b for b in context['bullets']}
    selected = proposal.selected_bullet_ids
    if len(selected) != len(set(selected)) or set(selected)-bullets.keys():
        raise ValueError('Selection contains unknown/duplicate master bullets')
    if len({e.bullet_id for e in proposal.edits}) != len(proposal.edits):
        raise ValueError('Duplicate edit target')
    result = []
    for edit in proposal.edits:
        if edit.bullet_id not in selected:
            raise ValueError('Edited bullet is not selected')
        original = bullets[edit.bullet_id]['text']
        alternatives = [s.strip() for s in edit.alternatives]
        if len(set([original, *alternatives])) != 4 or any(not 10 <= len(s) <= 600 for s in alternatives):
            raise ValueError('Three distinct bounded alternatives plus original are required')
        for n, text in enumerate([original, *alternatives]):
            result.append({'candidate_id': f'{edit.bullet_id}-{n}', 'bullet_id': edit.bullet_id,
                           'text': text, 'original': original, 'entry_id': bullets[edit.bullet_id]['entry_id']})
    return result


def choose(candidates, judgments, source_evidence=None):
    scores = {e.candidate_id: e for e in judgments.evaluations}
    if len(scores) != len(judgments.evaluations) or set(scores) != {c['candidate_id'] for c in candidates}:
        raise ValueError('Independent judge did not evaluate every candidate exactly once')
    replacements, decisions = {}, []
    for bullet in dict.fromkeys(c['bullet_id'] for c in candidates):
        options = [c for c in candidates if c['bullet_id'] == bullet]
        original = options[0]
        evidence = original['text'] + ' ' + (source_evidence or {}).get(bullet, '')
        numbers = set(re.findall(r'\d+(?:[.,]\d+)?', evidence))
        eligible = []
        for c in options:
            score = scores[c['candidate_id']]
            safe = not (set(re.findall(r'\d+(?:[.,]\d+)?', c['text']))-numbers)
            safe = safe and not any(ord(ch) < 32 for ch in c['text'])
            if safe and score.support == 'SUPPORTED':
                eligible.append(c)
        if original not in eligible:
            raise ValueError('Original has unresolved factual support; human review needed')
        winner = max(eligible, key=lambda c: scores[c['candidate_id']].total)
        if scores[winner['candidate_id']].total < scores[original['candidate_id']].total+5:
            winner = original
        if winner != original:
            replacements[bullet] = winner['text']
        decisions.append({'bullet_id': bullet, 'selected': winner['candidate_id'],
                          'original': original['text'], 'text': winner['text']})
    return replacements, decisions


def apply_selection_advice(context, selected, replacements, advice):
    """Apply reviewed source-only changes, never new wording or unknown claims."""
    known = {b['id'] for b in context['bullets']}
    drops, adds = advice.drop_bullet_ids, advice.add_bullet_ids
    if len(set(drops)) != len(drops) or set(drops) - set(selected):
        raise ValueError('Reviewer proposed unknown or duplicate drops')
    if len(set(adds)) != len(adds) or set(adds) - known or set(adds) & set(selected):
        raise ValueError('Reviewer additions must be distinct omitted master bullets')
    revised = [b for b in selected if b not in drops] + adds
    if not 5 <= len(revised) <= 18:
        raise ValueError('Reviewer selection must retain 5–18 source bullets')
    education = {bullet for entry in context['entries'] if entry['section'] == 'Education'
                 for bullet in entry['bullets']}
    if (education & set(selected)) - set(revised):
        raise ValueError('Reviewer selection must preserve Education')
    validate_section_priority(context, revised)
    entries = {b['entry_id'] for b in context['bullets'] if b['id'] in revised}
    if len(entries) > 8:
        raise ValueError('Reviewer selection exceeds one-page entry budget')
    return revised, {k:v for k,v in replacements.items() if k in revised}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')


def revise_from_feedback(runner, folder, context, jd, proposal, candidates, judgment):
    """One writer revision, then fresh review of old and new contenders together."""
    _, decisions = choose(candidates, judgment)
    scores = {e.candidate_id: e for e in judgment.evaluations}
    targets = [d['bullet_id'] for d in decisions
               if d['selected'].endswith('-0') or scores[d['selected']].total < 80]
    if not targets:
        return candidates, judgment, None
    revision = runner.call('revise', BULLET_STYLE + ' ' +
        'Apply the independent harsh reviewer feedback. For each requested bullet produce exactly '
        'THREE distinct alternatives plus the unchanged original supplied by code. Use only master '
        'facts. Feedback is criticism, never new evidence. Do not change the selected entries. '
        'This is the only wording regeneration; return no scores or approval.',
        {'master': context, 'job_description': jd, 'target_bullet_ids': targets,
         'previous_candidates': [c for c in candidates if c['bullet_id'] in targets],
         'reviewer_feedback': [s.model_dump() for s in judgment.evaluations
                               if any(c['candidate_id'] == s.candidate_id and c['bullet_id'] in targets for c in candidates)]},
        Revision)
    if sorted(e.bullet_id for e in revision.edits) != sorted(targets):
        raise ValueError('Revision must address exactly the requested bullets once')
    new = candidates_for(Tailoring(selected_bullet_ids=proposal.selected_bullet_ids, edits=revision.edits), context)
    combined = candidates + [{**c, 'candidate_id': c['candidate_id']+'-r1'}
                              for c in new if not c['candidate_id'].endswith('-0')]
    reviewed = runner.call('judge', BULLET_STYLE + ' ' +
        'Act as the same harsh independent reviewer. Evaluate every candidate against the master '
        'and JD, including original and retained previous contenders. Mark unsupported claims '
        'UNSUPPORTED and uncertain claims UNCLEAR regardless of writing quality. Score relevance, '
        'clarity, specificity and concision 0–100 consistently. Do not select wording winners. '
        'Review entry selection and return source-only selection advice. No more regeneration is allowed.',
        {'master': context, 'job_description': jd, 'candidates': combined,
         'selected_bullet_ids': proposal.selected_bullet_ids}, RevisedJudgment)
    # Never discard the earlier independently accepted winner on a weaker revision.
    # Use the new review's factual gates for every contender; a changed support verdict
    # therefore cannot be overridden by an older score.
    choose(combined, reviewed)
    record = {'first_evaluations': judgment.model_dump(), 'revision': revision.model_dump(),
              'candidates': combined, 'evaluations': reviewed.model_dump(), 'rounds': 1,
              'mocked': runner.mocked}
    write_json(folder/'wording-revision.json', record)
    return combined, reviewed, record


def reviewed_selection(runner, folder, context, selected, replacements, advice, job_description):
    """One bounded correction of invalid entry choices; wording scores stay unchanged."""
    try:
        return apply_selection_advice(context, selected, replacements, advice)
    except ValueError as exc:
        validation_error = str(exc)
    write_json(folder/'selection-correction-request.json', {
        'original_advice': advice.model_dump(), 'validation_error': validation_error})
    corrected = runner.call('content_repair',
        'You are the same independent content reviewer. Correct only your invalid source-bullet '
        'selection advice. Do not rewrite any wording or rescore candidates. '
        'The final selection equals selected_bullet_ids minus drop_bullet_ids plus add_bullet_ids. '
        'It MUST contain 5–18 total bullets, at most eight total entries including Education, '
        'and ONE or TWO Personal Projects. Preserve Education. Drop at most five currently selected '
        'bullets and add at most six distinct omitted master bullets. Count the resulting bullets, '
        'entries and projects before returning. If adding a third project, drop every bullet of '
        'an existing project. Include the reaching-task/feedback context when choosing body tracking. '
        'Avoid duplicating rocket-tracking contributions across team and project sections. '
        'Choose the strongest truthful evidence for the JD; no team is mandatory. '
        'Empty changes are valid if no feasible improvement exists. This is the only correction attempt.',
        {'master': context, 'selected_bullet_ids': selected, 'job_description': job_description,
         'original_advice': advice.model_dump(), 'validation_error': validation_error}, SelectionAdvice)
    write_json(folder/'selection-correction.json', corrected.model_dump())
    return apply_selection_advice(context, selected, replacements, corrected)


def validate_section_priority(context, selected, final=False):
    """Keep project count bounded without forcing role-irrelevant teams into a resume."""
    selected = set(selected)
    projects = [e for e in context['entries'] if e['section']=='Personal Projects']
    count = sum(bool(selected.intersection(e['bullets'])) for e in projects)
    if count>2 or (final and projects and count<1):
        raise ValueError('Final resume must contain one or two personal projects')


def resume_selection_summary(context, selected):
    """Small, phone-readable inventory of the entries in one exact resume version."""
    picked = set(selected)
    groups = {'Experience': 'experience', 'Engineering Design Teams': 'design_teams',
              'Personal Projects': 'projects'}
    result = {'experience': [], 'design_teams': [], 'projects': [], 'omitted_design_teams': []}
    for entry in context['entries']:
        group = groups.get(entry['section'])
        if group is None:
            continue
        parts = entry['context'].split(' | ')
        name = parts[2] if group == 'experience' and len(parts) > 2 else parts[0].split(' -- ')[0]
        if picked.intersection(entry['bullets']):
            result[group].append(name)
        elif group == 'design_teams':
            result['omitted_design_teams'].append(name)
    return result


def prune_duplicate_rocket_project(context, proposal):
    """Bounded recovery for a draft that chose three projects including Arbalest twice."""
    selected = set(proposal.selected_bullet_ids)
    projects = [e for e in context['entries'] if e['section'] == 'Personal Projects'
                and selected.intersection(e['bullets'])]
    if len(projects) <= 2:
        return proposal, None
    rocket = [e for e in projects if 'Real-Time Rocket Tracking System -- Arbalest Rocketry' in e['context']]
    arbalest = [e for e in context['entries'] if e['section'] == 'Engineering Design Teams'
                and 'Arbalest Rocketry' in e['context'] and selected.intersection(e['bullets'])]
    if len(projects) != 3 or len(rocket) != 1 or len(arbalest) != 1:
        raise ValueError('Final resume must contain one or two personal projects, prioritizing design teams')
    dropped = set(rocket[0]['bullets']) & selected
    repaired = proposal.model_copy(update={
        'selected_bullet_ids': [b for b in proposal.selected_bullet_ids if b not in dropped],
        'edits': [e for e in proposal.edits if e.bullet_id not in dropped],
    })
    return repaired, {'dropped_bullet_ids': sorted(dropped),
                      'reason': 'Three projects selected; keep Arbalest engineering team and the two non-rocket projects.'}


def layout_feedback(qa, context, selected):
    """Give the reviewer measured failures and the visible entries at the problem area."""
    pages = qa.get('pages', [])
    entries = [e for e in context['entries'] if set(e['bullets']) & set(selected)]
    details = []
    for number, page in enumerate(pages, 1):
        lines = [line.strip() for line in page.get('text', '').splitlines() if line.strip()]
        matched = [e['context'].split(' | ')[0] for e in entries
                   if e['context'].split(' | ')[0].casefold() in page.get('text', '').casefold()]
        details.append({'page': number, 'fill': page.get('vertical_fill_ratio'),
                        'bottom_gap_points': page.get('bottom_gap_points'),
                        'clipped_characters': len(page.get('clipped_characters', [])) if isinstance(page.get('clipped_characters'), list) else page.get('clipped_characters', 0),
                        'overlapping_words': page.get('overlapping_words', 0),
                        'visible_entries': matched,
                        'first_lines': lines[:3] if number > 1 else [],
                        'last_lines': lines[-3:]})
    issues = []
    if qa.get('page_count', len(pages)) != 1:
        issues.append('content spills onto another page' if len(pages) > 1 else 'PDF has no page')
    if qa.get('underfilled') or qa.get('vertical_fill_ratio', 1) < 0.94:
        issues.append('page ends before 94% of printable height')
    if qa.get('overfull_boxes', 0):
        issues.append(f"{qa['overfull_boxes']} overfull TeX boxes")
    if not qa.get('bounds_ok', True):
        issues.append('clipped or overlapping text')
    if not issues and not qa.get('passed'):
        issues.append('one-page layout quality check failed')
    return {'issues': issues, 'page_count': qa.get('page_count', len(pages)),
            'vertical_fill_ratio': qa.get('vertical_fill_ratio'),
            'skill_line_budget': qa.get('skill_line_budget'),
            'overfull_locations': qa.get('overfull_locations', []), 'pages': details}


def apply_layout_plan(source, context, selected, replacements, layout, folder, compiler, qa, images,
                      job_description=None, skill_lines=3, round_index=1, ranked_skills=None):
    """Test one reviewer plan locally and preserve failed-trial diagnostics for the next pass."""
    known = {b['id'] for b in context['bullets']}
    drops, adds = layout.drop_bullet_ids, layout.add_bullet_ids
    if len(set(drops))!=len(drops) or set(drops)-set(selected):
        raise ValueError('Layout attempted an unknown/duplicate deletion')
    if len(set(adds))!=len(adds) or set(adds)-known or set(adds)&set(selected):
        raise ValueError('Layout additions must be distinct omitted master bullets')
    base = [b for b in selected if b not in drops]
    if len(base)<5 or len(base)+len(adds)>18:
        raise ValueError('Layout selection must contain 5–18 bullets')
    validate_section_priority(context, base)
    def valid(q):
        return q.get('technical_passed', q['passed'])
    def fill(q):
        return q.get('vertical_fill_ratio', 1 if q['passed'] else 0)
    best = (selected, folder, qa, images)
    def quality(q):
        ratio = fill(q)
        return (bool(q.get('passed')), valid(q), q.get('page_count', 1) == 1,
                bool(q.get('bounds_ok', True)), -q.get('overfull_boxes', 0),
                -abs(ratio - 0.97))
    attempts = []
    working = base
    # At most one base trial plus six additions. All added text comes verbatim from master.
    for number, addition in enumerate([None, *adds]):
        trial_selection = working + ([addition] if addition else [])
        try:
            validate_section_priority(context, trial_selection)
        except ValueError:
            attempts.append({'selected':trial_selection,'technical_passed':False,'reason':'Section priority would be violated'})
            continue
        trial = folder/'layout trials'/f'round-{round_index:02d}'/f'{number:02d}'
        trial.mkdir(parents=True,exist_ok=True)
        text = render_source(source, trial_selection, {k:v for k,v in replacements.items() if k in trial_selection}, layout.preset,
                             focus_skills=True, job_description=job_description, skill_lines=skill_lines, ranked_skills=ranked_skills)
        (trial/'resume.tex').write_text(text,encoding='utf-8')
        trial_qa, trial_images = compiler(trial)
        attempts.append({'selected':trial_selection,
                         'feedback':layout_feedback(trial_qa, context, trial_selection)})
        if valid(trial_qa) and fill(trial_qa) <= 1.0:
            working = trial_selection
        if quality(trial_qa) > quality(best[2]):
            best = (trial_selection, trial, trial_qa, trial_images)
        # Overflowing additions are skipped; try the next, potentially shorter source bullet.
    write_json(folder/f'layout-decision-{round_index:02d}.json',
               {**layout.model_dump(), 'attempts':attempts,
                'chosen_feedback':layout_feedback(best[2], context, best[0])})
    selected, chosen, qa, images = best
    if chosen != folder:
        for path in chosen.iterdir():
            if path.is_file():
                shutil.copy2(path, folder/path.name)
        images = [folder/path.name for path in images]
    return selected, {k:v for k,v in replacements.items() if k in selected}, qa, images


def prepare_latex_application(job_id, answers=None, *, _runner=None, _compiler=None):
    # Injectable dependencies are Python-only; JSON dispatch never exposes these arguments.
    init_workflow()
    source, master = load_master()
    job = get_row('jobs', job_id)
    original_destination = job['url']
    answers = {} if answers is None else answers
    if not isinstance(answers, dict) or len(answers)>30 or any(not isinstance(k,str) or not isinstance(v,str) or len(k)>100 or len(v)>2000 for k,v in answers.items()):
        raise ValueError('Application answers must be a bounded string mapping supplied by the user')
    with connect() as c:
        job['url'] = canonical(c, job['url'])
        history = history_for(c, job['url'])
        if history and history['state'] in BLOCKED_STATES:
            raise ValueError('Application history blocks another preparation: '+history['state'])
        snapshot = latest_snapshot(c, job_id)
    config = routing_configuration()
    routing = {**route_for(job, snapshot), 'review_strategy': 'pinned-reviewer-routed-writer-v1',
               'configuration': config.model_dump(),
               'skills': {'engine': 'model', 'model': config.skills.model, 'max_model_calls': 1}}
    active_roles = {key: getattr(config, TASKS[key][1]).model_dump() for key in
                    ['skills', 'tailor', 'revise', 'judge', 'layout', 'content_repair', 'final_review']}
    context = source_context(source)
    context['skills_catalog'] = skills_catalog(source)
    run_key = [job_id, snapshot['id'], master['sha256'], answers, active_roles, routing,
               BULLET_STYLE, SKILLS_POLICY, 'latex-policy-v11-routed-review-revise',
               Judgment.model_json_schema(), 'bounded-selection-correction-v1']
    if job['url'] != original_destination:
        # Bind resolved destinations without invalidating unchanged existing packets.
        run_key.append(job['url'])
    run_id = digest(run_key)[:24]
    slug = re.sub(r'[^a-zA-Z0-9-]+', '-', f'{job["company"]}-{job["title"]}').strip('-')[:80] or 'application'
    folder = ARCHIVE / (slug+'-'+job_id[:8]) / run_id
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute('SELECT * FROM latex_runs WHERE id=?', (run_id,)).fetchone()
        if old:
            if old['state'] == 'PREPARING':
                raise ValueError('Preparation is already running; do not launch a duplicate')
            if old['state'] == 'FAILED':
                raise ValueError('Previous preparation failed; inspect its archive before an explicit recovery')
        else:
            c.execute('INSERT INTO latex_runs VALUES (?,?,?,?,?,?,?,?)', (run_id, job_id, snapshot['id'], str(folder), 'PREPARING', None, None, time.time()))
    if old:
        return application_pair(run_id)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder/'posting.txt').write_text(job['url']+'\n', encoding='utf-8')
        (folder/'job-description.txt').write_text(snapshot['text'], encoding='utf-8')
        write_json(folder/'answers.json', answers)
        write_json(folder/'source.json', {'master': master, 'job': job, 'snapshot': snapshot})
        write_json(folder/'routing.json', routing)
        runner = _runner or SubscriptionModels(folder, router=TaskRouter(config))
        compiler = _compiler or compile_resume
        tailor_data = {'master': context, 'job_description': snapshot['text']}
        proposal = runner.call('tailor', BULLET_STYLE + ' ' +
            'Select 5–18 source bullets for a readable, substantially FULL ONE PAGE internship resume. '
            'Target 94–100% of the printable page height with normal margins, type size and spacing. '
            'Preserve Education; allocate room to substantive evidence first. Code fills up to three Skills '
            'lines with remaining source-backed skills, ranking explicit JD matches before related skills '
            'even when not named in the JD. Do not reserve empty skills space or drop experience to add skills. '
            'Choose at most 7 other entries, prioritizing relevant recent employment. '
            'Select each Engineering Design Team only when its actual work supports this JD; '
            'do not preserve a team merely because it appears in the master. For roles with little '
            'embedded-systems work, consider omitting Lassonde Motorsports and using more relevant '
            'experience such as PharmShift. Include ONE or TWO relevant personal projects. '
            'If selecting the body-tracking project, include its source bullet explaining the reaching task '
            'and feedback modes so controller and calibration details have context. '
            'For a long platform bullet with a reported time reduction, propose shorter alternatives that '
            'preserve who reported it, the per-manual basis, and the rollout scope. '
            'Avoid repeating the same rocket-tracking contribution across sections. Use additional relevant experience '
            'to fill available room. Avoid padding, irrelevant content, or inflated claims. For up to FOUR selected bullets that '
            'benefit from tailoring, propose exactly THREE distinct concise alternatives, typically 20–35 words. '
            'Keep the source factual meaning and ownership. Return no scores or rationale.',
            tailor_data, Tailoring)
        proposal, selection_repair = prune_duplicate_rocket_project(context, proposal)
        if selection_repair:
            write_json(folder/'selection-repair.json', selection_repair)
        selected = proposal.selected_bullet_ids
        validate_section_priority(context, selected)
        chosen_entries = {b['entry_id'] for b in context['bullets'] if b['id'] in selected}
        if len(chosen_entries)>8:
            raise ValueError('Selection exceeds one-page entry budget')
        candidates = candidates_for(proposal, context)
        judgment = runner.call('judge', BULLET_STYLE + ' ' +
            'You are the ONE harsh content reviewer for this job. Do not reward keyword stuffing, '
            'vague accomplishments or inflated ownership. Independently evaluate every wording candidate '
            'against its original source and entry context; score the original and all three alternatives. '
            'SUPPORTED requires every claim to be entailed. Mark exaggeration UNSUPPORTED and ambiguity UNCLEAR. '
            'Score relevance, clarity, specificity, concision 0–100 with the same standards. Do not choose wording winners; code does that. '
            'Also review ALL selected versus omitted master bullets for this JD. Recommend up to five source-bullet drops '
            'and six verbatim source-bullet additions only when they materially improve JD fit and high-value candidate signal. '
            'Compare projects on demonstrated work, not only their titles: product-data collection is not the same as analysis; '
            'a controlled classifier comparison may demonstrate statistical method even when its accuracy is modest; '
            'rocket tracking can duplicate an Arbalest team entry. Do not conceal honest results or add unsupported capabilities. '
            'Use an empty selection change when the draft already has the best supported evidence. '
            'Explain the tradeoff briefly in selection_advice.reason.',
            {'candidates': candidates, 'selected_bullet_ids': selected, 'master': context,
             'job_description': snapshot['text']}, Judgment)
        candidates, judgment, revision = revise_from_feedback(
            runner, folder, context, snapshot['text'], proposal, candidates, judgment)
        replacements, decisions = choose(candidates, judgment)
        before_advice = selected[:]
        selected, replacements = reviewed_selection(runner, folder, context, selected, replacements,
                                                    judgment.selection_advice, snapshot['text'])
        write_json(folder/'candidate-review.json', {'proposal': proposal.model_dump(), 'evaluations': judgment.model_dump(),
                                                    'decisions': decisions, 'selection_before_advice': before_advice,
                                                    'selection_after_advice': selected, 'wording_regenerations': int(revision is not None),
                                                    'mocked': runner.mocked})
        ranked_skills = rank_with_model(runner, source, snapshot['text'])
        write_json(folder/'skills-ranking.json', {'ranked_terms': ranked_skills, 'model': config.skills.model, 'mocked': runner.mocked})
        (folder/'resume.tex').write_text(render_source(source, selected, replacements, focus_skills=True, job_description=snapshot['text'], ranked_skills=ranked_skills), encoding='utf-8')
        qa, images = compiler(folder)
        if not qa['passed']:
            fit = runner.call('layout',
                'You are the small physical-fit specialist. Keep the exact selected bullets and wording. '
                'Choose only a standard or compact margin preset and one to three Skills lines to fit a readable, '
                'well-filled one-page PDF (94–100% printable height). Do not decide which content to remove or add; '
                'the pinned independent reviewer owns JD-fit and content choices. Prefer standard margins for an underfilled page. '
                'Do not use tiny type, inflated whitespace, or unsupported text.',
                {'selected_bullet_ids': selected, 'layout': qa}, FitPlan)
            write_json(folder/'fit-decision.json', {'plan': fit.model_dump(), 'initial_layout': qa})
            trial = folder/'fit trial'
            trial.mkdir()
            (trial/'resume.tex').write_text(render_source(
                source, selected, replacements, fit.preset, focus_skills=True,
                job_description=snapshot['text'], skill_lines=fit.skill_lines, ranked_skills=ranked_skills), encoding='utf-8')
            qa, fit_images = compiler(trial)
            for path in trial.iterdir():
                if path.is_file():
                    shutil.copy2(path, folder/path.name)
            images = [folder/path.name for path in fit_images]
            repair_history = []
            attempted_repairs = set()
            for round_index in range(1, MAX_LAYOUT_REPAIRS + 1):
                if qa['passed']:
                    break
                feedback = layout_feedback(qa, context, selected)
                layout = runner.call('content_repair', BULLET_STYLE + ' ' +
                    'You are the SAME pinned independent content reviewer that assessed the candidates. '
                    'The small fit model did not produce a readable, well-filled one-page PDF. '
                    'Use the measured feedback, visible entry titles, and page-edge lines to identify which '
                    'section or bullet is causing overflow, clipping, or a sparse ending. Review prior repair '
                    'attempts and choose a different source-backed adjustment when one failed. '
                    'Decide up to five selected source bullets to remove and up to six omitted verbatim master '
                    'bullets to add, ranking JD fit and high-value candidate signal above low-value filler. '
                    'For a new project prefer coherent adjacent bullets. Compare Quizzle, the e-commerce chatbot, '
                    'and rocket tracking on actual analysis, honest metrics, and duplication with Arbalest. '
                    'Never invent or rewrite facts. Keep one or two personal projects and 5–18 total bullets. '
                    'Return the physical preset supplied by the fit model unchanged. Code will render your plan '
                    'and send you specific failure feedback again if it still does not fit.',
                    {'master': context, 'selected_bullet_ids': selected,
                     'rendered_text': '\n'.join(p.get('text', '') for p in qa.get('pages', [])),
                     'job_description': snapshot['text'], 'layout_feedback': feedback,
                     'physical_fit': fit.model_dump(), 'prior_repairs': repair_history[-2:]},
                    Layout, images)
                write_json(folder/f'content-repair-{round_index:02d}.json',
                           {'plan':layout.model_dump(), 'feedback':feedback})
                layout = layout.model_copy(update={'preset': fit.preset})
                repair_key = digest([selected, feedback, layout.model_dump()])
                if repair_key in attempted_repairs:
                    raise ValueError('Reviewer repeated a repair for unchanged layout; inspect recorded feedback')
                attempted_repairs.add(repair_key)
                selected, replacements, qa, images = apply_layout_plan(
                    source, context, selected, replacements, layout, folder, compiler, qa,
                    images, snapshot['text'], skill_lines=fit.skill_lines,
                    round_index=round_index, ranked_skills=ranked_skills)
                decision = json.loads((folder/f'layout-decision-{round_index:02d}.json').read_text(encoding='utf-8'))
                repair_history.append({'round':round_index, 'plan':layout.model_dump(),
                                       'outcome':layout_feedback(qa, context, selected),
                                       'trials':decision['attempts']})
        if not qa['passed']:
            raise ValueError(f'One-page layout remains underfilled or unsafe after {MAX_LAYOUT_REPAIRS} reviewer repairs; inspect recorded feedback')
        validate_section_priority(context, selected, final=True)
        skills_plan = realized_plan(plan_skills(source, selected, replacements, snapshot['text'], ranked_skills), qa)
        write_json(folder/'skills-selection.json', skills_plan)
        final = runner.call('final_review', BULLET_STYLE + ' ' +
            'As the SAME pinned harsh content reviewer, independently review the FINAL rendered one-page '
            'resume using its page image and extracted text. Maximize truthful JD fit and the signal of a '
            'high-value candidate; compare selected projects with omitted master evidence instead of assuming '
            'a product-data feature proves analytics or a modest honest metric makes a study worthless. '
            'Compare ALL claims, dates, identity, metrics and responsibilities to the master; assess job alignment, '
            'writing, omissions that misrepresent scope, repetition, readable layout and overlap. Any unresolved '
            'factual claim must be UNCLEAR/UNSUPPORTED. Set layout_ok false for clipping/overlap/tiny text, '
            'excessive blank space, or spacing inflated to disguise a lack of content. '
            'Check that any selected engineering design team is relevant to the JD and that one or two '
            'personal projects are included. Check that team and project bullets do not duplicate the same contribution. '
            'Skills should fill the available row width using master-supported terms missing elsewhere: '
            'explicit JD matches first, then related skills even if the JD does not name them. Do not '
            'penalize supported related skills for lacking an exact JD match. Reject unsupported skills. '
            'Substantive sections have priority over additional Skills rows. Technologies and bullet text '
            'use regular weight consistently; bold is reserved for structural headings. '
            'Return issues, not a rewrite. This review grants no permission to disclose or apply.',
            {'master': context, 'protected_source': source[source.index(r'\begin{document}'):],
             'job_description': snapshot['text'], 'rendered_text': '\n'.join(p['text'] for p in qa['pages'])}, FinalReview, images)
        write_json(folder/'final-review.json', {**final.model_dump(), 'mocked': runner.mocked, 'role': active_roles['final_review']})
        approved = final.factual_support == 'SUPPORTED' and final.writing_ok and final.layout_ok and final.job_alignment_ok and not final.issues
        if not approved:
            raise ValueError('Independent final review did not pass; see final-review.json')
        artifacts = ['resume.pdf','resume.tex','job-description.txt','posting.txt','answers.json',
                     'candidate-review.json','final-review.json','source.json','skills-selection.json','skills-ranking.json','routing.json']
        route_files = sorted((folder/'model calls'/'routes').glob('*.json'))
        routing['decisions'] = [json.loads(p.read_text(encoding='utf-8'))['route'] for p in route_files]
        routing['mocked'] = runner.mocked
        write_json(folder/'routing.json', routing)
        for optional in ('fit-decision.json', 'wording-revision.json'):
            if (folder/optional).exists():
                artifacts.append(optional)
        for optional in ('selection-correction-request.json', 'selection-correction.json'):
            if (folder/optional).exists():
                artifacts.append(optional)
        artifacts.extend(p.name for p in sorted(folder.glob('content-repair-*.json')))
        artifacts.extend(p.name for p in sorted(folder.glob('layout-decision-*.json')))
        manifest = {'run_id': run_id, 'job_id': job_id, 'snapshot_id': snapshot['id'], 'snapshot_hash': snapshot['hash'],
                    'destination': job['url'], 'master_sha256': master['sha256'], 'identity': master['identity'],
                    'models': active_roles, 'routing': routing, 'selected_bullet_ids': selected,
                    'skills':skills_plan, 'mocked': runner.mocked,
                    'files': {name: sha(folder/name) for name in artifacts},
                    'live_execution': False}
        write_json(folder/'manifest.json', manifest)
        with connect() as c:
            c.execute('UPDATE latex_runs SET state=?,manifest_hash=? WHERE id=?', ('READY_FOR_REVIEW', digest(manifest), run_id))
        return application_pair(run_id)
    except Exception as exc:
        with connect() as c:
            c.execute('UPDATE latex_runs SET state=?,error=? WHERE id=?', ('FAILED', str(exc)[:1000], run_id))
        raise


def checked_run(run_id, c):
    row = c.execute('SELECT * FROM latex_runs WHERE id=?', (run_id,)).fetchone()
    if not row or not row['manifest_hash']:
        raise ValueError('No reviewed application packet exists')
    folder = Path(row['folder'])
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if digest(manifest) != row['manifest_hash'] or any(sha(folder/name) != expected for name,expected in manifest['files'].items()):
        raise ValueError('Application artifacts changed; approval invalidated')
    _, master = load_master()
    if master['sha256'] != manifest['master_sha256']:
        raise ValueError('Master version changed')
    snapshot = latest_snapshot(c, row['job_id'])
    if snapshot['id'] != manifest['snapshot_id'] or snapshot['hash'] != manifest['snapshot_hash']:
        raise ValueError('Job description changed; prepare and review again')
    if canonical(c, manifest['destination']) != manifest['destination']:
        raise ValueError('Application destination changed; review the resolved URL first')
    return dict(row), manifest


def application_pair(run_id):
    init_workflow()
    with connect() as c:
        row, manifest = checked_run(run_id, c)
    folder = Path(row['folder'])
    source, _ = load_master()
    selection_summary = manifest.get('resume_selection_summary') or resume_selection_summary(source_context(source), manifest['selected_bullet_ids'])
    return {'run_id': run_id, 'state': row['state'], 'resume_pdf': str(folder/'resume.pdf'),
            'preview': str(next(folder.glob('page-*.png'), folder/'resume.pdf')),
            'job_description': str(folder/'job-description.txt'), 'posting_url': manifest['destination'],
            'answers': json.loads((folder/'answers.json').read_text(encoding='utf-8')),
            'folder': str(folder), 'mocked': manifest['mocked'],
            'resume_selection_summary': selection_summary,
            'routing': manifest.get('routing'), 'live_browser_execution': False,
            'next_step': 'Show this exact resume–JD pair for approval. After approval, Apply authorizes supervised preparation of this packet. Final submission requires fresh action-time confirmation.'}


def approve_latex_pair(run_id, evidence):
    """Conversation approval bookkeeping, not an authenticated human approval plane."""
    if not isinstance(evidence,str) or not evidence.strip():
        raise ValueError('Record the actual user approval message/reference')
    init_workflow()
    with connect() as c:
        row, manifest = checked_run(run_id, c)
        if manifest['mocked']:
            raise ValueError('Mock-reviewed packets cannot be approved for real disclosure')
        approval = uid()
        c.execute('INSERT INTO latex_pair_approvals VALUES (?,?,?,?,?,?,0)',
                  (approval,run_id,row['manifest_hash'],manifest['identity'],evidence,time.time()+86400))
    return {'approval_id': approval, 'run_id': run_id, 'expires_in_hours': 24, 'action': 'pair_review',
            'note': 'Content approval recorded. Await an explicit Apply instruction; no data has been disclosed.'}


def begin_latex_application(run_id, approval_id, apply_evidence):
    if not isinstance(apply_evidence,str) or not apply_evidence.strip():
        raise ValueError('Record the user Apply instruction for this exact packet')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row, manifest = checked_run(run_id, c)
        a = c.execute('SELECT * FROM latex_pair_approvals WHERE id=?', (approval_id,)).fetchone()
        if not a or a['run_id']!=run_id or a['binding']!=row['manifest_hash'] or a['identity']!=manifest['identity'] or a['used'] or a['expires']<=time.time():
            raise ValueError('Pair approval missing, stale, expired or already consumed')
        target = canonical(c, manifest['destination'])
        history = history_for(c, target)
        if history and history['state'] in BLOCKED_STATES:
            raise ValueError('Application already claimed/applied/unknown/skipped')
        c.execute('UPDATE latex_pair_approvals SET used=1 WHERE id=?', (approval_id,))
        record_application_in_transaction(c, target, 'IN_PROGRESS', apply_evidence)
        c.execute("UPDATE latex_runs SET state='IN_PROGRESS' WHERE id=?", (run_id,))
    write_json(Path(row['folder'])/'approval.json', {'pair_approval':dict(a), 'apply_evidence':apply_evidence, 'binding':row['manifest_hash'], 'disclosure_scope':'Exact resume and saved answers at the recorded destination only', 'submit_authorized':False})
    return {**application_pair(run_id), 'next_step':'Use supervised Computer Use. Stop for new answers/destinations or account challenges. Obtain fresh final submission confirmation before the single Submit action.'}


def archive_application_result(run_id, state, evidence):
    if state not in {'APPLIED','SUBMISSION_UNKNOWN'}:
        raise ValueError('Archive accepts APPLIED or SUBMISSION_UNKNOWN')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM latex_runs WHERE id=?', (run_id,)).fetchone()
        if not row or row['state'] not in {'IN_PROGRESS','SUBMISSION_UNKNOWN','APPLIED'}:
            raise ValueError('No supervised application attempt exists')
        # Preserve receipt even if a posting has since changed; used files remain in this archive.
        manifest = json.loads((Path(row['folder'])/'manifest.json').read_text(encoding='utf-8'))
        if digest(manifest)!=row['manifest_hash'] or any(sha(Path(row['folder'])/name)!=expected for name,expected in manifest['files'].items()):
            raise ValueError('Applied artifacts changed; reconcile manually')
        result = record_application_in_transaction(c, manifest['destination'], state, evidence)
        c.execute('UPDATE latex_runs SET state=? WHERE id=?', (state,run_id))
    write_json(Path(row['folder'])/'receipt.json', {**result,'recorded':time.time(),'run_id':run_id,'manifest_hash':row['manifest_hash'],'evidence':evidence})
    return {**result, 'folder':row['folder']}
