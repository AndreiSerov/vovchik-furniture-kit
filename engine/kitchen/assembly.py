"""Сборочный лист (4.4, тикет 22): по модулям детали, фурнитура «цех» и шаги сборки.

Шаги собираются из модели соединений, дверей, полок и ящиков модуля (`parts.AssemblyPlan`) и фурнитуры модуля;
порядок этапов — ключ `сборка` construction.yaml. Чисел и формул здесь нет: количества берутся из расчёта деталей.
"""

from collections import defaultdict

from pydantic import BaseModel

from kitchen.models import Workshop
from kitchen.parts import (
    APPLIANCE_NAMES,
    BACK_PANEL,
    DRAWER_BOTTOM,
    TEMPLATES,
    ModuleParts,
    module_dims,
    resolve_materials,
)
from kitchen.project import Project
from kitchen.quote import plural

__all__ = [
    "AssemblyHardware",
    "AssemblyModule",
    "AssemblyPart",
    "build_assembly",
]

UNIT = "шт"
NO_CARCASS_NOTE = "корпуса нет, фасад {facades} крепится к посудомойке на объекте"


class AssemblyPart(BaseModel):
    """Строка таблицы деталей: «П1-бок-Л · боковина · 2100×560×16 мм · материал»."""

    mark: str
    name: str
    size: str
    material: str


class AssemblyHardware(BaseModel):
    """Позиция фурнитуры «цех» с количеством; название — из `hardware.yaml`."""

    id: str
    name: str
    quantity: int
    unit: str = UNIT


class AssemblyModule(BaseModel):
    """Блок сборочного листа одного модуля. `carcass` — у модуля без корпуса (фасад ПММ) нет ни деталей
    в таблице, ни шагов: печатается одна строка `title: note`."""

    label: str
    title: str
    carcass: bool
    note: str | None = None
    parts: list[AssemblyPart]
    hardware: list[AssemblyHardware]
    appliances: list[str]
    steps: list[str]


def _ids(ids: list[str]) -> str:
    return ", ".join(ids)


def _steps(
    stages: list[str], ws: Workshop, built: ModuleParts, handles: str, name_of: dict[str, tuple[str, str]]
) -> list[str]:
    """Строки по этапам в порядке `сборка`; этап без строк у модуля пропускается."""
    c = ws.construction
    plan, hardware = built.plan, built.hardware
    facades = [p.id for p in built.parts if p.name == "фасад"]

    def installed_here(item: str) -> bool:
        return hardware.get(item, 0) > 0 and name_of[item][1] == "цех"

    lines: dict[str, list[str]] = defaultdict(list)
    by_end: dict[str, list[str]] = {}
    for joint in plan.joints:
        by_end.setdefault(joint.end, []).append(joint.plate)
    for end, plates in by_end.items():
        holes = next(p for p in built.parts if p.id == end).holes or []
        n = sum(1 for h in holes if h.purpose == "confirmat" and h.surface == "end")
        lines["корпус"].append(
            f"{end} к {_ids(plates)} — {plural(n, 'конфирмат', 'конфирмата', 'конфирматов')}"
        )
    back = next((p for p in built.parts if p.name == BACK_PANEL), None)
    if back is not None and plan.back_screws:
        lines["задняя_стенка"].append(
            f"Задняя стенка {back.id} — {plural(plan.back_screws, 'саморез', 'самореза', 'саморезов')}"
        )
    pins = hardware.get(c.furniture.shelf_pins, 0)
    if plan.shelves and pins:
        lines["полки"].append(f"Полкодержатели — {pins} шт, полки: {_ids([s.shelf for s in plan.shelves])}")
    drawer_set = c.drawers.hardware
    if plan.drawers and hardware.get(drawer_set, 0):
        bottoms = [p.id for p in built.parts if p.name == DRAWER_BOTTOM]
        lines["ящики"].append(
            f"Направляющие {name_of[drawer_set][0]} — {hardware[drawer_set]} компл., ящики: {_ids(bottoms)}"
        )
    hinges = c.furniture.hinges
    if plan.doors and hardware.get(hinges, 0):
        lines["фасады"].append(
            f"Петли {name_of[hinges][0]} — {hardware[hinges]} шт, "
            f"фасады: {_ids([d.facade for d in plan.doors])}"
        )
    lift = c.upper_rules.lift
    if hardware.get(lift, 0):
        lines["фасады"].append(f"{name_of[lift][0]} — {hardware[lift]} шт, фасады: {_ids(facades)}")
    if installed_here(handles):  # ручки и опоры ставятся на объекте; в цехе — только если мастер так отметил
        lines["ручки"].append(
            f"Ручки {name_of[handles][0]} — {hardware[handles]} шт, фасады: {_ids(facades)}"
        )
    if installed_here(c.furniture.legs):
        legs = c.furniture.legs
        lines["опоры"].append(f"Опоры {name_of[legs][0]} — {hardware[legs]} шт")
    return [line for stage in stages for line in lines.get(stage, [])]


def build_assembly(project: Project, built: list[ModuleParts], ws: Workshop) -> list[AssemblyModule]:
    """Блоки модулей в порядке стен и рядов (как состав КП); пенал записан один раз, в своём ряду."""
    by_label = {m.module: m for m in built}
    name_of = {i.id: (i.name, i.installed) for i in ws.hardware.items}
    materials = {m.id: m.name for m in [*ws.materials.sheets, *ws.materials.facades]}
    blocks = []
    for wall in project.walls:
        for row in wall.rows:
            for module in row.modules:
                template = TEMPLATES[module.template]
                module_parts = by_label[module.label]
                dims = module_dims(module, row.kind, ws)
                parts = [
                    AssemblyPart(
                        mark=p.id,
                        name=p.name,
                        size=f"{p.length}×{p.width}×{p.thickness} мм",
                        material=materials.get(p.material, p.material),
                    )
                    for p in module_parts.parts
                ]
                kinds = [APPLIANCE_NAMES[a.type] for a in project.appliances if a.module == module.label]
                common = {
                    "label": module.label,
                    "carcass": template.carcass,
                    "parts": parts,
                    "appliances": kinds,
                }
                if not template.carcass:
                    facades = [p.mark for p in parts if p.name == "фасад"]
                    blocks.append(
                        AssemblyModule(
                            title=f"{module.label} — {template.title}, {dims.width} мм",
                            note=NO_CARCASS_NOTE.format(facades=_ids(facades)),
                            hardware=[],
                            steps=[],
                            **common,
                        )
                    )
                    continue
                handles = resolve_materials(module, row, project.materials).handles
                blocks.append(
                    AssemblyModule(
                        title=f"{module.label} — {template.title}, {dims.width}×{dims.height}×{dims.depth} мм",
                        hardware=[
                            AssemblyHardware(id=i.id, name=i.name, quantity=module_parts.hardware[i.id])
                            for i in ws.hardware.items
                            if module_parts.hardware.get(i.id, 0) > 0 and i.installed == "цех"
                        ],
                        steps=_steps(ws.construction.assembly, ws, module_parts, handles, name_of),
                        **common,
                    )
                )
    return blocks
