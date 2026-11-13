import pytest
from kitchen import api
from kitchen.errors import WorkshopError

ALL_FILES = {"equipment", "construction", "materials", "hardware", "labor", "pricing"}


def test_valid_mock_workshop_loads(home):
    w = api.load_workshop(home)
    assert w.is_mock is True
    assert set(w.mock_files) == {f"{n}.yaml" for n in ALL_FILES}
    assert w.labor.hourly_rate == 1000
    assert w.materials.sheets[0].id == "ldsp_16_white"
    assert w.materials.sheets[0].sheet == (2800, 2070)
    assert w.materials.sheets[0].texture is False
    assert w.materials.sheets[1].texture == "вдоль 2800"
    assert w.construction.facades.height_lower == "H - 4"
    assert w.construction.hinges_by_facade_height[0] == (900, 2)
    assert len(w.hardware.items) == 19
    assert w.pricing.quick_estimate_per_m == (85000, 110000)


def test_non_mock_file_is_not_listed(home):
    p = home / "workshop" / "labor.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("mock: true", "mock: false"), encoding="utf-8")
    w = api.load_workshop(home)
    assert "labor.yaml" not in w.mock_files
    assert w.is_mock is True


def _break(home, fname, old, new):
    p = home / "workshop" / fname
    text = p.read_text(encoding="utf-8")
    assert old in text
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def _problems(home):
    with pytest.raises(WorkshopError) as e:
        api.load_workshop(home)
    return "\n".join(e.value.problems)


def test_typo_key_names_file_key_value(home):
    _break(home, "labor.yaml", "ставка_в_час", "ставка_в_чаc")
    text = _problems(home)
    assert "labor.yaml" in text
    assert "ставка_в_чаc" in text
    assert "неизвестный ключ" in text


def test_string_instead_of_number(home):
    _break(home, "labor.yaml", "ставка_в_час: 1000", "ставка_в_час: тысяча")
    text = _problems(home)
    assert "labor.yaml" in text and "ставка_в_час" in text
    assert "тысяча" in text and "число" in text


def test_nested_key_path(home):
    _break(home, "labor.yaml", "замер: 90", "замер: много")
    text = _problems(home)
    assert "нормы_минут.замер" in text and "много" in text


def test_missing_key(home):
    _break(home, "pricing.yaml", "предоплата: 0.5\n", "")
    text = _problems(home)
    assert "pricing.yaml" in text and "предоплата" in text and "обязательный" in text


def test_dangling_back_panel_material(home):
    _break(home, "construction.yaml", "материал: hdf_3_white", "материал: hdf_nope")
    text = _problems(home)
    assert "construction.yaml" in text and "задняя_стенка.материал" in text
    assert "hdf_nope" in text and "materials.yaml" in text


def test_dangling_tandembox_material(home):
    _break(home, "construction.yaml", "материал: ldsp_16_white\n", "материал: ldsp_zzz\n")
    text = _problems(home)
    assert "ящик_tandembox.материал" in text and "ldsp_zzz" in text


def test_missing_file(home):
    (home / "workshop" / "pricing.yaml").unlink()
    assert "pricing.yaml" in _problems(home) and "не найден" in _problems(home)


def test_broken_yaml_syntax(home):
    (home / "workshop" / "pricing.yaml").write_text("mock: [true\n", encoding="utf-8")
    assert "pricing.yaml" in _problems(home)


def test_check_workshop_summary(home):
    r = api.check_workshop(home)
    assert r.valid is True and r.mock is True
    assert len(r.mock_files) == 6
    s = {f.file: f for f in r.files}
    assert s["materials.yaml"].counts == {"листовые": 3, "кромка": 2, "столешницы": 1, "фасады": 1}
    assert s["hardware.yaml"].counts == {"позиции": 19}
    assert s["labor.yaml"].counts == {"нормы_минут": 11}
    assert s["pricing.yaml"].counts["ключей"] == 6
    assert r.errors == []


def test_check_workshop_reports_errors_without_raising(home):
    _break(home, "labor.yaml", "замер: 90", "замер: много")
    r = api.check_workshop(home)
    assert r.valid is False and r.errors and r.mock is True


def test_home_resolution_env(home):
    assert api.resolve_home() == home


def test_home_resolution_walks_up(tmp_path, monkeypatch):
    monkeypatch.delenv("KITCHEN_HOME", raising=False)
    (tmp_path / "workshop").mkdir()
    (tmp_path / "workshop" / "construction.yaml").touch()
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    assert api.resolve_home() == tmp_path


def test_home_not_found(tmp_path, monkeypatch):
    monkeypatch.delenv("KITCHEN_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(WorkshopError) as e:
        api.resolve_home()
    assert "мастерская не найдена" in e.value.problems[0].lower()


def test_env_home_without_workshop(tmp_path, monkeypatch):
    monkeypatch.setenv("KITCHEN_HOME", str(tmp_path))
    with pytest.raises(WorkshopError) as e:
        api.resolve_home()
    assert "KITCHEN_HOME" in e.value.problems[0]


@pytest.mark.parametrize("bad", ["1", '"true"', '"yes"'])
def test_mock_must_be_real_bool(home, bad):
    _break(home, "labor.yaml", "mock: true", f"mock: {bad}")
    text = _problems(home)
    assert "labor.yaml" in text and "«mock»" in text and "true или false" in text


def test_bool_rejected_as_number(home):
    _break(home, "labor.yaml", "ставка_в_час: 1000", "ставка_в_час: true")
    text = _problems(home)
    assert "ставка_в_час" in text and "число" in text


def test_yaml_syntax_error_in_russian(home):
    (home / "workshop" / "pricing.yaml").write_text("mock: true\nпредоплата: [0.5\n", encoding="utf-8")
    text = _problems(home)
    assert "pricing.yaml: строка" in text and "столбец" in text and "ошибка синтаксиса YAML" in text
    assert "while" not in text


def test_step_row_error_is_about_the_row(home):
    _break(home, "construction.yaml", "[[150, 1], [600, 2]", "[[150], [600, 2]")
    text = _problems(home)
    assert "конфирматы_по_длине_соединения[0]" in text and "обязательный ключ" not in text


def test_failed_file_still_flags_mock(home):
    _break(home, "labor.yaml", "mock: true", "mock: 1")
    assert api.check_workshop(home).mock is True


def test_root_needs_construction_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("KITCHEN_HOME", raising=False)
    (tmp_path / "workshop").mkdir()
    monkeypatch.chdir(tmp_path)
    with pytest.raises(WorkshopError):
        api.resolve_home()
