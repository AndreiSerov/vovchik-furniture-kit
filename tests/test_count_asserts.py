"""`scripts/count-asserts` (тикет 73.2): потерянный assert даёт код 1 и из корня репозитория, и из подпапки."""

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "count-asserts"
# git в хуке pre-commit получает GIT_INDEX_FILE внешнего коммита: без очистки `git add` во временном репо пишет в него
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
TWO = "def test_a():\n    assert 1\n    assert 2\n"
ONE = "def test_a():\n    assert 1\n"


def make_repo(tmp_path: Path, base: dict[str, str], work: dict[str, str | None]) -> Path:
    """Репо с коммитом `base` (имя в tests/ -> текст); затем `work` в рабочей папке (None удаляет файл)."""
    repo = tmp_path / "r"
    (repo / "tests").mkdir(parents=True)
    (repo / "engine").mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            env=CLEAN_ENV,
        )

    git("init", "-q")
    for name, text in base.items():
        (repo / "tests" / name).write_text(text)
    (repo / "engine" / "keep").write_text("")
    git("add", ".")
    git("commit", "-qm", "base")
    for name, text in work.items():
        if text is None:
            (repo / "tests" / name).unlink()
        else:
            (repo / "tests" / name).write_text(text)
    return repo


def repo_with_dropped_assert(tmp_path: Path) -> Path:
    return make_repo(tmp_path, {"test_x.py": TWO}, {"test_x.py": ONE})


def run_script(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(args or ("HEAD",))],
        check=False,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=CLEAN_ENV,
    )


def test_count_asserts_drop_fails_from_root(tmp_path):
    result = run_script(repo_with_dropped_assert(tmp_path))
    assert result.returncode == 1
    assert "test_x.py 2 1 <" in result.stdout


def test_count_asserts_drop_fails_from_a_subdirectory(tmp_path):
    result = run_script(repo_with_dropped_assert(tmp_path) / "engine")
    assert result.returncode == 1
    assert "test_x.py 2 1 <" in result.stdout


def test_count_asserts_ignores_outer_git_environment(tmp_path, monkeypatch):
    poison_index = tmp_path / "poison-index"
    poison_dir = tmp_path / "poison-git"
    monkeypatch.setenv("GIT_INDEX_FILE", str(poison_index))
    monkeypatch.setenv("GIT_DIR", str(poison_dir))
    result = run_script(repo_with_dropped_assert(tmp_path / "inner"))
    assert result.returncode == 1
    assert "test_x.py 2 1 <" in result.stdout
    assert not poison_index.exists()
    assert not poison_dir.exists()


def test_count_asserts_unchanged_exits_zero(tmp_path):
    result = run_script(make_repo(tmp_path, {"test_x.py": TWO}, {}))
    assert result.returncode == 0
    assert "test_x.py 2 2" in result.stdout
    assert " <" not in result.stdout


def test_count_asserts_counts_pytest_raises(tmp_path):
    swapped = "def test_a():\n    assert 1\n    with pytest.raises(ValueError):\n        pass\n"
    result = run_script(make_repo(tmp_path, {"test_x.py": TWO}, {"test_x.py": swapped}))
    assert result.returncode == 0
    assert "test_x.py 2 2" in result.stdout


def test_count_asserts_file_on_one_side_only(tmp_path):
    repo = make_repo(tmp_path, {"test_x.py": TWO}, {"test_x.py": None, "test_new.py": ONE})
    result = run_script(repo)
    assert result.returncode == 1
    assert "test_x.py 2 0 <" in result.stdout
    assert "test_new.py 0 1" in result.stdout
    assert "test_new.py 0 1 <" not in result.stdout


def test_count_asserts_wrong_argument_count_exits_two(tmp_path):
    result = run_script(make_repo(tmp_path, {"test_x.py": TWO}, {}), "HEAD", "extra")
    assert result.returncode == 2
    assert "count-asserts <git-ref>" in result.stderr
