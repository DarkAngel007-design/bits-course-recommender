import os

os.environ["BCR_DISABLE_DOTENV"] = "1"  # tests are hermetic: never read a real .env / API keys
for _k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "LLM_PROVIDER"):
    os.environ.pop(_k, None)

import json
from pathlib import Path

import pytest

from backend.app.academics.history import interpret
from backend.app.academics.models import StudentProfile
from backend.app.academics.requirements import compute_requirements
from backend.app.academics.scope import resolve_scope
from backend.app.catalog.store import get_store

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def snap():
    return get_store().current()


@pytest.fixture(scope="session")
def golden():
    data = json.loads((FIXTURES / "golden_profiles.json").read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def state(profile_dict, snap):
    p = StudentProfile.model_validate(profile_dict)
    scope = resolve_scope(p, snap)
    hist = interpret(p, snap)
    req = compute_requirements(p, snap, scope, hist)
    return p, scope, hist, req


@pytest.fixture
def make_state(snap):
    return lambda d: state(d, snap)
