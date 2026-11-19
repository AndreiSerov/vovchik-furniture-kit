"""`scripts/count-asserts` (тикет 73.2): потерянный assert даёт код 1 и из корня репозитория, и из подпапки."""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "count-asserts"


def repo_with_dropped_assert(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    (repo / "tests").mkdir(parents=True)
    (repo / "engine").mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    (repo / "tests" / "test_x.py").write_text("def test_a():\n    assert 1\n    assert 2\n")
    (repo / "engine" / "keep").write_text("")
    git("add", ".")
    git("commit", "-qm", "base")
    (repo / "tests" / "test_x.py").write_text("def test_a():\n    assert 1\n")
    return repo


def run_script(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "HEAD"], check=False, cwd=cwd, capture_output=True, text=True
    )


def test_count_asserts_drop_fails_from_root(tmp_path):
    result = run_script(repo_with_dropped_assert(tmp_path))
    assert result.returncode == 1
    assert "test_x.py 2 1 <" in result.stdout


def test_count_asserts_drop_fails_from_a_subdirectory(tmp_path):
    result = run_script(repo_with_dropped_assert(tmp_path) / "engine")
    assert result.returncode == 1
    assert "test_x.py 2 1 <" in result.stdout
