import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Point the app at a throwaway database before anything imports config, so a
# test run can never touch the developer's own packtrack.db.
TEST_DB = ROOT / "test_packtrack.db"
os.environ["PACKTRACK_DB_URL"] = f"sqlite:///{TEST_DB}"

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import db as db_module  # noqa: E402

DEMO = ROOT / "demo"


@pytest.fixture
def session():
    """A throwaway in-memory database per test."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    db_module.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(scope="session")
def client():
    """A TestClient against a clean file-backed database."""
    from fastapi.testclient import TestClient

    TEST_DB.unlink(missing_ok=True)
    import api

    with TestClient(api.app) as c:
        yield c
    TEST_DB.unlink(missing_ok=True)


@pytest.fixture
def clean(client):
    client.post("/api/demo/reset")
    return client
