"""Снимок выводов до рефакторинга этапа 9 (тикет 66; удаляется тикетом 75).

Снимок нигде не хранится. Тест берёт движок коммита `BASE_COMMIT` (`git archive`), прогоняет на нём и на
текущем коде помощник `tests/stage9_snapshot.py` (две подпроцессные пары: `kitchen` каждой стороны
импортируется чисто) и сравнивает папки файл за файлом. Мастерскую и входные файлы оба прогона берут из
текущего репозитория: помощник копирует `workshop/` из той папки, где лежит он сам.
"""

import difflib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

# Последний коммит перед любым изменением движка этапа 9 (тикеты 67–75 идут после него).
# CI забирает полную историю (`fetch-depth: 0`), иначе этого коммита там нет.
BASE_COMMIT = "086cf59"

REPO = Path(__file__).resolve().parent.parent
HELPER = REPO / "tests" / "stage9_snapshot.py"

pytestmark = pytest.mark.timeout(120)


def git(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], check=False, cwd=REPO, capture_output=True, **kwargs)


def snapshot_process(out: Path, pythonpath: Path | None) -> subprocess.Popen:
    env = {**os.environ}
    # обе стороны — только со своим движком и своим home
    for name in ("KITCHEN_HOME", "KITCHEN_DB", "PYTHONPATH"):
        env.pop(name, None)
    if pythonpath:
        env["PYTHONPATH"] = str(pythonpath)
    return subprocess.Popen(
        [sys.executable, str(HELPER), "--out", str(out)],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def read_dir(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes().decode("utf-8")
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def diff_report(before: dict[str, str], after: dict[str, str]) -> str:
    lines = []
    for name in sorted(before.keys() | after.keys()):
        if before.get(name) == after.get(name):
            continue
        if name not in after or name not in before:
            lines.append(f"{'нет в новом прогоне' if name not in after else 'нет в старом'}: {name}")
            continue
        delta = difflib.unified_diff(
            before[name].splitlines(), after[name].splitlines(), "было", "стало", lineterm="", n=1
        )
        lines.append(f"отличается: {name}\n" + "\n".join(list(delta)[:30]))
    return f"{len(lines)} файлов отличаются; первые:\n" + "\n".join(lines[:5])


@pytest.fixture(scope="module")
def runs(tmp_path_factory) -> tuple[dict[str, str], dict[str, str]]:
    """(снимок на движке BASE_COMMIT, снимок на текущем движке); оба прогона идут одновременно, один раз на модуль."""
    base = git("cat-file", "-e", f"{BASE_COMMIT}^{{commit}}")
    assert base.returncode == 0, (
        f"нет коммита {BASE_COMMIT}: в CI нужен `fetch-depth: 0`, локально — `git fetch --unshallow`"
    )
    root = tmp_path_factory.mktemp("stage9")
    archive = git("archive", BASE_COMMIT, "engine", "skills")  # движок читает скрипт брифа из skills/
    assert archive.returncode == 0, archive.stderr.decode()
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(root / "base", filter="data")
    procs = {
        "before": snapshot_process(root / "before", root / "base" / "engine"),
        "after": snapshot_process(root / "after", None),
    }
    outputs = {}
    try:
        for name, proc in procs.items():
            outputs[name], _ = proc.communicate()
            assert proc.returncode == 0, f"помощник ({name}) упал:\n{outputs[name][-3000:]}"
    finally:
        for proc in procs.values():
            proc.kill()  # упавшая сторона не оставляет вторую висеть
    origin = {
        name: next(line for line in out.splitlines() if line.startswith("kitchen: "))[len("kitchen: ") :]
        for name, out in outputs.items()
    }
    # каждая сторона сама сообщает, откуда импортирован kitchen: «до» — из базового коммита, «после» — из репо
    assert origin["before"].startswith(str((root / "base" / "engine").resolve())), origin
    assert origin["after"].startswith(str((REPO / "engine").resolve())), origin
    return read_dir(root / "before"), read_dir(root / "after")


@pytest.fixture(scope="module")
def stored(runs) -> dict[str, str]:
    """Снимок текущего движка для проверок «не пусто»."""
    return runs[1]


def case(stored: dict[str, str], name: str, key: str):
    return json.loads(stored[f"{name}/reads/{key}.json"])


def test_outputs_match_those_of_the_base_commit(runs):
    before, after = runs
    assert before, "помощник ничего не записал"
    assert before == after, diff_report(before, after)


# --- снимок не пустой там, где ловит особые правила (проверка приёмки 66.3) -----------------

NEWER_BRIEF = "4_newer_brief_kerf_no_sink"
BROKEN = "6_file_check_error"
NO_BUILD = "5_file_module_does_not_build"
NO_BRIEF = "3_order_without_brief"
DRAFT_ERROR = "Проект с ошибкой проверки (импорт его отклонит): "


def test_snapshot_has_the_six_cases(stored):
    assert sorted({k.split("/")[0] for k in stored}) == [
        "1_order_petrov",
        "2_file_petrov_with_order",
        NO_BRIEF,
        NEWER_BRIEF,
        NO_BUILD,
        BROKEN,
    ]


def test_snapshot_estimate_of_newer_brief_has_the_brief_kerf_and_sink_warnings(stored):
    warnings = case(stored, NEWER_BRIEF, "estimate")["warnings"]
    assert len(warnings) == 4
    assert warnings[0] == "Н5: без задней стенки"
    assert warnings[1].startswith("Бриф №2 новее брифа, с которым проверена версия 1 (№1)")
    assert warnings[2] == "в проекте нет мойки — врезка мойки не посчитана"
    assert warnings[3].startswith("пропил 4.2 мм учтён как 5 мм")


def test_snapshot_estimate_and_quote_of_a_broken_draft_carry_the_check_error(stored):
    for key in ("estimate", "quote"):
        warnings = case(stored, BROKEN, key)["warnings"]
        assert sum(w.startswith(DRAFT_ERROR) for w in warnings) == 2, (key, warnings)


def test_snapshot_views_of_a_module_that_does_not_build_have_one_error(stored):
    findings = case(stored, NO_BUILD, "views")["findings"]
    assert [(f["code"], f["subject"]) for f in findings if f["level"] == "error"] == [("module_build", "Н3")]


def test_snapshot_parts_of_a_module_that_does_not_build_record_the_error(stored):
    error = case(stored, NO_BUILD, "parts")["error"]
    assert error.startswith("OrderError: Модуль Н3: отверстие «конфирмат» ")


def test_snapshot_trip_without_a_brief_has_the_empty_header_warning(stored):
    assert "брифа нет — шапка пустая" in case(stored, NO_BRIEF, "trip")["warnings"]
