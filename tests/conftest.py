import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Временный KITCHEN_HOME с копией workshop/."""
    shutil.copytree(REPO / "workshop", tmp_path / "workshop")
    monkeypatch.setenv("KITCHEN_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def repo() -> Path:
    return REPO
