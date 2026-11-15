"""Тикет 40: чек-лист выезда (4.4) — ключ `выезд` equipment.yaml, состав разделов, PDF, учёт, `kitchen production`."""

from pathlib import Path

import pytest
import yaml
from kitchen import api
from kitchen.errors import KitchenError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"


def without_trip_key(home) -> list[str]:
    path = home / "workshop" / "equipment.yaml"
    text = path.read_text(encoding="utf-8")
    return text[: text.index("выезд:")].splitlines()  # ключ стоит в конце файла


def test_repository_equipment_names_the_trip_lists_and_mocks_md_mentions_them(repo):
    data = yaml.safe_load((repo / "workshop" / "equipment.yaml").read_text(encoding="utf-8"))
    assert set(data["выезд"]) == {"инструмент", "расходники"}
    assert all(isinstance(s, str) for key in data["выезд"] for s in data["выезд"][key])
    mocks = (repo / "MOCKS.md").read_text(encoding="utf-8")
    assert "`выезд.инструмент`" in mocks and "`выезд.расходники`" in mocks


def test_workshop_without_trip_key_is_load_error(base_door_project, home):
    (home / "workshop" / "equipment.yaml").write_text("\n".join(without_trip_key(home)) + "\n")
    with pytest.raises(KitchenError) as e:
        api.parts_from_project(base_door_project, home)
    assert "выезд" in "\n".join(e.value.problems)


@pytest.mark.parametrize(
    "bad",
    ["выезд: {инструмент: [a], расходники: [b], склад: [c]}", "выезд: {инструмент: a, расходники: [b]}"],
    ids=["unknown-key", "not-a-list"],
)
def test_bad_trip_key_is_load_error_naming_it(base_door_project, home, bad):
    (home / "workshop" / "equipment.yaml").write_text("\n".join([*without_trip_key(home), bad]) + "\n")
    with pytest.raises(KitchenError) as e:
        api.parts_from_project(base_door_project, home)
    text = "\n".join(e.value.problems)
    assert "выезд" in text
