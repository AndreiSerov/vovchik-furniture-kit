"""Схема project.json: стены, ряды, модули, материалы и техника.

Ключи английские (GLOSSARY.md); неизвестные ключи отклоняются.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from kitchen.errors import OrderError
from kitchen.models import Num
from kitchen.workshop import translate

__all__ = ["ModuleSpec", "Project", "ProjectMaterials", "Row", "Wall", "parse_project"]


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class MaterialOverrides(Section):
    body: str | None = None
    facades: str | None = None
    facade_color: str | None = None
    handles: str | None = None
    countertop: str | None = None


class Contents(Section):
    doors: StrictInt | None = Field(default=None, ge=0)
    shelves: StrictInt | None = Field(default=None, ge=0)
    drawers: StrictInt | None = Field(default=None, ge=0)
    inserts: list[str] = Field(default_factory=list)
    lift: str | None = None


class Corner(Section):
    adjacent_wall: str


class ModuleSpec(Section):
    """Модуль ряда; пенал указывается в одном ряду и занимает оба."""

    label: str
    template: str
    width: Num
    height: Num | None = None
    depth: Num | None = None
    shelves: StrictInt = 1  # совместимость с проектами тикета 05
    materials: MaterialOverrides | None = None
    contents: Contents | None = None
    without_back: bool = False
    corner: Corner | None = None


class Row(Section):
    kind: Literal["lower", "upper"]
    modules: list[ModuleSpec] = Field(default_factory=list)
    materials: MaterialOverrides | None = None


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
    width: Num  # габариты корпуса прибора (справочно): на нишу не влияют
    model: str | None = None
    height: Num | None = None
    depth: Num | None = None
    niche_width: Num | None = None  # размеры ниши (встраивания): строит модуль, недостающие — из каталога
    niche_height: Num | None = None
    niche_depth: Num | None = None
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
