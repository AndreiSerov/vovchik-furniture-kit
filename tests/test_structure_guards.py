"""Ограждения структуры: ловят то, что ревью раньше находило руками."""

import ast
import re
import typing
from pathlib import Path

from kitchen import models
from kitchen.parts import APPLIANCE_NAMES, TEMPLATES

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
ENGINE = ROOT / "engine" / "kitchen"

# ADR 0002: тесты логики идут через kitchen.api, CLI — контрактными тестами.
PUBLIC = {"kitchen.api", "kitchen.cli", "kitchen.errors", "kitchen.result"}

# Отдельные инструменты (`python -m`), не логика заказов: тесты вызывают их напрямую.
TOOLS = {"kitchen.skill_lint"}

# Храповик: тесты, которые уже ходят во внутренние модули. Список только сокращается.
# Новый тест берёт данные через kitchen.api; если без внутреннего модуля никак — запиши
# причину в DECISIONS.md и добавь сюда в том же коммите.
LEGACY_INTERNAL_IMPORTS = {
    "test_brief_api.py": {"kitchen.store"},
    "test_checks_brief.py": {"kitchen.brief", "kitchen.models"},
    "test_cut_pdf.py": {"kitchen.cutting", "kitchen.cutting_pdf", "kitchen.pdf"},
    "test_cut_pdf_dense.py": {"kitchen.cutting", "kitchen.cutting_pdf", "kitchen.pdf"},
    "test_cutting.py": {"kitchen.cutting", "kitchen.parts", "kitchen.project", "kitchen.workshop"},
    "test_input_guards_project.py": {"kitchen.parts"},
    "test_orders_api.py": {"kitchen.store"},
    "test_pdf.py": {"kitchen.pdf"},
    # `row_layouts` не сходится только на искусственной стене: через проект её не собрать (тикет 45)
    "test_project_cleanup.py": {"kitchen.common"},
    "test_project_import_diff.py": {"kitchen.project"},
    "test_row_sum_registry.py": {"kitchen.parts"},
    "test_structure_guards.py": {"kitchen.models", "kitchen.parts"},
    "test_template_registry.py": {"kitchen.parts", "kitchen.project", "kitchen.workshop"},
}

# Файлы, где шаблоны модулей определены. Остальной движок берёт свойства шаблона из TEMPLATES.
TEMPLATE_HOMES = {"parts.py", "tall.py"}


def kitchen_imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("kitchen"):
            if node.module == "kitchen":
                found |= {f"kitchen.{alias.name}" for alias in node.names}
            else:
                found.add(node.module)
        elif isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names if alias.name.startswith("kitchen")}
    return found


def test_tests_reach_logic_through_the_api():
    problems = []
    for path in sorted(TESTS.glob("test_*.py")):
        extra = kitchen_imports(path) - PUBLIC - TOOLS - LEGACY_INTERNAL_IMPORTS.get(path.name, set())
        if extra:
            problems.append(f"{path.name}: {sorted(extra)}")
    assert not problems, "ADR 0002: тесты логики идут через kitchen.api.\n" + "\n".join(problems)


def test_every_legacy_import_exception_is_explained_in_decisions():
    decisions = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    missing = [name for name in LEGACY_INTERNAL_IMPORTS if name not in decisions]
    assert not missing, f"ADR 0002: исключение без причины в DECISIONS.md: {missing}"


def test_legacy_list_only_shrinks():
    stale = []
    for name, allowed in LEGACY_INTERNAL_IMPORTS.items():
        path = TESTS / name
        unused = allowed - (kitchen_imports(path) if path.exists() else set())
        if unused:
            stale.append(f"{name}: {sorted(unused)}")
    assert not stale, "Убери из LEGACY_INTERNAL_IMPORTS то, что тест больше не импортирует:\n" + "\n".join(
        stale
    )


def test_template_names_live_only_in_the_registry():
    names = "|".join(re.escape(n) for n in TEMPLATES)
    pattern = re.compile(rf"""["']({names})["']""")
    problems = []
    for path in sorted(ENGINE.glob("*.py")):
        if path.name in TEMPLATE_HOMES:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                problems.append(f"{path.name}:{number}: {line.strip()}")
    assert not problems, (
        "Свойство шаблона берётся из реестра TEMPLATES, а не по имени шаблона:\n" + "\n".join(problems)
    )


def test_drawer_part_names_live_only_in_parts():
    pattern = re.compile(r"""["'](дно ящика|задняя стенка ящика)["']""")
    problems = []
    for path in sorted(ENGINE.glob("*.py")):
        if path.name == "parts.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                problems.append(f"{path.name}:{number}: {line.strip()}")
    assert not problems, "Имена деталей ящика берутся из parts.DRAWER_BOTTOM и DRAWER_BACK:\n" + "\n".join(
        problems
    )


# Тикет 33. Поля, которые ограничивает разбор правила или валидатор модели, а не тип.
BOUNDED_BY_VALIDATOR = {
    "Pricing.quick_estimate_per_m",
    "MeterRounding.step_m",
    "RatioRule.per_unit",
    "RoundingRule.step",
}


def _has_bound(metadata):
    return any(getattr(m, "ge", None) is not None or getattr(m, "gt", None) is not None for m in metadata)


def _unbounded(annotation, metadata, where, seen):
    if typing.get_origin(annotation) is typing.Annotated:
        base, *extra = typing.get_args(annotation)
        flat = []
        for item in extra:
            flat += list(getattr(item, "metadata", [])) or [item]
        return _unbounded(base, [*metadata, *flat], where, seen)
    if annotation in (int, float):
        return [] if _has_bound(metadata) else [where]
    if isinstance(annotation, type) and issubclass(annotation, models.BaseModel):
        return _unbounded_fields(annotation, seen)
    found = []
    for arg in typing.get_args(annotation):
        found += _unbounded(arg, [], where, seen)
    return found


def _unbounded_fields(model, seen):
    if model in seen:
        return []
    seen.add(model)
    found = []
    for name, field in model.model_fields.items():
        found += _unbounded(field.annotation, field.metadata, f"{model.__name__}.{name}", seen)
    return found


def test_every_numeric_model_field_has_a_bound():
    """Число мастерской без ge/gt в типе — молчаливо принятое отрицательное значение."""
    unbounded = set(_unbounded_fields(models.Workshop, set()))
    assert unbounded <= BOUNDED_BY_VALIDATOR, sorted(unbounded - BOUNDED_BY_VALIDATOR)


def test_every_template_has_a_russian_title():
    """Тикет 18: строка состава КП берёт название шаблона из реестра, у каждого шаблона оно русское."""
    for name, template in TEMPLATES.items():
        assert re.search("[а-яё]", template.title, re.IGNORECASE), name


def test_every_appliance_kind_has_a_russian_name():
    assert set(APPLIANCE_NAMES) == set(models.APPLIANCE_KINDS)


def test_cli_reaches_logic_only_through_the_api():
    """ADR 0002: CLI — тонкая обёртка над kitchen.api."""
    assert kitchen_imports(ENGINE / "cli.py") <= PUBLIC


def test_appliance_names_match_the_brief_wording():
    """Названия техники в КП — слова брифа; скилл переносим и не импортируется, поэтому сверка по тексту."""
    source = (ROOT / "skills" / "kitchen-brief" / "scripts" / "check_brief.py").read_text(encoding="utf-8")
    block = re.search(r"^APPLIANCE_NAMES = \{(.*?)^\}", source, re.DOTALL | re.MULTILINE).group(1)
    brief = dict(re.findall(r'"(\w+)": "([^"]+)"', block))
    assert brief and {k: APPLIANCE_NAMES[k] for k in brief} == brief


# Тикет 47: склонение, целое число и поворот в оси комнаты живут в одном месте.
SINGLE_HOME = {"plural": "format.py", "num": "format.py", "turn": "geometry.py"}
# Свой, не дубль: `scene3d._num` — обёртка `format.num` с четырьмя знаками.
OWN_NUM = {"scene3d.py"}


def test_shared_helpers_are_defined_once():
    problems = []
    for path in sorted(ENGINE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.FunctionDef):
                continue
            name = node.name.lstrip("_")
            if name not in SINGLE_HOME or path.name == SINGLE_HOME[name]:
                continue
            if node.name == "_num" and path.name in OWN_NUM:
                continue
            problems.append(f"{path.name}: def {node.name}")
    assert not problems, "Склонение, число и поворот берутся из format.py и geometry.py:\n" + "\n".join(
        problems
    )


# Тикет 47: «кто стоит в модуле или пустом месте» отвечает `Project.appliances_at`. Фильтр по `.module ==` над
# списком техники проекта разрешён только в `project.py`; `parts.py` и `tall.py` фильтруют не проект, а свои
# списки (`ctx.appliances`, `project_appliances` — техника, переданная сборщику шаблона).
FILTER_OK = {"project.py", "parts.py", "tall.py"}


def test_appliance_filter_by_module_lives_only_in_project():
    problems = []
    for path in sorted(ENGINE.glob("*.py")):
        if path.name in FILTER_OK:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"for (\w+) in project\.appliances\b[^\]\n]*(?:\n[^\]]*?)?\b\1\.module ==", text
        ):
            problems.append(f"{path.name}: {match.group(0)[:60]!r}")
    assert not problems, "Техника места — Project.appliances_at:\n" + "\n".join(problems)
