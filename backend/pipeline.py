import json
import re
import time
from .db import audit, connect, digest, get_row, packed, uid
from .models import Candidate, Profile
from .providers import ProviderError, providers

MARGIN = 5.0  # Calibration starting point, not an established hiring metric.


def gate(candidate, bullet, profile):
    if candidate.target_bullet_id != bullet.id:
        return 'Protected target changed'
    if set(candidate.source_fact_ids) - set(bullet.source_fact_ids):
        return 'Unknown or out-of-scope evidence reference'
    if set(candidate.job_requirement_ids) - {'r1'}:
        return 'Unknown requirement reference'
    evidence = ' '.join(f.text for f in profile.facts if f.id in candidate.source_fact_ids)
    numbers = set(re.findall(r'\d+(?:[.,]\d+)?%?', candidate.replacement_text))
    if numbers - set(re.findall(r'\d+(?:[.,]\d+)?%?', evidence)):
        return 'Unsupported numeric claim'
    if any(ord(c) < 32 and c not in '\n\t' for c in candidate.replacement_text):
        return 'Control characters rejected'
    return None


def optimize(profile, requirements, generator, judge, regeneration_rounds=0):
    if regeneration_rounds not in (0, 1):
        raise ValueError('At most one regeneration round')
    decisions = []
    selected_texts = []
    for bullet in profile.bullets:
        original = Candidate(target_bullet_id=bullet.id, candidate_id=bullet.id+'-original', replacement_text=bullet.text,
                             source_fact_ids=bullet.source_fact_ids, job_requirement_ids=['r1'])
        candidates = [original]
        for round_no in range(regeneration_rounds+1):
            alternatives = generator.propose(bullet, profile, requirements)
            if len(alternatives) != 3 or len({a.replacement_text for a in alternatives}) != 3:
                raise ProviderError('Generator must propose three distinct alternatives')
            if any(a.replacement_text == bullet.text for a in alternatives):
                raise ProviderError('Alternative duplicates the original')
            for i, a in enumerate(alternatives):
                a.candidate_id = f'{bullet.id}-r{round_no}-{i+1}'
            candidates.extend(alternatives)
        evaluations = []
        for candidate in candidates:
            reason = gate(candidate, bullet, profile)
            evaluation = judge.evaluate(candidate, profile, requirements)
            evaluations.append({**candidate.model_dump(), 'evaluation': evaluation.model_dump(), 'score': evaluation.score,
                                'eligible': reason is None and evaluation.support == 'SUPPORTED', 'gate_reason': reason})
        baseline = evaluations[0]
        eligible = [e for e in evaluations[1:] if e['eligible']]
        winner = baseline
        reason = 'Original retained: no clear, supported improvement.'
        if baseline['eligible'] and eligible:
            best = max(eligible, key=lambda e: e['score'])
            if best['score'] >= baseline['score'] + MARGIN:
                winner = best
                reason = f'Supported improvement exceeds the {MARGIN:g}-point margin.'
        decisions.append({'bullet_id': bullet.id, 'candidates': evaluations, 'selected': winner['candidate_id'], 'reason': reason})
        selected_texts.append(winner['replacement_text'])
    review = judge.review(selected_texts, profile)
    if any(not next(c for c in d['candidates'] if c['candidate_id'] == d['selected'])['eligible'] for d in decisions):
        review = {**review, 'approved': False, 'reason': 'Selected original has unresolved factual support.'}
    return {'decisions': decisions, 'selected_texts': selected_texts, 'review': review, 'margin': MARGIN,
            'generator': generator.name, 'judge': judge.name, 'rubric': 'v1: relevance .40, clarity .25, specificity .20, concision .15'}


def enqueue(job_id, profile_id):
    job, profile = get_row('jobs', job_id), get_row('profiles', profile_id)
    if not profile['verified']:
        raise ValueError('Verify imported facts before preparing a packet')
    with connect() as c:
        snapshot = c.execute('SELECT * FROM snapshots WHERE job_id=? ORDER BY created DESC LIMIT 1', (job_id,)).fetchone()
        if not snapshot:
            raise ValueError('Add the official posting text, or use the labeled synthetic practice snapshot')
        if not profile['synthetic'] and snapshot['provenance'] == 'SYNTHETIC':
            raise ValueError('Real profiles require an official posting snapshot')
        from .providers import provider_configuration
        import os
        key = digest([job['material_hash'], profile['hash'], snapshot['hash'], os.getenv('PROVIDER_MODE', 'demo'), provider_configuration()])
        existing = c.execute('SELECT id,status FROM tasks WHERE dedup=?', (key,)).fetchone()
        if existing:
            # Preparation is read-only externally; explicit retry is safe.
            if existing['status'] == 'FAILED':
                c.execute("UPDATE tasks SET status='QUEUED',error=NULL,updated=? WHERE id=?", (time.time(), existing['id']))
            return existing['id']
        task = uid()
        now = time.time()
        c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)', (task, job_id, profile_id, snapshot['id'], key, 'QUEUED', None, now, now))
        return task


def work_once():
    from .db import setting
    if setting('worker_paused', False):
        return False
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        # Only preparation leases are reclaimable. Submission has no automatic retry.
        c.execute("UPDATE tasks SET status='FAILED',error='Preparation interrupted; retry explicitly' WHERE status='RUNNING' AND updated<?", (time.time()-1800,))
        task = c.execute("SELECT * FROM tasks WHERE status='QUEUED' ORDER BY created LIMIT 1").fetchone()
        if not task:
            return False
        task = dict(task)
        c.execute("UPDATE tasks SET status='RUNNING',updated=? WHERE id=?", (time.time(), task['id']))
    try:
        job = get_row('jobs', task['job_id'])
        pr = get_row('profiles', task['profile_id'])
        snapshot = get_row('snapshots', task['snapshot_id'])
        profile = Profile.model_validate_json(pr['content'])
        generator, judge = providers(pr['id'], pr['synthetic'])
        result = optimize(profile, snapshot['text'], generator, judge)
        payload = {'job': job, 'profile_id': pr['id'], 'profile_hash': pr['hash'], 'synthetic': bool(pr['synthetic']),
                   'snapshot': snapshot, 'name': profile.name, 'contact': profile.contact, 'heading': profile.heading,
                   'answers': profile.answers, 'facts': [f.model_dump() for f in profile.facts], **result,
                   'eligibility': 'NEEDS_CLARIFICATION',
                   'missing_answers': [q for q in ('work_authorization', 'sponsorship', 'graduation_date', 'availability') if q not in profile.answers],
                   'destination': job['url'], 'version': 1, 'created': time.time()}
        packet_id = uid()
        status = 'DEMO_READY' if pr['synthetic'] and result['review']['approved'] else ('READY_FOR_REVIEW' if result['review']['approved'] else 'NEEDS_HUMAN')
        with connect() as c:
            c.execute('INSERT INTO packets VALUES (?,?,?,?,?,?,?,?)', (packet_id, job['id'], pr['id'], snapshot['id'], packed(payload), digest(payload), status, time.time()))
            c.execute("UPDATE tasks SET status='DONE',updated=? WHERE id=?", (time.time(), task['id']))
            c.execute("UPDATE jobs SET state='DRAFTED' WHERE id=?", (job['id'],))
            c.execute('INSERT INTO notifications VALUES (?,?,?,?,0,NULL)', (uid(), packet_id, f"{'Demo packet' if pr['synthetic'] else 'Review packet'}: {job['company']} - {job['title']}", time.time()))
            audit(c, 'PACKET_PREPARED', packet_id)
    except Exception as exc:
        error = str(exc) if isinstance(exc, (ProviderError, ValueError)) else 'Preparation failed; no packet approved'
        with connect() as c:
            c.execute("UPDATE tasks SET status='FAILED',error=?,updated=? WHERE id=?", (error, time.time(), task['id']))
            audit(c, 'PREPARATION_FAILED', task['id'])
    return True
