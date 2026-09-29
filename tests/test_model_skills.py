import json
from types import SimpleNamespace

import pytest

from backend.model_routing import TaskRouter, routing_configuration
from backend.opencode_models import invocation
from backend.resume_skills import catalog, rank_with_model, plan_skills, SkillRanking
from backend.subscription_models import SubscriptionModels
from backend import subscription_models as models
from test_resume_skills import SOURCE, terms


@pytest.mark.parametrize('backend,model', [('codex','gpt-6-luna'), ('opencode','opencode/space-bunny-free')])
def test_skills_pinned_even_when_laya_would_escalate(monkeypatch, backend, model):
    monkeypatch.setenv('AUTOAPPLY_MODEL_BACKEND', backend)
    config = routing_configuration().model_copy(update={'mode':'laya'})
    router = TaskRouter(config, lambda *_: pytest.fail('Pinned skills should not invoke Laya'))
    route = router.route('skills', {})
    assert route['model'] == model and route['effort'] == 'low'
    if backend == 'opencode':
        command, _, env = invocation('opencode', route, 'Synthetic', SkillRanking.model_json_schema(), (), {})
        assert command[command.index('--model')+1] == model
        assert json.loads(env['OPENCODE_CONFIG_CONTENT'])['enabled_providers'] == ['opencode']


def test_model_rank_controls_render_order_but_does_not_duplicate_covered_skills():
    names = [s['term'] for s in catalog(SOURCE)]
    order = ['PyTorch'] + [x for x in names if x != 'PyTorch']
    received = []
    def call(role, instruction, data, schema):
        received.append(data)
        assert role == 'skills' and set(data) == {'skill_terms','job_description'}
        return schema(ranked_terms=order)
    ranking = rank_with_model(SimpleNamespace(call=call), SOURCE, 'ML job')
    plan = plan_skills(SOURCE, ['b2'], job_description='ML job', ranked_terms=ranking)
    assert terms(plan)[0] == 'PyTorch' and 'Python' not in terms(plan)
    assert plan['matching'] == 'model'
    # A later layout change filters newly covered skills, without another model call.
    next_plan = plan_skills(SOURCE, ['b2','b4'], job_description='ML job', ranked_terms=ranking)
    assert 'SQL' not in terms(next_plan) and len(received) == 1
    assert 'Example Company' not in json.dumps(received)


def test_explicit_and_related_jd_matches_precede_unrelated_model_preferences():
    names = [s['term'] for s in catalog(SOURCE)]
    order = ['PyTorch'] + [x for x in names if x != 'PyTorch']
    selected = terms(plan_skills(SOURCE, ['b2'], job_description='Python software engineering', ranked_terms=order))
    assert selected[0] == 'Python'
    assert selected.index('Git') < selected.index('PyTorch')


@pytest.mark.parametrize('change', ['invented','duplicate','omitted','renamed'])
def test_model_cannot_invent_duplicate_omit_or_rename_source_skills(change):
    order = [s['term'] for s in catalog(SOURCE)]
    if change == 'invented':
        order.append('Kubernetes')
    elif change == 'duplicate':
        order.append(order[0])
    elif change == 'omitted':
        order.pop()
    else:
        order[0] = 'Made up spelling'
    runner = SimpleNamespace(call=lambda *args: SkillRanking(ranked_terms=order))
    with pytest.raises(ValueError, match='every source skill exactly once'):
        rank_with_model(runner, SOURCE, 'Synthetic job')


def test_free_model_failure_stops_without_paid_retry(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOAPPLY_MODEL_BACKEND', 'opencode')
    monkeypatch.setattr(models.shutil, 'which', lambda _: 'opencode.exe')
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert command[command.index('--model')+1] == 'opencode/space-bunny-free'
        return SimpleNamespace(returncode=1, stdout='', stderr='model_not_found')
    monkeypatch.setattr(models.subprocess, 'run', run)
    with pytest.raises(ValueError, match='MODEL_ACCESS'):
        SubscriptionModels(tmp_path).call('skills', 'Synthetic', {}, SkillRanking)
    with pytest.raises(ValueError, match='previously attempted'):
        SubscriptionModels(tmp_path).call('skills', 'Synthetic', {}, SkillRanking)
    assert len(calls) == 1


def test_nonfree_zen_route_is_rejected():
    route = {'role':'skills', 'model':'opencode/glm-5.3'}
    with pytest.raises(ValueError, match='pinned free Zen'):
        invocation('opencode', route, '', {}, (), {})
