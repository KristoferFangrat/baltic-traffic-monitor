import json
import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    def _load(name: str):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def pg_conn():
    """Connection to a throw-away test database; skipped when not configured."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")

    from btm import db

    conn = db.connect(url)
    conn.execute("DROP SCHEMA IF EXISTS traffic CASCADE")
    conn.commit()
    db.init_schema(conn)
    yield conn
    conn.close()
