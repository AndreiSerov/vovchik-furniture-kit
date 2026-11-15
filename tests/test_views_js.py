"""Тикет 42: скрипт страницы (`engine/kitchen/web/viewer.js`) под node с настоящим three.js r147, без WebGL.

Проверяет то, что в браузере увидит глаз: кухня не зеркальна, стены видны только изнутри, камера на высоте
1600 и вся кухня в кадре, нажатие подсвечивает модуль. Без `node` тесты пропускаются (в CI — падают); WebGL,
касания и щипок — `manual` (телефон)."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from kitchen import api

from tests.conftest import REPO

ENGINE = Path(api.__file__).parent
THREE = ENGINE / "vendor" / "three" / "three.min.js"
VIEWER = ENGINE / "web" / "viewer.js"
CHECK = Path(__file__).parent / "js" / "check_viewer.js"
PETROV = Path(__file__).parent / "fixtures" / "petrov_project.json"
NODE = shutil.which("node")

# В CI node есть всегда: без него тесты не пропускаются, а падают (тикет 47); локально без node — пропуск.
pytestmark = pytest.mark.skipif(
    NODE is None and not os.environ.get("CI"),
    reason="нет node: скрипт страницы проверяется в браузере (manual)",
)


@pytest.fixture(scope="module")
def viewed(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    shutil.copytree(REPO / "workshop", home / "workshop")
    scene = api.views_from_project(PETROV, home)
    data = home / "scene.json"
    data.write_text(scene.model_dump_json(), encoding="utf-8")
    done = subprocess.run(
        [NODE, str(CHECK), str(THREE), str(VIEWER), str(data)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_viewer_in_front_of_wall_a_sees_modules_left_to_right_in_row_order(viewed):
    """Зритель в комнате лицом к стене A: модуль с меньшим x — левее на экране, стена Б — справа (не зеркало)."""
    seen = sorted(viewed["screen"].values(), key=lambda item: item["x"])
    assert len(seen) >= 4
    screen = [item["screenX"] for item in seen]
    assert screen == sorted(screen) and len(set(screen)) == len(screen)
    assert viewed["wallAStartScreenX"] < viewed["wallAEndScreenX"] < viewed["wallBFarScreenX"]  # Б — справа


def test_walls_face_the_room_and_are_single_sided_and_floor_faces_up(viewed):
    assert {w["label"] for w in viewed["walls"]} == {"A", "Б"}
    for wall in viewed["walls"]:
        assert wall["facing"] > 0 and wall["frontOnly"], wall
    assert viewed["floorNormalY"] == pytest.approx(1)


def test_every_module_and_gap_is_pickable_and_walls_are_not(viewed):
    assert viewed["modules"] == 14 and len(viewed["pickLabels"]) == 14
    assert viewed["pickableKinds"] == ["module"]


def test_selection_highlights_one_module_and_clears(viewed):
    s = viewed["select"]
    assert s["n1"] != 0 and s["n1After"] == 0 and s["n2"] != 0
    assert s["gapSelected"] == "ПМ1" and s["gapOpacity"] > s["gapAfter"]
    assert s["cleared"] is None


@pytest.mark.parametrize("aspect", ["1.6", "0.5"])
def test_camera_is_at_1600_and_the_whole_kitchen_is_in_frame(viewed, aspect):
    cam = viewed[f"camera{aspect}"]
    assert cam["y"] == pytest.approx(1.6)
    assert cam["worst"] <= 0.93
    assert cam["target"][0] == pytest.approx(2.8) and cam["target"][2] == pytest.approx(0)  # угол: конец A


def test_labels_sit_just_in_front_of_the_face_and_face_the_room(viewed):
    for label, v in viewed["labels"].items():
        assert v["facing"] > 0.99 and 0 < v["offset"] < 0.01, label


def test_box_is_width_by_height_by_depth(viewed):
    assert [round(x, 3) for x in viewed["n1box"]] == [0.6, 0.72, 0.579]


CHECK_LAYOUTS = Path(__file__).parent / "js" / "check_viewer_layouts.js"


def test_one_wall_and_u_shaped_kitchens_have_inward_walls_all_modules_pickable_and_are_in_frame(tmp_path):
    """Не только угол Петрова: одна стена (6000 мм) и П-образная кухня (тикет 47)."""
    from tests.test_views import long_wall, u_shaped

    home = tmp_path / "home"
    shutil.copytree(REPO / "workshop", home / "workshop")
    files = {}
    for name, views in (("one", long_wall(home)), ("u", u_shaped(home))):
        path = tmp_path / f"{name}.json"
        path.write_text(views.model_dump_json(), encoding="utf-8")
        files[name] = (path, views)
    done = subprocess.run(
        [NODE, str(CHECK_LAYOUTS), str(THREE), str(VIEWER), *(str(p) for p, _ in files.values())],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)
    for name, expected_walls in (("one", 1), ("u", 3)):
        path, views = files[name]
        got = seen[str(path)]
        assert len(got["walls"]) == expected_walls == len(views.scene.walls)
        assert all(w["facing"] > 0 and w["frontOnly"] for w in got["walls"]), (name, got["walls"])
        assert got["modules"] == len(views.scene3d.modules) == len(got["pickLabels"])
        for aspect in ("1.6", "0.5"):
            assert got["cameras"][aspect]["y"] == pytest.approx(1.6)
            assert got["cameras"][aspect]["worst"] <= 0.93, (name, aspect)
