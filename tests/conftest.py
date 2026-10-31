"""Phase 1 tests run against the local Docker Postgres. Each `fresh` test starts from a reset (D0)."""
from __future__ import annotations

import subprocess
import sys

import pytest

from app import admin
from common.settings import REPO_ROOT, get_settings

# Citation that passes the evidence rule (Retention playbook §1.4).
CITE = {"evidence": ["test: UPI down 36% vs prior 3 weeks"], "playbook_ref": "Retention playbook §3.1"}


@pytest.fixture(scope="session", autouse=True)
def generated_data():
    if not (get_settings().data_out_dir / "merchants.parquet").exists():
        subprocess.run([sys.executable, "-m", "data.generator"], cwd=REPO_ROOT, check=True)


@pytest.fixture
def fresh():
    state = admin.reset()
    yield state
    admin.reset()
