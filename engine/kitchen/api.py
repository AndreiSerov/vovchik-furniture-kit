"""Публичный API движка. Вся логика здесь; CLI только печатает результат."""

from pathlib import Path

from kitchen.errors import WorkshopError
from kitchen.result import Result
from kitchen.workshop import (
    FILES,
    load_workshop,
    mock_files_of,
    read_raw,
    resolve_home,
)

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
    home = home or resolve_home()
    raw, read_problems = read_raw(home)
    errors = list(read_problems)
    try:
        load_workshop(home)
    except WorkshopError as e:
        errors = list(dict.fromkeys(errors + e.problems))
    mock = mock_files_of(raw)
    files = [
        FileSummary(file=n, counts=_counts(n, raw[n]), mock=n in mock, mock_files=[n] if n in mock else [])
        for n in FILES
        if n in raw
    ]
    return WorkshopCheck(valid=not errors, files=files, errors=errors, mock=bool(mock), mock_files=mock)
