"""Проверки проекта (1.6): находки с кодом, уровнем, значением и пределом.

Проверки ничего не бросают на ошибках проекта: каждая проблема — находка, чтобы мастер
видел все сразу. Пороги и допуски — из мастерской (`construction.yaml`, `equipment.yaml`).
"""

import re
from typing import Literal

from pydantic import BaseModel

from kitchen import formulas
from kitchen.brief import BriefAppliance
from kitchen.brief import appliances as brief_appliances
from kitchen.errors import KitchenError, WorkshopError
from kitchen.models import Workshop
from kitchen.parts import (
    APPLIANCE_ROWS,
    BACK_PANEL,
    TEMPLATES,
    ModuleDims,
    TemplateOut,
    accepts,
    build_module,
    built_niche,
    module_dims,
    niche_item,
)
from kitchen.project import ModuleSpec, Project

__all__ = ["Finding", "check_project"]

_KIND_RU = {"lower": "нижний", "upper": "верхний"}


class Finding(BaseModel):
    """Находка проверки. `subject` — модуль, деталь или стена; `value` и `limit` — числа проверки."""

    code: str
    level: Literal["error", "warning"]
    subject: str
    message: str
    value: float | None = None
    limit: float | None = None


def _num(x: float) -> int | float:
    return int(x) if float(x).is_integer() else x


def _row_sums(project: Project, ws: Workshop) -> list[Finding]:
    """Ширины модулей ряда + угловые смещения с соседней стены (+ пенал из другого ряда) = длина стены."""
    rules = ws.construction.corner_rules
    offsets = {"lower": rules.lower_offset, "upper": rules.upper_offset}
    extra: dict[tuple[str, str], float] = {}  # (стена, вид ряда) -> смещения и пеналы
    tall_widths: dict[str, float] = {}
    found: list[Finding] = []
    walls = {w.label for w in project.walls}
    for wall in project.walls:
        for row in wall.rows:
            for m in row.modules:
                if m.template == "tall_oven":
                    tall_widths[wall.label] = tall_widths.get(wall.label, 0) + m.width
                if m.corner is None:
                    continue
                adjacent = m.corner.adjacent_wall
                if adjacent == wall.label or adjacent not in walls:
                    found.append(
                        Finding(
                            code="corner_wall",
                            level="error",
                            subject=m.label,
                            message=f"Модуль {m.label}: соседняя стена «{adjacent}» должна существовать и отличаться от стены «{wall.label}».",
                        )
                    )
                    continue
                offset = m.corner.offset if m.corner.offset is not None else offsets[row.kind]
                extra[(adjacent, row.kind)] = extra.get((adjacent, row.kind), 0) + offset
    for wall in project.walls:
        for row in wall.rows:
            total = sum(m.width for m in row.modules) + extra.get((wall.label, row.kind), 0)
            if not any(m.template == "tall_oven" for m in row.modules):
                total += tall_widths.get(wall.label, 0)
            if abs(total - wall.length) > rules.wall_tolerance:
                found.append(
                    Finding(
                        code="row_sum",
                        level="error",
                        subject=wall.label,
                        message=(
                            f"Стена {wall.label}: {_KIND_RU[row.kind]} ряд занимает {_num(total)} мм "
                            f"(модули, угловые смещения, пенал), а длина стены {_num(wall.length)} мм: "
                            f"разница {_num(abs(total - wall.length))} мм (допуск {rules.wall_tolerance} мм)."
                        ),
                        value=total,
                        limit=wall.length,
                    )
                )
    return found


def _drawer_depth(module: ModuleSpec, dims: ModuleDims, ws: Workshop) -> Finding | None:
    """Внутренняя глубина — глубина корпуса: задняя стенка накладная, фасад снаружи."""
    guide = ws.construction.tandembox.guide_length
    if not TEMPLATES[module.template].drawers or dims.depth >= guide:
        return None
    return Finding(
        code="drawer_depth",
        level="error",
        subject=module.label,
        message=(
            f"Модуль {module.label}: внутренняя глубина {dims.depth} мм меньше направляющей ящика {_num(guide)} мм."
        ),
        value=dims.depth,
        limit=guide,
    )


def _pair(project: Project, brief: list[BriefAppliance], ws: Workshop, found: list[Finding]):
    """Сверка состава. В каждом виде техники сначала сопоставление по модели, затем по порядку.
    Возвращает прибор проекта → прибор брифа (или `None`)."""
    paired: dict[int, BriefAppliance | None] = {}
    leftover_brief: dict[str, list[BriefAppliance]] = {}
    for type_ in dict.fromkeys([b.type for b in brief] + [a.type for a in project.appliances]):
        items = [b for b in brief if b.type == type_]
        mine = [a for a in project.appliances if a.type == type_]
        rest = []
        for a in mine:
            hit = next((b for b in items if a.model and b.model and a.model.lower() == b.model.lower()), None)
            if hit:
                items.remove(hit)
                paired[id(a)] = hit
            else:
                rest.append(a)
        by_order = bool(rest and items)
        for a in rest:
            paired[id(a)] = items.pop(0) if items else None
        if by_order and (len([b for b in brief if b.type == type_]) > 1 or len(mine) > 1):
            found.append(
                Finding(
                    code="appliance_pairing",
                    level="warning",
                    subject=type_,
                    message=f"Несколько «{type_}» без моделей, сопоставление по порядку.",
                )
            )
        leftover_brief[type_] = items
    for a in project.appliances:
        if paired[id(a)] is None:
            found.append(
                Finding(
                    code="appliance_not_in_brief",
                    level="warning",
                    subject=a.module,
                    message=f"Техника «{a.type}» в модуле {a.module} есть в проекте, но не в брифе заказа.",
                )
            )
    for items in leftover_brief.values():
        for b in items:
            if not b.built_in:
                continue
            if any(accepts(name, b.type, ws) for name in TEMPLATES):
                found.append(
                    Finding(
                        code="appliance_unbound",
                        level="error",
                        subject=b.label,
                        message=f"Встраиваемая техника из брифа «{b.label}» не привязана ни к одному модулю проекта.",
                    )
                )
            else:
                found.append(
                    Finding(
                        code="appliance_no_template",
                        level="warning",
                        subject=b.label,
                        message=f"Для встраиваемой техники «{b.label}» шаблона под «{b.type}» нет: модуль придётся описать вручную.",
                    )
                )
    return paired


def _built_niche(appliance, module: ModuleSpec, d: ModuleDims, item, ws: Workshop):
    """Ниша, которую модуль реально строит: той же функцией, что шаблон (проект, иначе каталог);
    у остальных — пространство модуля (ширина и глубина корпуса; у ПММ — модуль целиком)."""
    if item is not None:
        return built_niche(appliance, item)
    if not TEMPLATES[module.template].carcass:
        return d.width, d.height + ws.construction.legs.height, d.depth
    # прибор «в столешницу» шире корпуса модуля может быть: его ширина не про нишу корпуса
    on_top = appliance.type in ws.construction.countertop_appliances
    return (None if on_top else _inner_width(d, ws)), None, d.depth


def _inner_width(dims: ModuleDims, ws: Workshop) -> int:
    """Внутренняя ширина корпуса — по формуле мастерской (`ящики.внутренняя_ширина`)."""
    return int(
        formulas.evaluate(ws.construction.drawers.inner_width, W=dims.width, T=ws.construction.case_thickness)
    )


def _niches(
    project: Project,
    ws: Workshop,
    dims: dict[str, tuple[ModuleSpec, ModuleDims]],
    brief: list[BriefAppliance] | None,
    failed: set[str],
) -> list[Finding]:
    """Требования из брифа (размеры встраивания, иначе типовая ниша каталога) против ниши,
    которую строит модуль. Размеры корпуса прибора с нишей не сравниваются."""
    found: list[Finding] = []
    if brief is None:
        found.append(
            Finding(
                code="brief_missing",
                level="warning",
                subject=project.order,
                message="Брифа нет, техника не сверена с клиентом: ниши проверены по типовым значениям мок-каталога.",
            )
        )
        paired: dict[int, BriefAppliance | None] = {id(a): None for a in project.appliances}
    else:
        paired = _pair(project, brief, ws, found)
        found += [
            Finding(
                code="brief_dimensions", level="warning", subject=b.label, message=f"Бриф, «{b.label}»: {w}."
            )
            for b in brief
            for w in b.warnings
        ]
    for a in project.appliances:
        if a.module not in dims:
            found.append(
                Finding(
                    code="appliance_module",
                    level="error",
                    subject=a.module,
                    message=f"Техника «{a.type}» привязана к модулю {a.module}, которого нет в проекте.",
                )
            )
            continue
        module, d = dims[a.module]
        template = TEMPLATES[module.template]
        if template.row_kind not in APPLIANCE_ROWS.get(a.type, (template.row_kind,)):
            found.append(
                Finding(
                    code="appliance_wrong_module",
                    level="error",
                    subject=a.module,
                    message=f"Модуль {a.module}: шаблон {module.template} не подходит для техники «{a.type}» (другой ряд).",
                )
            )
            continue
        if not accepts(module.template, a.type, ws):
            found.append(
                Finding(
                    code="appliance_no_niche",
                    level="warning",
                    subject=a.module,
                    message=f"Модуль {a.module}: шаблон {module.template} не строит нишу под «{a.type}» — проверьте вручную.",
                )
            )
        b = paired[id(a)]
        item = niche_item(module.template, a.type, ws)
        try:
            built = _built_niche(a, module, d, item, ws)
        except KitchenError as e:  # дробные размеры техники; расчёт модуля мог не запускаться
            if a.module not in failed:  # иначе тот же сбой уже назван при расчёте модуля
                found.append(Finding(code="appliance_size", level="error", subject=a.module, message=str(e)))
            continue
        # требование: размеры встраивания из брифа, недостающие — типовая ниша каталога
        width = (b.width if b and b.width else None) or a.width
        cat = item or next(
            (i for i in ws.appliances.items if i.type == a.type and i.nominal_width == width), None
        )
        asked = (b.niche_width, b.niche_height, b.niche_depth) if b else (None, None, None)
        typical = (cat.niche_width, cat.niche_height, cat.niche_depth) if cat else (None, None, None)
        required = tuple(
            mine if mine is not None else other for mine, other in zip(asked, typical, strict=True)
        )
        if cat is None and None in required:
            found.append(
                Finding(
                    code="appliance_no_data",
                    level="warning",
                    subject=a.module,
                    message=(
                        f"Техника «{a.type}» шириной {_num(a.width)} мм в модуле {a.module}: такого типоразмера нет "
                        "в appliances.yaml и в брифе нет размеров встраивания, нишу можно проверить только "
                        "по известным размерам."
                    ),
                )
            )
        if b and any(x is None and y is not None for x, y in zip(asked, typical, strict=True)):
            found.append(
                Finding(
                    code="appliance_catalog_dims",
                    level="warning",
                    subject=a.module,
                    message=(
                        f"Размеры встраивания «{b.label}» не заданы в брифе, взята типовая ниша из мок-каталога — "
                        "уточните размеры встраивания по инструкции модели."
                    ),
                )
            )
        # шаблон строит нишу по прибору из проекта, а бриф говорит другое: пусть мастер перенесёт в проект
        differs = any(x is not None and x != y for x, y in zip(asked, built, strict=True))
        if b and item is not None and differs:
            found.append(
                Finding(
                    code="niche_not_in_project",
                    level="warning",
                    subject=a.module,
                    message=(
                        f"Модуль {a.module}, «{b.label}»: размеры встраивания в брифе отличаются от ниши, "
                        "которую строит модуль, — перенесите размеры встраивания из брифа в проект."
                    ),
                )
            )
        for name, need, have in zip(("ширина", "высота", "глубина"), required, built, strict=True):
            if need is None or have is None or need <= have:
                continue
            found.append(
                Finding(
                    code="niche_too_small",
                    level="error",
                    subject=a.module,
                    message=(
                        f"Модуль {a.module}: {name} ниши под «{b.label if b else a.type}» по брифу (или каталогу) "
                        f"{_num(need)} мм, а модуль строит нишу {_num(have)} мм."
                    ),
                    value=_num(need),
                    limit=_num(have),
                )
            )
    return found


def _modules(project: Project):
    for wall in project.walls:
        for row in wall.rows:
            for module in row.modules:
                yield row, module


def _build(project: Project, ws: Workshop, found: list[Finding]):
    """Габариты и детали каждого модуля; сбой расчёта модуля — находка, а не обрыв."""
    dims: dict[str, tuple[ModuleSpec, ModuleDims]] = {}
    built: dict[str, TemplateOut] = {}
    for row, module in _modules(project):
        try:
            d = module_dims(module, row.kind, ws)
        except KitchenError as e:
            found.append(_build_failure(module, e))
            continue
        dims[module.label] = (module, d)
        depth = _drawer_depth(module, d, ws)
        if depth:
            found.append(depth)
            continue  # расчёт упёрся бы в ту же глубину
        try:
            built[module.label] = build_module(module, row, project.materials, ws, project.appliances)
        except KitchenError as e:
            found.append(_build_failure(module, e))
    return dims, built


def _build_failure(module: ModuleSpec, error: KitchenError) -> Finding:
    return Finding(
        code="module_build", level="error", subject=module.label, message="\n".join(error.problems)
    )


def _along_texture(sheet, ws_sheet: tuple[float, float]) -> tuple[float, float]:
    """(вдоль текстуры, поперёк) для листа с текстурой «вдоль N»."""
    match = re.fullmatch(r"вдоль\s+(\d+)", sheet.texture.strip()) if isinstance(sheet.texture, str) else None
    if match is None or float(match.group(1)) not in ws_sheet:
        raise WorkshopError(
            f"materials.yaml: у материала «{sheet.id}» текстура «{sheet.texture}» — "
            f"ожидалось «вдоль N», где N — одна из сторон листа {ws_sheet[0]:g}×{ws_sheet[1]:g}."
        )
    along = float(match.group(1))
    return along, ws_sheet[1] if along == ws_sheet[0] else ws_sheet[0]


def _sheet_fit(built: dict[str, TemplateOut], ws: Workshop) -> list[Finding]:
    """Деталь в рабочем поле листа (лист минус обрезка с двух сторон). Текстурная — без поворота."""
    sheets = {m.id: m for m in ws.materials.sheets}
    trim = 2 * ws.equipment.sheet.perimeter_trim
    found: list[Finding] = []
    for out in built.values():
        for part in out.parts:
            sheet = sheets.get(part.material)
            if sheet is None:  # фасады и столешница — не листовые детали
                continue
            if part.texture:
                along, across = (v - trim for v in _along_texture(sheet, sheet.sheet))
                limits = (("длина (вдоль текстуры)", part.length, along), ("ширина", part.width, across))
            else:
                big, small = (v - trim for v in sorted(sheet.sheet, reverse=True))
                limits = (("длина", part.length, big), ("ширина", part.width, small))
                if part.length <= big and part.width <= small:
                    continue
            for name, value, limit in limits:
                if value > limit:
                    found.append(
                        Finding(
                            code="part_exceeds_sheet",
                            level="error",
                            subject=part.id,
                            message=(
                                f"Деталь {part.id}: {name} {_num(value)} мм не помещается в рабочее поле листа "
                                f"{_num(limit)} мм (лист {sheet.sheet[0]:g}×{sheet.sheet[1]:g} минус обрезка "
                                f"по {ws.equipment.sheet.perimeter_trim:g} мм с каждой стороны)."
                            ),
                            value=value,
                            limit=limit,
                        )
                    )
    return found


def _no_back(built: dict[str, TemplateOut], dims: dict[str, tuple[ModuleSpec, ModuleDims]]) -> list[Finding]:
    """Корпус без задней стенки. Модуль без корпуса (только фасад) не в счёт."""
    return [
        Finding(
            code="no_back_panel",
            level="warning",
            subject=label,
            message=f"Модуль {label}: без задней стенки — проверьте, что так и задумано.",
        )
        for label, out in built.items()
        if TEMPLATES[dims[label][0].template].carcass and not any(p.name == BACK_PANEL for p in out.parts)
    ]


def check_project(project: Project, ws: Workshop, brief: dict | None = None) -> list[Finding]:
    """Все проверки проекта, ошибки и предупреждения вместе. `brief` — последний бриф заказа (или `None`)."""
    found = _row_sums(project, ws)
    dims, built = _build(project, ws, found)
    failed = {f.subject for f in found if f.code == "module_build"}
    found += _niches(project, ws, dims, None if brief is None else brief_appliances(brief), failed)
    found += _sheet_fit(built, ws)
    found += _no_back(built, dims)
    return found
