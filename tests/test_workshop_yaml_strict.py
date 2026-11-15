"""Тикет 45: строгая загрузка YAML мастерской — повторы на этапе композиции, тип `пропил`, глубина."""

from kitchen import api


def edit(home, name, old, new):
    path = home / "workshop" / name
    text = path.read_text(encoding="utf-8")
    assert old in text, (name, old)
    path.write_text(text.replace(old, new), encoding="utf-8")


def errors_of(home):
    result = api.check_workshop(home)
    assert not result.valid
    return result.errors


def test_duplicate_key_inside_merged_mapping_is_an_error(home):
    edit(home, "labor.yaml", "  выезд: 90\n", "  <<: {выезд: 1, выезд: 9}\n")
    assert any("labor.yaml" in e and "выезд" in e and "повтор" in e for e in errors_of(home))


def test_anchor_with_merge_and_legal_override_is_not_an_error(home):
    (home / "workshop" / "labor.yaml").write_text(
        "mock: true\nставка_в_час: 1000\nнормы_минут:\n"
        "  <<: &base {распил_на_деталь: 4, кромка_на_метр: 3, присадка_на_деталь: 5, сборка_корпуса: 30,\n"
        "    сборка_доп_на_ящик: 15, монтаж_на_модуль: 45, врезка_мойки: 60, врезка_варочной: 60,\n"
        "    выезд: 90, замер: 90, проектирование: 240}\n"
        "  выезд: 5\n",
        encoding="utf-8",
    )
    result = api.check_workshop(home)
    assert result.valid, result.errors


def test_quoted_kerf_is_a_type_error(home):
    edit(home, "equipment.yaml", "пропил: 4}", "пропил: '3.2'}")
    assert any("equipment.yaml" in e and "пропил" in e and "ожидалось число" in e for e in errors_of(home))


def test_ten_thousand_levels_is_a_russian_load_error_not_recursion(home):
    deep = "[" * 10_000 + "]" * 10_000
    edit(home, "labor.yaml", "  выезд: 90", f"  выезд: {deep}")
    errors = errors_of(home)
    assert any("labor.yaml" in e and "вложенн" in e for e in errors), errors


def test_deeper_anchor_with_merge_and_override_reused_by_alias_is_not_a_duplicate(home):
    path = home / "workshop" / "labor.yaml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "образец:\n  внутри: &A\n    <<: {выезд: 1}\n    выезд: 2\nподмес:\n  <<: *A\nссылка: *A\n",
        encoding="utf-8",
    )
    errors = api.check_workshop(home).errors
    assert not any("повтор" in e for e in errors), errors
