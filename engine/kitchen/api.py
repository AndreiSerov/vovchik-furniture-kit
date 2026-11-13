"""Публичный API движка. Вся логика здесь; CLI только печатает результат."""

from pathlib import Path

from kitchen.result import Result
from kitchen.workshop import FILES, load, load_workshop, resolve_home

__all__ = ["FileSummary", "WorkshopCheck", "check_workshop", "load_workshop", "resolve_home"]


class FileSummary(Result):
    file: str
    counts: dict[str, int]


class WorkshopCheck(Result):
    valid: bool
    files: list[FileSummary]
    errors: list[str]


_LIST_KEYS = {
    "materials.yaml": ["листовые", "кромка", "столешницы", "фасады"],
    "hardware.yaml": ["позиции"],
    "labor.yaml": ["нормы_минут"],
}


def _counts(name: str, data: dict) -> dict[str, int]:
    keys = _LIST_KEYS.get(name)
    if keys:
        return {k: len(data.get(k) or []) for k in keys}
    return {"ключей": len([k for k in data if k != "mock"])}


def check_workshop(home: Path | None = None) -> WorkshopCheck:
    loaded = load(home or resolve_home())
    mock = loaded.mock_files
    files = [
        FileSummary(
            file=n, counts=_counts(n, loaded.raw[n]), mock=n in mock, mock_files=[n] if n in mock else []
        )
        for n in FILES
        if n in loaded.raw
    ]
    return WorkshopCheck(
        valid=not loaded.problems, files=files, errors=loaded.problems, mock=bool(mock), mock_files=mock
    )
