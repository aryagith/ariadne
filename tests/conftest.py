import pytest
from backend.db import init


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    from backend import job_tracker
    monkeypatch.setenv('AUTOAPPLY_ARCHIVE', str(tmp_path / 'job apps'))
    monkeypatch.setattr(job_tracker, 'ARCHIVE', tmp_path / 'job apps')
    monkeypatch.setenv('APP_DB', str(tmp_path / 'test.sqlite'))
    monkeypatch.setenv('APP_PASSWORD', 'test-password')
    monkeypatch.setenv('APP_ORIGIN', 'http://127.0.0.1:8000')
    monkeypatch.setenv('PROVIDER_MODE', 'demo')
    monkeypatch.setenv('AUTOAPPLY_ROUTER', 'rules')
    monkeypatch.setenv('AUTOAPPLY_MODEL_BACKEND', 'codex')
    for tier in ('CHEAP', 'REASONING', 'REVIEWER'):
        monkeypatch.delenv('AUTOAPPLY_'+tier+'_MODEL', raising=False)
        monkeypatch.delenv('AUTOAPPLY_'+tier+'_EFFORT', raising=False)
    monkeypatch.delenv('ALLOW_PROVIDER_PROCESSING', raising=False)
    init()
