import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend import subscription_models as models
from backend.latex_workflow import FitPlan, Judgment


def test_roles_fresh_cli_and_verified_cache(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(models.shutil,'which',lambda _: 'codex.exe')
    def run(command,**kwargs):
        calls.append((command,kwargs))
        Path(command[command.index('-o')+1]).write_text(json.dumps({'preset':'standard','skill_lines':3,'reason':'test'}))
        return SimpleNamespace(returncode=0,stdout=json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':20}}))
    monkeypatch.setattr(models.subprocess,'run',run)
    runner=models.SubscriptionModels(tmp_path)
    result=runner.call('layout','check layout',{'selected':['b1']},FitPlan)
    assert result.preset=='standard'
    command,kwargs=calls[0]
    assert command[command.index('-m')+1]=='gpt-6-luna'
    assert '--ephemeral' in command and '--ignore-user-config' in command and 'read-only' in command
    assert 'forced_login_method="chatgpt"' in command
    assert 'features.shell_tool=false' in command and 'web_search="disabled"' in command
    assert 'OPENAI_API_KEY' not in kwargs['env']
    assert 'Do not use any tools' in kwargs['input']
    runner.call('layout','check layout',{'selected':['b1']},FitPlan)
    assert len(calls)==1
    next((tmp_path/'model calls').glob('*/response.json')).write_text('{}')
    with pytest.raises(ValueError,match='Cached model response changed'):
        runner.call('layout','check layout',{'selected':['b1']},FitPlan)


def test_failure_no_fallback_no_retry(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(models.shutil,'which',lambda _: 'codex.exe')
    monkeypatch.setattr(models.subprocess,'run',lambda *a,**k: calls.append(a) or SimpleNamespace(returncode=1,stdout=''))
    runner=models.SubscriptionModels(tmp_path)
    with pytest.raises(ValueError,match='No fallback'):
        runner.call('layout','check',{},FitPlan)
    with pytest.raises(ValueError,match='previously attempted'):
        models.SubscriptionModels(tmp_path).call('layout','check',{},FitPlan)
    assert len(calls)==1


def test_model_tool_execution_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(models.shutil,'which',lambda _: 'codex.exe')
    monkeypatch.setattr(models.subprocess,'run',lambda *a,**k: SimpleNamespace(returncode=0,stdout=json.dumps({'type':'item.completed','item':{'type':'command_execution'}})))
    with pytest.raises(ValueError,match='TOOLS_ATTEMPTED'):
        models.SubscriptionModels(tmp_path).call('layout','check',{},FitPlan)


def test_input_bounded_before_call(tmp_path):
    with pytest.raises(ValueError,match='budget'):
        models.SubscriptionModels(tmp_path).call('layout','check',{'text':'x'*60001},FitPlan)


def test_judge_schema_and_response_require_explicit_nested_advice(tmp_path, monkeypatch):
    monkeypatch.setattr(models.shutil, 'which', lambda _: 'codex.exe')
    def run(command, **kwargs):
        contract = json.loads(Path(command[command.index('--output-schema')+1]).read_text())
        assert set(contract['required']) == {'evaluations', 'selection_advice'}
        advice = contract['$defs']['SelectionAdvice']
        assert set(advice['required']) == {'drop_bullet_ids', 'add_bullet_ids', 'reason'}
        assert 'default' not in advice['properties']['reason']
        response = {'evaluations': [], 'selection_advice': {
            'drop_bullet_ids': [], 'add_bullet_ids': [], 'reason': 'MOCKED no change'}}
        Path(command[command.index('-o')+1]).write_text(json.dumps(response))
        return SimpleNamespace(returncode=0, stdout='')
    monkeypatch.setattr(models.subprocess, 'run', run)
    result = models.SubscriptionModels(tmp_path).call('judge', 'Synthetic check', {}, Judgment)
    assert result.selection_advice.add_bullet_ids == []
    for response in ({'evaluations': []}, {'evaluations': [], 'selection_advice': {}}):
        with pytest.raises(ValueError):
            Judgment.model_validate(response)


@pytest.mark.parametrize('nested', [False, True])
def test_schema_preflight_rejects_optional_fields_before_launch(tmp_path, monkeypatch, nested):
    contract = Judgment.model_json_schema()
    target = contract['$defs']['SelectionAdvice'] if nested else contract
    target.pop('required')
    class BrokenSchema:
        @staticmethod
        def model_json_schema():
            return contract
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid schema must not launch the CLI')
    monkeypatch.setattr(models.subprocess, 'run', forbidden)
    runner = models.SubscriptionModels(tmp_path)
    with pytest.raises(ValueError, match='every property must be required'):
        runner.call('judge', 'Synthetic check', {}, BrokenSchema)
    assert runner.calls == 0
    assert not list(tmp_path.rglob('attempt.json'))


@pytest.mark.parametrize('message,category', [
    ('Invalid schema for response_format: required is missing', 'INVALID_OUTPUT_SCHEMA'),
    ('rate_limit_exceeded', 'RATE_LIMIT'),
    ('401 Unauthorized', 'AUTHENTICATION'),
    ('model_not_found', 'MODEL_ACCESS'),
    ('stream disconnected before completion', 'CONNECTION'),
    ('unexpected argument --unknown', 'CLI_CONFIGURATION'),
    ('Something else went wrong', 'CLI_FAILURE'),
])
def test_failure_diagnostics_do_not_mislabel_or_leak(tmp_path, monkeypatch, message, category):
    monkeypatch.setattr(models.shutil, 'which', lambda _: 'codex.exe')
    # Error details can echo private input or credentials; do not persist raw text.
    secret = 'private-token-and-resume-text'
    monkeypatch.setattr(models.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=1, stdout=json.dumps({'type': 'turn.failed', 'error': {'message': message}}),
        stderr=secret))
    with pytest.raises(ValueError, match=category) as exc:
        models.SubscriptionModels(tmp_path).call('judge', 'Synthetic check', {}, Judgment)
    diagnostic = next(tmp_path.rglob('failure.json')).read_text()
    assert json.loads(diagnostic)['category'] == category
    assert secret not in diagnostic and secret not in str(exc.value)
    assert message not in diagnostic
    assert not list(tmp_path.rglob('complete.json'))


def test_failed_turn_cannot_approve_even_with_zero_exit_and_response(tmp_path, monkeypatch):
    monkeypatch.setattr(models.shutil, 'which', lambda _: 'codex.exe')
    def run(command, **kwargs):
        Path(command[command.index('-o')+1]).write_text(json.dumps(
            {'preset':'standard', 'skill_lines':3, 'reason':'MOCKED'}))
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            {'type':'turn.failed', 'error':{'message':'stream disconnected'}}))
    monkeypatch.setattr(models.subprocess, 'run', run)
    with pytest.raises(ValueError, match='CONNECTION'):
        models.SubscriptionModels(tmp_path).call('layout', 'Synthetic check', {}, FitPlan)
    assert not list(tmp_path.rglob('complete.json'))


@pytest.mark.parametrize('outcome,category', [
    ('timeout', 'TIMEOUT'), ('launch', 'CLI_LAUNCH'),
    ('missing', 'MISSING_RESPONSE'), ('malformed', 'INVALID_RESPONSE'),
])
def test_non_provider_failures_preserve_diagnostics_and_block_retry(tmp_path, monkeypatch, outcome, category):
    monkeypatch.setattr(models.shutil, 'which', lambda _: 'codex.exe')
    def run(command, **kwargs):
        if outcome == 'timeout':
            raise models.subprocess.TimeoutExpired(command, 300)
        if outcome == 'launch':
            raise OSError('private diagnostic')
        if outcome == 'malformed':
            Path(command[command.index('-o')+1]).write_text('{}')
        return SimpleNamespace(returncode=0, stdout='')
    monkeypatch.setattr(models.subprocess, 'run', run)
    with pytest.raises(ValueError, match=category):
        models.SubscriptionModels(tmp_path).call('layout', 'Synthetic check', {}, FitPlan)
    assert json.loads(next(tmp_path.rglob('failure.json')).read_text())['category'] == category
    with pytest.raises(ValueError, match='previously attempted'):
        models.SubscriptionModels(tmp_path).call('layout', 'Synthetic check', {}, FitPlan)
    assert not list(tmp_path.rglob('complete.json'))
