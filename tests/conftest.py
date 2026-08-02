"""Fixture condivise: ogni test parte da un database vuoto e isolato."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VETTORE_DB", str(tmp_path / "test.db"))
    from app import db
    from app.main import app

    db.init_db()
    with TestClient(app) as c:
        yield c
