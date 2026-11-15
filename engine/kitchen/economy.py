"""Вариант «экономия» (тикет 17): правила замен из `economy.yaml` применяются к копии проекта.

Здесь только замена модулей в копии и проверка, что заменённый модуль строится. Цену копии считает тот же
движок, что и смету: детали -> раскрой -> закупка -> смета (`kitchen.api`). Копия версией не становится.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel

from kitchen.errors import OrderError
from kitchen.models import EconomyChange, EconomyRule, EconomyWhere, Workshop
from kitchen.parts import build_module
from kitchen.project import Contents, ModuleSpec, Project

__all__ = ["Applied", "EconomyLine", "EconomyVariant", "apply_rule"]


class EconomyLine(BaseModel):
    """Одна замена: правило, модули, цена с одной этой заменой и разница с ценой «как в проекте»."""

    id: str
    name: str
    client_text: str
    modules: list[str]
    price: int
    saving: int


class EconomyVariant(BaseModel):
    """Вариант «экономия»: цена как в проекте, цена со всеми предложенными заменами, строки замен.

    `not_offered` — замены, которые не удешевляют (разница не больше нуля): клиенту их не показывают,
    мастеру они видны в смете. `problems` — модули, на которых замена не строится, по метке модуля."""

    price_as_is: int
    price: int
    saving: int
    replacements: list[EconomyLine]
    not_offered: list[EconomyLine]
    problems: list[str]


@dataclass
class Applied:
    """Результат применения одного правила: копия проекта, замененные модули, модули, где замена не строится."""

    project: Project
    modules: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _matches(module: ModuleSpec, where: EconomyWhere) -> bool:
    if module.template != where.template:
        return False
    contents = module.contents
    if where.lift is not None and bool(contents and contents.lift) != where.lift:
        return False
    return where.insert is None or where.insert in (contents.inserts if contents else [])


def _changed(module: ModuleSpec, change: EconomyChange) -> ModuleSpec:
    """Копия модуля с заменой. Новый шаблон собирает наполнение заново: остаётся только указанное в `на`."""
    contents = module.contents
    update: dict = {}
    if change.template is not None:
        update["template"] = change.template
        contents = None
    if change.shelves is not None or change.remove_lift or change.remove_insert:
        data = contents.model_dump() if contents else {}
        if change.shelves is not None:
            data["shelves"] = change.shelves
        if change.remove_lift:
            data["lift"] = None
        if change.remove_insert:
            data["inserts"] = [i for i in data.get("inserts", []) if i != change.remove_insert]
        contents = Contents(**data)
    update["contents"] = contents
    return module.model_copy(update=update)


def apply_rule(project: Project, rule: EconomyRule, ws: Workshop) -> Applied:
    """Правило ко всем подходящим модулям копии. Модуль, на котором замена не строится, остаётся как в проекте,
    про него — сообщение с меткой и причиной; остальные модули заменяются. Модуль, который замена не меняет, —
    не в списке."""
    copy = project.model_copy(deep=True)
    applied = Applied(copy)
    for wall in copy.walls:
        for row in wall.rows:
            for index, module in enumerate(row.modules):
                case = next((c for c in rule.cases if _matches(module, c.where)), None)
                if case is None:
                    continue
                changed = _changed(module, case.change)
                if changed == module:
                    continue
                row.modules[index] = changed
                try:
                    build_module(changed, row, copy.materials, ws, copy.appliances)
                except OrderError as e:
                    row.modules[index] = module
                    applied.problems.append(
                        f"{module.label}: замена «{rule.name}» не строится, модуль оставлен как в проекте. "
                        + " ".join(e.problems)
                    )
                else:
                    applied.modules.append(module.label)
    return applied
