"""Схема проекта: стены, ряды, модули, материалы по умолчанию.

Минимум тикета 05 — ровно столько, сколько нужно проекту из одного модуля
`base_door`; полная схема (техника, наполнение, пенал в обоих рядах, угловые
модули, переопределения материалов) — тикет 06. Ключи `project.json` — английские
(GLOSSARY.md), поэтому алиасы не нужны, а «неизвестный ключ» ловится `extra="forbid"`.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kitchen.errors import OrderError
from kitchen.models import Num
from kitchen.workshop import translate

__all__ = ["ModuleSpec", "Project", "ProjectMaterials", "Row", "Wall", "parse_project"]


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModuleSpec(Section):
    """Модуль ряда: метка, шаблон, ширина; высота и глубина — по ряду, если не заданы."""

    label: str
    template: str
    width: Num
    height: Num | None = Field(default=None)
    depth: Num | None = Field(default=None)
    shelves: int = 1  # наполнение: несколько полок придёт с полной схемой (тикет 06)


class Row(Section):
    """Ряд модулей на стене, по порядку от начала стены."""

    kind: Literal["lower", "upper"]
    modules: list[ModuleSpec] = Field(default_factory=list)


class Wall(Section):
    """Стена помещения с меткой (`A`, `Б`)."""

    label: str
    length: Num
    rows: list[Row] = Field(default_factory=list)


class ProjectMaterials(Section):
    """Материалы по умолчанию на весь проект (id из materials.yaml и hardware.yaml)."""

    body: str
    facades_lower: str
    facades_upper: str
    handles: str


class Project(Section):
    order: str
    ceiling_height: Num
    walls: list[Wall]
    materials: ProjectMaterials


def parse_project(data: object) -> Project:
    """Проект из JSON. Ошибки схемы — OrderError по-русски, со всеми находками сразу."""
    if not isinstance(data, dict):
        raise OrderError("Проект должен быть JSON-объектом ({...}), а не списком или значением.")
    try:
        return Project.model_validate(data)
    except ValidationError as e:
        raise OrderError(translate("project.json", e)) from e
