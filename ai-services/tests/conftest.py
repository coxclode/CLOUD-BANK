from __future__ import annotations

import os

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-key-not-real")
os.environ.setdefault("APP_ENV", "development")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from src.api.main import create_app

    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
