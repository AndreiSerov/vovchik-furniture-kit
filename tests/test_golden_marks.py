"""Спека 1.5: каждый эталон несёт выкладку и пометку «ПРОВЕРИТЬ МАСТЕРУ»."""

import json

import pytest

from tests.conftest import GOLDEN_DIR, load_golden

FILES = sorted(GOLDEN_DIR.glob("*.json"))


def test_golden_files_exist():
    assert len(FILES) >= 12


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_golden_has_master_mark_and_layout(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "ПРОВЕРИТЬ МАСТЕРУ" in raw["_проверить_мастеру"]
    assert raw["_выкладка"].strip()
    assert not any(k.startswith("_") for k in load_golden(path.stem))
