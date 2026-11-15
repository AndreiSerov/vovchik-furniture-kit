"""Схема project.json: стены, ряды, модули, материалы и техника.

Ключи английские (GLOSSARY.md); неизвестные ключи отклоняются.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, StrictInt, Tag, ValidationError
from pydantic.json_schema import SkipJsonSchema

from kitchen.errors import OrderError
from kitchen.models import Num, PosNum
from kitchen.workshop import translate

__all__ = ["Gap", "ModuleSpec", "Project", "ProjectMaterials", "Row", "Wall", "is_gap", "parse_project"]


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class MaterialOverrides(Section):
    body: str | None = None
    facades: str | None = None
    facade_color: str | None = None
    handles: str | None = None
    # Столешница одна на проект (`materials.countertop`): у ряда и модуля её в схеме нет. Ключ разбирается, чтобы
    # проверки назвали его ошибкой `countertop_override`, а не «неизвестным ключом».
    countertop: SkipJsonSchema[str | None] = None


class Contents(Section):
    doors: StrictInt | None = Field(default=None, ge=0)
    shelves: StrictInt | None = Field(default=None, ge=0)
    drawers: StrictInt | None = Field(default=None, ge=0)
    inserts: list[str] = Field(default_factory=list)
    lift: str | None = None
    # сторона петель одной двери (решение мастера, тикет 19); по умолчанию слева, у угловых — по месту в ряду
    hinge_side: Literal["left", "right"] | None = None


class Corner(Section):
    adjacent_wall: str


def _template_names(schema: dict) -> None:
    """В JSON Schema `template` — перечень реестра `TEMPLATES`. Реестр импортируется при выгрузке схемы:
    `parts` сам читает этот модуль. Неизвестный шаблон схему проекта проходит и получает находку
    `module_build` в проверках, поэтому на разбор перечень не влияет."""
    from kitchen.parts import TEMPLATES

    schema["enum"] = sorted(TEMPLATES)


class ModuleSpec(Section):
    """Модуль ряда; пенал указывается в одном ряду и занимает оба."""

    label: str
    template: str = Field(json_schema_extra=_template_names)
    width: PosNum
    height: PosNum | None = None
    depth: PosNum | None = None
    shelves: StrictInt = Field(default=1, ge=0)  # совместимость с проектами тикета 05
    materials: MaterialOverrides | None = None
    contents: Contents | None = None
    without_back: bool = False
    corner: Corner | None = None


class Gap(Section):
    """Пустое место ряда: участок заданной ширины без деталей (под отдельностоящую технику или над окном).
    Запись того же списка `modules`; признак — `gap: true`, шаблона нет."""

    label: str
    gap: Literal[True]
    width: PosNum
    under_countertop: bool = False  # место под общей столешницей: входит в её длину, но не в цоколь


def is_gap(entry: "ModuleSpec | Gap") -> bool:
    """Запись ряда — пустое место, а не модуль."""
    return isinstance(entry, Gap)


def _entry_kind(value: object) -> str:
    if isinstance(value, dict):
        return "gap" if "gap" in value else "module"
    return "gap" if isinstance(value, Gap) else "module"


RowEntry = Annotated[
    Annotated[ModuleSpec, Tag("module")] | Annotated[Gap, Tag("gap")], Discriminator(_entry_kind)
]


class Row(Section):
    kind: Literal["lower", "upper"]
    modules: list[RowEntry] = Field(default_factory=list)  # модули и пустые места по порядку вдоль стены
    materials: MaterialOverrides | None = None

    @property
    def cabinets(self) -> list[ModuleSpec]:
        """Только модули ряда, без пустых мест: то, что превращается в детали."""
        return [m for m in self.modules if not isinstance(m, Gap)]


class Wall(Section):
    label: str
    length: Num
    rows: list[Row] = Field(default_factory=list)


class ProjectMaterials(Section):
    body: str
    facades_lower: str
    facades_upper: str
    handles: str
    countertop: str | None = None
    facades_lower_color: str | None = None
    facades_upper_color: str | None = None


class Appliance(Section):
    type: str
    width: PosNum  # габариты корпуса прибора (справочно): на нишу не влияют
    model: str | None = None
    height: PosNum | None = None
    depth: PosNum | None = None
    niche_width: PosNum | None = None  # размеры ниши (встраивания): строит модуль, недостающие — из каталога
    niche_height: PosNum | None = None
    niche_depth: PosNum | None = None
    module: str


class Project(Section):
    order: str
    version: StrictInt | None = Field(default=None, ge=1)
    ceiling_height: Num
    walls: list[Wall]
    materials: ProjectMaterials
    led: bool = False
    appliances: list[Appliance] = Field(default_factory=list)


def parse_project(data: object) -> Project:
    """Проект из JSON. Ошибки схемы — OrderError по-русски, со всеми находками сразу."""
    if not isinstance(data, dict):
        raise OrderError("Проект должен быть JSON-объектом ({...}), а не списком или значением.")
    try:
        return Project.model_validate(data)
    except ValidationError as e:
        raise OrderError(translate("project.json", e)) from e
