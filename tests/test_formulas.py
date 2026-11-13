import pytest
from kitchen import api
from kitchen.errors import WorkshopError
from kitchen.formulas import FormulaError, evaluate, variables


def test_height_lower_h720():
    assert evaluate("H - 4", H=720) == 716


def test_single_door_width_w600():
    assert evaluate("W - 4", W=600) == 596


def test_two_doors_width_w600():
    assert evaluate("(W - 4 - 2) / 2", W=600) == 297


def test_pair_with_cyrillic_variable():
    assert evaluate("(внутр_ширина - 75) x (500 - 10)", внутр_ширина=540) == (465, 490)


def test_unknown_variable():
    with pytest.raises(FormulaError) as e:
        evaluate("Q - 1")
    assert "«Q»" in str(e.value)


def test_forbidden_power():
    with pytest.raises(FormulaError):
        evaluate("W ** 2", W=600)


def test_forbidden_call():
    with pytest.raises(FormulaError):
        evaluate("min(W, 100)", W=600)


def test_forbidden_import():
    with pytest.raises(FormulaError):
        evaluate("__import__('os').system('ls')")


def test_non_integer_division():
    with pytest.raises(FormulaError) as e:
        evaluate("(W - 3) / 2", W=600)
    assert "нацело" in str(e.value)


def test_division_by_zero():
    with pytest.raises(FormulaError):
        evaluate("W / 0", W=600)


def test_unknown_symbol():
    with pytest.raises(FormulaError):
        evaluate("W @ 2")


def test_variables_collected():
    assert variables("(внутр_ширина - 75) x (500 - 10)") == {"внутр_ширина"}


def test_fractional_variable_rejected():
    with pytest.raises(FormulaError) as e:
        evaluate("H - 4", H=720.5)
    assert "«H»" in str(e.value) and "целое" in str(e.value)


# --- формулы проверяются при загрузке мастерской ------------------------------


def _break(home, fname, old, new):
    p = home / "workshop" / fname
    text = p.read_text(encoding="utf-8")
    assert old in text
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def _problems(home):
    with pytest.raises(WorkshopError) as e:
        api.load_workshop(home)
    return "\n".join(e.value.problems)


def test_unknown_variable_rejected_at_load(home):
    _break(home, "construction.yaml", 'высота_нижнего: "H - 4"', 'высота_нижнего: "Q - 4"')
    text = _problems(home)
    assert "высота_нижнего" in text and "«Q»" in text


def test_bad_syntax_rejected_at_load(home):
    _break(home, "construction.yaml", '"(W - 4 - 2) / 2"', '"W ** 2"')
    text = _problems(home)
    assert "две_двери_ширина" in text and "неожиданный элемент" in text


def test_bad_purchase_rounding_rejected_at_load(home):
    _break(
        home, "construction.yaml", "округление_закупки: вверх до 0.1 м", "округление_закупки: вверх до 0.1"
    )
    text = _problems(home)
    assert "округление_закупки" in text and "вверх до N м" in text


def test_unsorted_steps_rejected_at_load(home):
    _break(home, "construction.yaml", "[[900, 2], [1600, 3]", "[[1600, 2], [900, 3]")
    text = _problems(home)
    assert "петли_по_высоте_фасада" in text and "по возрастанию" in text


def test_unknown_edge_in_material_mapping_rejected_at_load(home):
    _break(home, "materials.yaml", "ldsp_16_white: edge_08_white", "ldsp_16_white: edge_nope")
    text = _problems(home)
    assert "кромка_к_материалу" in text and "edge_nope" in text


def test_unknown_material_in_edge_mapping_rejected_at_load(home):
    _break(home, "materials.yaml", "ldsp_16_white: edge_08_white", "ldsp_nope: edge_08_white")
    text = _problems(home)
    assert "кромка_к_материалу" in text and "ldsp_nope" in text
