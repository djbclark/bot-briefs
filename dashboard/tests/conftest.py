from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Generator

_DASHBOARD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DASHBOARD))
# Pytest may preload the installed wheel; force the source tree + fresh config load.
for _name in list(sys.modules):
    if _name == "briefs" or _name.startswith("briefs."):
        del sys.modules[_name]

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
os.environ["BRIEFS_CONFIG_FILE"] = str(_FIXTURES / "briefs-test.toml")
os.environ.setdefault("BRIEFS_ALLOWED_LOGINS", "user@example.com")

import pytest
from fastapi.testclient import TestClient

from briefs.config import BOTS, REPO_ROOT

assert any(b.slug == "radar" for b in BOTS), f"expected test bot inventory, got {[b.slug for b in BOTS]}"
from briefs.db import connect, init_schema
from briefs.importer import run_import
from briefs.web import create_app

SAMPLES_DIR = REPO_ROOT / "dev" / "samples"


@pytest.fixture
def samples_path() -> Path:
    if not SAMPLES_DIR.exists():
        pytest.skip("dev/samples directory is missing")
    return SAMPLES_DIR


@pytest.fixture
def temp_db(tmp_path: Path) -> str:
    db_file = str(tmp_path / "test_briefs.db")
    conn = connect(db_file)
    init_schema(conn)
    conn.close()
    return db_file


@pytest.fixture
def imported_db(temp_db: str, samples_path: Path) -> str:
    # Run import once into the temp db using the dev/samples
    sources = {
        "radar_md_glob": str(samples_path / "radar-*.md"),
        "zero_md_glob": str(samples_path / "zero-*.md"),
        "zero_state": str(samples_path / "zero-reported.json"),
        "family_state": str(samples_path / "family-state.json"),
        "cos_reqs": str(samples_path / "cos-reqs.json"),
        "transcripts": str(samples_path / "transcripts-sendtouser.json"),
    }
    run_import(sources=sources, db_path=temp_db)
    return temp_db


@pytest.fixture
def client(imported_db: str) -> Generator[TestClient, None, None]:
    app = create_app(imported_db)
    with TestClient(app) as test_client:
        yield test_client
