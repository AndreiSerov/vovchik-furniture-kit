"""Ревью тикета 52: края правил глубины и высоты пустого места, запасная глубина верхнего ряда, верх камеры."""

from kitchen import api

from tests.test_gap import base, gap, lower, mod, write


def views_of(home, walls):
    return api.calculation(project=write(home, walls), home=home).views()


def depth_of_gap(result, label):
    return next(m for m in result.scene3d.modules if m.label == label).depth


def test_gap_takes_the_nearest_module_not_a_deeper_one_further_along(home):
    # ПМ1 первый: ближайший следующий Н1 (600 -> 619), дальше Н2 (700 -> 719), глубина ряда 719
    walls = [{"label": "A", "length": 1800, "rows": [
        lower(gap("ПМ1"), base("Н1") | {"depth": 600}, base("Н2") | {"depth": 700}),
    ]}]  # fmt: skip
    assert depth_of_gap(views_of(home, walls), "ПМ1") == 619


def test_gap_beside_a_deep_tall_module_ignores_it(home):
    # пенал глубже соседа-модуля: пеналы не в счёт -> 579 от Н1, а не глубина пенала
    walls = [{"label": "A", "length": 1800, "rows": [
        lower(base("Н1"), gap("ПМ1"), mod("П1", "tall_oven", 600, depth=700)),
    ]}]  # fmt: skip
    result = views_of(home, walls)
    p1 = next(m for m in result.scene.modules if m.label == "П1")
    assert p1.depth > 579
    assert depth_of_gap(result, "ПМ1") == 579


def test_lower_top_of_one_wall_does_not_lift_the_gap_of_another(home):
    from tests.test_placement import corner

    walls = [
        {"label": "A", "length": 2250, "rows": [
            lower(base("Н1") | {"height": 800}, base("Н2"), corner("Н4", "base_corner", 1050, "Б")),
        ]},
        {"label": "Б", "length": 1800, "rows": [lower(gap("ПМ1"), base("Н5"))]},
    ]  # fmt: skip
    g = next(g for g in views_of(home, walls).scene.gaps if g.label == "ПМ1")
    assert g.front.height == 820  # верх нижних стены Б, а не 900 стены A


def test_lone_upper_gap_without_uppers_takes_the_workshop_upper_depth_and_lifts_the_camera(home):
    from tests.test_gap import upper

    walls = [{"label": "A", "length": 1200, "rows": [lower(base("Н1"), base("Н2")), upper(gap("ПМ1", 1200))]}]
    result = views_of(home, walls)
    scene = result.scene3d
    pm1 = next(m for m in scene.modules if m.label == "ПМ1")
    assert pm1.depth == 320  # верхний модуль мастерской без фасада, не нижний 560
    assert pm1.boxes[0].max[2] == 2200 and scene.camera.target[2] == 1100  # верх ПМ1 — самый высокий в сцене
