"""Task routing is advisory; it cannot choose reviewers or approve resume content."""
import json
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import Field
from typing import Literal

from .db import digest
from .models import StrictModel

TASKS = {
    'skills': ('Rank verified skill names against the public job description.', 'skills'),
    'layout': ('Choose margins and number of skills rows; no content selection.', 'cheap'),
    'tailor': ('Compare experience, engineering teams and projects; select evidence and draft alternatives.', 'reasoning'),
    'revise': ('Apply reviewer feedback to alternative wording without adding facts.', 'reasoning'),
    'master_audit': ('Compare strength and factual scope of source experience.', 'reasoning'),
    'gap_analysis': ('Compare job requirements to source evidence and prioritize entries.', 'reasoning'),
    'judge': ('Harsh independent factual and writing review.', 'reviewer'),
    'content_repair': ('Reviewer chooses source entries to address measured layout failures.', 'reviewer'),
    'final_review': ('Independent review of the entire assembled resume and page images.', 'reviewer'),
}


class ModelSpec(StrictModel):
    model: str = Field(min_length=1, max_length=120, pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._/-]*$')
    effort: Literal['low', 'medium', 'high']


class RoutingConfig(StrictModel):
    backend: Literal['codex', 'opencode'] = 'codex'
    mode: Literal['rules', 'laya'] = 'laya'
    cheap: ModelSpec
    reasoning: ModelSpec
    reviewer: ModelSpec
    skills: ModelSpec
    laya_url: str = 'http://127.0.0.1:8001'
    confidence_threshold: float = Field(default=.85, ge=.5, le=1)
    version: str = 'task-router-v2-model-skills'


def routing_configuration():
    backend = os.getenv('AUTOAPPLY_MODEL_BACKEND', 'codex')
    if backend not in {'codex', 'opencode'}:
        raise ValueError('AUTOAPPLY_MODEL_BACKEND must be codex or opencode')
    defaults = (('gpt-6-luna', 'gpt-6-astra', 'gpt-6-sol') if backend == 'codex'
                else ('opencode-go/glm-5.3-flash', 'opencode-go/glm-5.3', 'opencode-go/kimi-k2.6'))
    specs = {}
    for name, model, effort in zip(('cheap', 'reasoning', 'reviewer'), defaults, ('low', 'high', 'medium')):
        specs[name] = ModelSpec(model=os.getenv('AUTOAPPLY_'+name.upper()+'_MODEL', model),
                               effort=os.getenv('AUTOAPPLY_'+name.upper()+'_EFFORT', effort))
    if specs['reviewer'].model in {specs['cheap'].model, specs['reasoning'].model}:
        raise ValueError('Reviewer model must differ from both writer models')
    if backend == 'opencode' and any(not s.model.startswith('opencode-go/') for s in specs.values()):
        raise ValueError('OpenCode backend requires explicit opencode-go models; no provider fallback')
    specs['skills'] = ModelSpec(model='gpt-6-luna' if backend == 'codex' else 'opencode/space-bunny-free', effort='low')
    if specs['reviewer'].model == specs['skills'].model:
        raise ValueError('Reviewer model must differ from the skills matcher')
    return RoutingConfig(backend=backend, mode=os.getenv('AUTOAPPLY_ROUTER', 'laya'), **specs,
                         laya_url=os.getenv('LAYA_URL', 'http://127.0.0.1:8001').rstrip('/'))


def ask_laya(state, config):
    """Send bounded application-owned metadata only, never resume/JD text."""
    url = urlsplit(config.laya_url)
    if (url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password
            or url.query or url.fragment or (url.scheme == 'http' and url.hostname not in {'127.0.0.1', 'localhost', '::1'})):
        raise ValueError('Invalid Laya routing endpoint')
    question = {'type': 'choice', 'instructions': 'Classify the reasoning difficulty of the task.',
                'criteria': {'cheap': 'Simple formatting: adjust page margins and count skills rows. No content selection or writing.',
                             'reasoning': 'Complex judgment: compare experience, teams and projects, prioritize evidence, or write and revise resume content.'}}
    body = {'model': 'typed-decisions', 'state': state, 'questions': {'tier': question}}
    if len(json.dumps(body).encode('utf-8')) > 900:
        raise ValueError('Laya routing context budget exceeded')
    headers = {}
    if os.getenv('LAYA_API_KEY'):
        headers['Authorization'] = 'Bearer ' + os.environ['LAYA_API_KEY']
    with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
        response = client.post(config.laya_url+'/v1/systemone', json=body, headers=headers)
        response.raise_for_status()
        parsed = response.json()
        if not isinstance(parsed, dict) or not isinstance(parsed.get('answers'), dict):
            raise ValueError('Malformed Laya response')
        answer = parsed['answers'].get('tier')
    if not isinstance(answer, dict):
        raise ValueError('Malformed Laya tier')
    # Laya 0.3.20 exposes choice probability here; `confidence` is entropy-based.
    confidence = answer.get('answer_confidence')
    if (answer.get('choice') not in {'cheap', 'reasoning'} or type(confidence) not in {int, float}
            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError('Malformed Laya route')
    return answer['choice'], confidence


class TaskRouter:
    def __init__(self, config=None, predictor=None):
        self.config = config or routing_configuration()
        self.predictor = predictor or ask_laya

    def route(self, role, data, images=()):
        if role not in TASKS:
            raise ValueError('Unknown model task: '+role)
        task, minimum = TASKS[role]
        tier, reason, confidence = minimum, 'task_policy', None
        if minimum == 'skills':
            reason = 'pinned_skills_matcher_no_paid_fallback'
        elif minimum == 'reviewer':
            reason = 'pinned_independent_reviewer'
        elif self.config.mode == 'laya':
            # No untrusted instructions or personal text enter the router.
            state = {'task': task, 'input_characters': len(json.dumps(data)), 'images': len(images)}
            try:
                tier, confidence = self.predictor(state, self.config)
                if tier not in {'cheap', 'reasoning'}:
                    raise ValueError('Invalid tier')
                if confidence < self.config.confidence_threshold:
                    raise ValueError('Uncertain Laya route')
                reason = 'laya'
                if minimum == 'reasoning' and tier == 'cheap':
                    tier, reason = 'reasoning', 'laya_with_reasoning_floor'
            except (ValueError, KeyError, TypeError, httpx.HTTPError):
                tier, reason = 'reasoning', 'laya_unavailable_or_uncertain_conservative_route'
        spec = getattr(self.config, tier)
        return {'role': role, 'tier': tier, **spec.model_dump(), 'backend': self.config.backend,
                'effort_applied': spec.effort if self.config.backend == 'codex' else 'provider-default',
                'router': self.config.mode, 'reason': reason, 'confidence': confidence,
                'policy_version': self.config.version}

    def cached_route(self, folder, role, data, images=()):
        # Freeze routing across restarts, including uncertain decisions. Provider failure
        # must not get a new attempt just because Laya changes its mind later.
        folder = Path(folder)/'routes'
        folder.mkdir(parents=True, exist_ok=True)
        key = digest([self.config.model_dump(), role, data, len(images)])
        path = folder/(key+'.json')
        if path.exists():
            record = json.loads(path.read_text(encoding='utf-8'))
            route = record['route']
            if record.get('sha256') != digest(route):
                raise ValueError('Cached route changed')
            return route
        route = self.route(role, data, images)
        path.write_text(json.dumps({'route': route, 'sha256': digest(route)}, indent=2), encoding='utf-8')
        return route


def model_routing_status():
    """Local configuration inspection only: no requests, CLI calls or private facts."""
    import shutil
    config = routing_configuration()
    return {'configuration': config.model_dump(), 'cli_available': bool(shutil.which(config.backend)),
            'skills': 'one pinned model ranking; deterministic source and duplicate checks',
            'reviewer_pinned': True, 'live_provider_verified': False,
            'max_model_calls': 14, 'max_wording_regenerations': 1}


def main():
    """Probe real Laya inference with synthetic metadata; never call a writer."""
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1]/'.env')
    config = routing_configuration().model_copy(update={'mode': 'laya'})
    router = TaskRouter(config)
    routes = [router.route(role, {}) for role in ('layout', 'tailor')]
    ok = all(route['reason'] in {'laya', 'laya_with_reasoning_floor'} for route in routes)
    print(json.dumps({'laya_inference_verified': all(r['confidence'] is not None for r in routes),
                      'confident_routes': ok, 'routes': routes}, indent=2))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
