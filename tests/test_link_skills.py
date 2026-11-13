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


def snapshot(root):
    """Всё, что лежит в местах ссылок: путь -> цель симлинка или None."""
    found = {}
    for place in PLACES:
        base = root / place
        for p in sorted(base.rglob("*")) if base.exists() else []:
            found[str(p)] = os.readlink(p) if p.is_symlink() else None
    return found


def test_check_clean_returns_zero(repo, fake_home):
    run(repo, fake_home)
    r = run(repo, fake_home, "--check")
    assert r.returncode == 0, r.stderr + r.stdout


def test_check_reports_missing_and_changes_nothing(repo, fake_home):
    r = run(repo, fake_home, "--check")
    assert r.returncode == 1
    assert "missing" in r.stdout and "kitchen-a" in r.stdout
    assert snapshot(repo) == {}
    assert not (repo / ".claude").exists()


def test_check_reports_dangling_own_link(repo, fake_home):
    run(repo, fake_home)
    shutil.rmtree(repo / "skills" / "kitchen-b")
    before = snapshot(repo)
    r = run(repo, fake_home, "--check")
    assert r.returncode == 1
    assert "dangling" in r.stdout and "kitchen-b" in r.stdout
    assert snapshot(repo) == before


def test_check_reports_foreign_kitchen_symlink(repo, fake_home):
    run(repo, fake_home)
    link = repo / ".claude" / "skills" / "kitchen-a"
    link.unlink()
    link.symlink_to(repo)
    before = snapshot(repo)
    r = run(repo, fake_home, "--check")
    assert r.returncode == 1
    assert "foreign" in r.stdout and "kitchen-a" in r.stdout
    assert snapshot(repo) == before


def test_check_reports_blocked_by_real_dir(repo, fake_home):
    run(repo, fake_home)
    link = repo / ".qwen" / "skills" / "kitchen-a"
    link.unlink()
    link.mkdir()
    before = snapshot(repo)
    r = run(repo, fake_home, "--check")
    assert r.returncode == 1
    assert "real file or folder" in r.stdout and "kitchen-a" in r.stdout
    assert snapshot(repo) == before


def test_foreign_symlink_with_wanted_name_is_warned_and_kept(repo, fake_home):
    outside = repo / "elsewhere"
    outside.mkdir()
    link = repo / ".claude" / "skills" / "kitchen-a"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    r = run(repo, fake_home)
    assert r.returncode == 1
    assert "kitchen-a" in r.stderr
    assert os.readlink(link) == str(outside)


def test_check_reports_link_pointing_at_wrong_skill(repo, fake_home):
    run(repo, fake_home)
    link = repo / ".claude" / "skills" / "kitchen-a"
    link.unlink()
    link.symlink_to("../../skills/kitchen-b")
    before = snapshot(repo)
    r = run(repo, fake_home, "--check")
    assert r.returncode == 1
    assert "wrong skill" in r.stdout and "kitchen-a" in r.stdout
    assert snapshot(repo) == before


def test_check_with_hermes_uses_fake_home(repo, fake_home):
    run(repo, fake_home)
    r = run(repo, fake_home, "--check", "--hermes")
    assert r.returncode == 1
    assert ".hermes" in r.stdout and "missing" in r.stdout
    assert not (fake_home / ".hermes").exists()
    run(repo, fake_home, "--hermes")
    assert run(repo, fake_home, "--check", "--hermes").returncode == 0
