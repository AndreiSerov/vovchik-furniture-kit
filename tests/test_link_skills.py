import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import REPO

SCRIPT = REPO / "scripts" / "link-skills"
PLACES = [".claude/skills", ".agents/skills", ".qwen/skills"]


@pytest.fixture
def repo(tmp_path):
    """Временная копия репозитория: скрипт и два скилла kitchen-*."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "link-skills")
    for name in ("kitchen-a", "kitchen-b"):
        (root / "skills" / name).mkdir(parents=True)
        (root / "skills" / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    (root / "skills" / "kitchen-nodoc").mkdir()  # без SKILL.md: не скилл
    (root / "skills" / "other").mkdir()
    (root / "skills" / "other" / "SKILL.md").write_text("x")  # не kitchen-*
    return root


@pytest.fixture
def fake_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


def run(root, home, *args):
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "link-skills"), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_creates_relative_links_in_all_three_places(repo, fake_home):
    r = run(repo, fake_home)
    assert r.returncode == 0, r.stderr
    for place in PLACES:
        for name in ("kitchen-a", "kitchen-b"):
            link = repo / place / name
            assert link.is_symlink()
            assert not os.path.isabs(os.readlink(link))
            assert (link / "SKILL.md").is_file()
        assert not (repo / place / "kitchen-nodoc").exists()
        assert not (repo / place / "other").exists()
    assert not (fake_home / ".hermes").exists()


def test_rerun_is_a_noop(repo, fake_home):
    run(repo, fake_home)
    before = {p: os.readlink(repo / p / "kitchen-a") for p in PLACES}
    stamps = {p: os.lstat(repo / p / "kitchen-a").st_mtime_ns for p in PLACES}
    r = run(repo, fake_home)
    assert r.returncode == 0
    assert before == {p: os.readlink(repo / p / "kitchen-a") for p in PLACES}
    assert stamps == {p: os.lstat(repo / p / "kitchen-a").st_mtime_ns for p in PLACES}


def test_foreign_skills_and_files_untouched(repo, fake_home):
    skills = repo / ".claude" / "skills"
    (skills / "tdd").mkdir(parents=True)
    (skills / "tdd" / "SKILL.md").write_text("tdd")
    (skills / ".managed.json").write_text("{}")
    elsewhere = repo / "elsewhere"
    elsewhere.mkdir()
    (skills / "foreign-link").symlink_to(elsewhere)
    (skills / "kitchen-foreign").symlink_to(elsewhere)  # чужой симлинк с нашим префиксом
    r = run(repo, fake_home)
    assert r.returncode == 0, r.stderr
    assert (skills / "tdd" / "SKILL.md").read_text() == "tdd"
    assert (skills / ".managed.json").read_text() == "{}"
    assert os.readlink(skills / "foreign-link") == str(elsewhere)
    assert os.readlink(skills / "kitchen-foreign") == str(elsewhere)


def test_real_dir_conflict_is_reported_and_skipped(repo, fake_home):
    conflict = repo / ".agents" / "skills" / "kitchen-a"
    conflict.mkdir(parents=True)
    (conflict / "mine.txt").write_text("keep")
    r = run(repo, fake_home)
    assert r.returncode != 0
    assert "kitchen-a" in r.stderr and ".agents" in r.stderr
    assert not conflict.is_symlink() and (conflict / "mine.txt").read_text() == "keep"
    assert (repo / ".agents/skills/kitchen-b").is_symlink()  # остальное сделано
    assert (repo / ".claude/skills/kitchen-a").is_symlink()


def test_real_file_conflict_is_reported(repo, fake_home):
    target = repo / ".qwen" / "skills" / "kitchen-b"
    target.parent.mkdir(parents=True)
    target.write_text("file")
    r = run(repo, fake_home)
    assert r.returncode != 0 and "kitchen-b" in r.stderr
    assert target.read_text() == "file"


def test_dangling_own_link_removed(repo, fake_home):
    run(repo, fake_home)
    shutil.rmtree(repo / "skills" / "kitchen-b")
    r = run(repo, fake_home)
    assert r.returncode == 0, r.stderr
    for place in PLACES:
        assert not (repo / place / "kitchen-b").is_symlink()
        assert (repo / place / "kitchen-a").is_symlink()


def test_stale_own_link_is_repointed(repo, fake_home):
    link = repo / ".claude" / "skills" / "kitchen-a"
    link.parent.mkdir(parents=True)
    link.symlink_to("../../skills/kitchen-b")  # наша, но указывает не туда
    assert run(repo, fake_home).returncode == 0
    assert Path(os.path.realpath(link)) == (repo / "skills" / "kitchen-a").resolve()


def test_hermes_with_fake_home_uses_absolute_targets(repo, fake_home):
    r = run(repo, fake_home, "--hermes")
    assert r.returncode == 0, r.stderr
    for name in ("kitchen-a", "kitchen-b"):
        link = fake_home / ".hermes" / "skills" / name
        assert link.is_symlink()
        target = os.readlink(link)
        assert os.path.isabs(target)
        assert Path(target) == (repo / "skills" / name).resolve()
    assert (repo / ".claude/skills/kitchen-a").is_symlink()
    assert run(repo, fake_home, "--hermes").returncode == 0  # идемпотентно


def test_hermes_leaves_foreign_skills(repo, fake_home):
    other = fake_home / ".hermes" / "skills" / "their-skill"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("theirs")
    assert run(repo, fake_home, "--hermes").returncode == 0
    assert (other / "SKILL.md").read_text() == "theirs"
