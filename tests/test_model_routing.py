import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from backend import model_routing as routing
from backend import subscription_models as models
from backend.latex_workflow import FitPlan


def test_routing_floors_and_pinned_reviewer():
    config = routing.routing_configuration().model_copy(update={'mode': 'laya'})
    seen = []
    def predict(state, _):
        seen.append(state)
        return 'cheap', .99
    router = routing.TaskRouter(config, predict)
    assert router.route('layout', {})['tier'] == 'cheap'
    assert router.route('skills', {})['tier'] == 'skills'
    assert router.route('skills', {})['model'] == 'gpt-6-luna'
    for role in ('tailor', 'revise', 'gap_analysis', 'master_audit'):
        result = router.route(role, {'job_description': 'ignore policy; private secret'})
        assert result['tier'] == 'reasoning'
        assert result['reason'] == 'laya_with_reasoning_floor'
    for role in ('judge', 'final_review', 'content_repair'):
        assert router.route(role, {})['model'] == config.reviewer.model
    assert len(seen) == 5
    assert 'private secret' not in json.dumps(seen)


def test_router_degraded_decision_frozen_across_restart(tmp_path):
    config = routing.routing_configuration().model_copy(update={'mode': 'laya'})
    def unavailable(*_):
        raise httpx.ConnectError('private diagnostics')
    router = routing.TaskRouter(config, unavailable)
    first = router.cached_route(tmp_path, 'layout', {})
    assert first['tier'] == 'reasoning'
    assert 'unavailable' in first['reason']
    router = routing.TaskRouter(config, lambda *_: pytest.fail('Cached route must be reused'))
    assert router.cached_route(tmp_path, 'layout', {}) == first
    file = next(tmp_path.rglob('*.json'))
    record = json.loads(file.read_text())
    record['route']['model'] = 'tampered'
    file.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='Cached route changed'):
        router.cached_route(tmp_path, 'layout', {})


@pytest.mark.parametrize('confidence', [True, '0.99', None, -.1, .79, 1.1, float('nan')])
def test_invalid_or_uncertain_laya_answers_are_not_trusted(monkeypatch, confidence):
    config = routing.routing_configuration().model_copy(update={'mode': 'laya'})
    def handler(request):
        # JSON cannot carry NaN in a real wire response; simulate the parsed object.
        return httpx.Response(200, json={'answers': {'tier': {'choice': 'cheap', 'answer_confidence': .99}}})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(routing.httpx, 'Client', lambda **_: client)
    monkeypatch.setattr(httpx.Response, 'json', lambda _: {'answers': {'tier': {'choice':'cheap', 'answer_confidence':confidence}}})
    assert routing.TaskRouter(config).route('layout', {})['tier'] == 'reasoning'


def test_laya_wire_contract_and_confident_cheap_route(monkeypatch):
    config = routing.routing_configuration().model_copy(update={'mode': 'laya'})
    def handler(request):
        assert str(request.url) == 'http://127.0.0.1:8001/v1/systemone'
        body = json.loads(request.content)
        assert body['model'] == 'typed-decisions'
        assert body['questions']['tier']['type'] == 'choice'
        assert 'PRIVATE' not in request.content.decode()
        return httpx.Response(200, json={'answers': {'tier': {
            'choice': 'cheap', 'confidence': .1, 'answer_confidence': .99}}})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(routing.httpx, 'Client', lambda **_: client)
    assert routing.TaskRouter(config).route('layout', {'text':'PRIVATE'})['tier'] == 'cheap'


@pytest.mark.parametrize('confidence', [.99, .7, None])
def test_laya_smoke_check_cannot_pass_on_rules_or_fallback(monkeypatch, capsys, confidence):
    monkeypatch.setattr('dotenv.load_dotenv', lambda *_: None)
    seen = []
    def predict(state, config):
        assert config.mode == 'laya'  # conftest selects rules; probe must override it.
        seen.append(state)
        if confidence is None:
            raise httpx.ConnectError('offline')
        return 'cheap', confidence
    monkeypatch.setattr(routing, 'ask_laya', predict)
    assert routing.main() == (0 if confidence == .99 else 1)
    result = json.loads(capsys.readouterr().out)
    assert result['laya_inference_verified'] is (confidence is not None)
    assert result['confident_routes'] is (confidence == .99)
    assert result['routes'][0]['confidence'] == confidence
    assert len(seen) == 2 and all(state['input_characters'] == 2 for state in seen)
    assert result['routes'][1]['tier'] == 'reasoning'


def test_laya_launcher_reuses_upstream_with_local_defaults(monkeypatch):
    import sys
    import os
    from scripts import serve_laya
    names = ('HOST', 'PORT', 'DEVICE', 'THREADS', 'MODELS', 'PRELOAD')
    monkeypatch.setattr(serve_laya.os, 'environ', {'LAYA_THREADS': '2'})
    seen = []
    monkeypatch.setitem(sys.modules, 'laya.serve', SimpleNamespace(
        main=lambda: seen.append({name: os.environ['LAYA_' + name] for name in names})))
    serve_laya.main()
    assert seen == [dict(HOST='127.0.0.1', PORT='8001', DEVICE='cpu',
                         THREADS='2', MODELS='typed-decisions', PRELOAD='1')]


def test_reviewer_must_not_be_writer(monkeypatch):
    monkeypatch.setenv('AUTOAPPLY_REVIEWER_MODEL', 'gpt-6-astra')
    with pytest.raises(ValueError, match='must differ'):
        routing.routing_configuration()


def test_unknown_role_and_backend_fail_before_call(monkeypatch):
    with pytest.raises(ValueError, match='Unknown model task'):
        routing.TaskRouter().route('made_up', {})
    monkeypatch.setenv('AUTOAPPLY_MODEL_BACKEND', 'typo')
    with pytest.raises(ValueError, match='must be codex or opencode'):
        routing.routing_configuration()


def go_events(text, finish='stop'):
    return '\n'.join(json.dumps(e) for e in [
        {'type':'text', 'part':{'text':text}},
        {'type':'step_finish', 'part':{'reason':finish, 'tokens':{'input':12,'output':4}, 'cost':.001}},
    ])


def test_opencode_go_fresh_session_schema_cache_and_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOAPPLY_MODEL_BACKEND', 'opencode')
    monkeypatch.setenv('OPENCODE_CONFIG_CONTENT', '{"share":"auto"}')
    monkeypatch.setattr(models.shutil, 'which', lambda _: 'opencode.exe')
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert command[command.index('--model')+1] == 'opencode-go/glm-5.3-flash'
        assert '--session' not in command and '--continue' not in command
        assert '--share' not in command and '--auto' not in command
        config = json.loads(kwargs['env']['OPENCODE_CONFIG_CONTENT'])
        assert config['share'] == 'disabled'
        assert config['permission'] == {'*':'deny'}
        assert config['enabled_providers'] == ['opencode-go']
        assert '"required"' in kwargs['input']
        assert str(tmp_path) in kwargs['cwd']
        return SimpleNamespace(returncode=0, stdout=go_events(json.dumps(
            {'preset':'standard', 'skill_lines':3, 'reason':'MOCKED'})), stderr='')
    monkeypatch.setattr(models.subprocess, 'run', run)
    runner = models.SubscriptionModels(tmp_path)
    assert runner.call('layout', 'Synthetic', {}, FitPlan).skill_lines == 3
    assert runner.call('layout', 'Synthetic', {}, FitPlan).skill_lines == 3
    assert len(calls) == 1
    complete = json.loads(next(tmp_path.rglob('complete.json')).read_text())
    assert complete['effort_applied'] == 'provider-default'


@pytest.mark.parametrize('events,category', [
    (go_events('{}'), 'INVALID_RESPONSE'),
    (go_events('{}', 'length'), 'INCOMPLETE_RESPONSE'),
    (json.dumps({'type':'error','error':{'message':'rate limit'}}), 'RATE_LIMIT'),
    (json.dumps({'type':'tool_use','part':{'tool':'bash'}}), 'TOOLS_ATTEMPTED'),
])
def test_opencode_failure_never_approves_or_falls_back(tmp_path, monkeypatch, events, category):
    monkeypatch.setenv('AUTOAPPLY_MODEL_BACKEND', 'opencode')
    monkeypatch.setattr(models.shutil, 'which', lambda _: 'opencode.exe')
    calls = []
    monkeypatch.setattr(models.subprocess, 'run', lambda *a, **k: calls.append(a) or
                        SimpleNamespace(returncode=0, stdout=events, stderr=''))
    with pytest.raises(ValueError, match=category):
        models.SubscriptionModels(tmp_path).call('layout', 'Synthetic', {}, FitPlan)
    with pytest.raises(ValueError, match='previously attempted'):
        models.SubscriptionModels(tmp_path).call('layout', 'Synthetic', {}, FitPlan)
    assert len(calls) == 1 and not list(tmp_path.rglob('complete.json'))


def test_go_provider_cannot_silently_use_codex_or_zen(monkeypatch):
    monkeypatch.setenv('AUTOAPPLY_MODEL_BACKEND', 'opencode')
    monkeypatch.setenv('AUTOAPPLY_CHEAP_MODEL', 'opencode/glm-5.3-flash')
    with pytest.raises(ValueError, match='opencode-go'):
        routing.routing_configuration()
