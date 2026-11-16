"""Проверки проекта (1.6): находки с кодом, уровнем, значением и пределом.

Проверки ничего не бросают на ошибках проекта: каждая проблема — находка, чтобы мастер
видел все сразу. Пороги и допуски — из мастерской (`construction.yaml`, `equipment.yaml`).
"""

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel

from kitchen import formulas
from kitchen.brief import BriefAppliance
from kitchen.brief import appliances as brief_appliances
from kitchen.common import (
    corner_modules,
    corner_offsets,
    corner_wall_message,
    is_tall,
    pair_by_model_then_order,
    row_layouts,
    row_positions,
)
from kitchen.errors import KitchenError, WorkshopError
from kitchen.format import num
from kitchen.models import SheetMaterial, Workshop
from kitchen.parts import (
    APPLIANCE_NAMES,
    APPLIANCE_ROWS,
    BACK_PANEL,
    ROW_GEN,
    ROW_NOM,
    TEMPLATES,
    ModuleDims,
    Part,
    PartTooSmallError,
    TemplateOut,
    accepts,
    build_module,
    built_niche,
    drawer_depth_shortfall,
    gap_name,
    gap_width,
    module_dims,
    niche_item,
    part_too_small_message,
    unknown_template_error,
)
from kitchen.project import Appliance, Gap, ModuleSpec, Project, is_gap

__all__ = ["Finding", "along_texture", "check_project", "part_fit_findings"]


class Finding(BaseModel):
    """Находка проверки. `subject` — модуль, деталь или стена; `value` и `limit` — числа проверки."""

    code: str
    level: Literal["error", "warning"]
    subject: str
    message: str
    value: float | None = None
    limit: float | None = None


def _row_sums(project: Project, ws: Workshop) -> list[Finding]:
    """Ширины модулей ряда + угловые смещения с соседней стены + пеналы стены из других рядов = длина стены."""
    rules = ws.construction.corner_rules
    found: list[Finding] = []
    walls = {w.label for w in project.walls}
    for wall, _, m, adjacent in corner_modules(project):
        if adjacent == wall.label or adjacent not in walls:
            found.append(
                Finding(
                    code="corner_wall",
                    level="error",
                    subject=m.label,
                    message=corner_wall_message(m.label, adjacent, wall.label),
                )
            )
    offsets = corner_offsets(
        project, ws
    )  # одно правило величины и стороны смещения — и для порядка модулей в рядах
    for wall in project.walls:
        for row, modules in zip(wall.rows, row_layouts(wall, offsets), strict=True):
            if (wall.label, row.kind) in offsets.unknown:
                # габариты углового не строятся (дробные мм) — причину называет module_build,
                # сумму соседнего ряда без смещения не считаем, чтобы не было ложного row_sum
                continue
            mine = offsets.of(wall.label, row.kind)
            total = sum(m.width for m in modules) + mine.start + mine.end
            if abs(total - wall.length) > rules.wall_tolerance:
                found.append(
                    Finding(
                        code="row_sum",
                        level="error",
                        subject=wall.label,
                        message=(
                            f"Стена {wall.label}: {ROW_NOM[row.kind]} ряд занимает {num(total)} мм "
                            f"(модули, пустые места, угловые смещения, пенал), а длина стены {num(wall.length)} мм: "
                            f"разница {num(abs(total - wall.length))} мм (допуск {rules.wall_tolerance} мм)."
                        ),
                        value=total,
                        limit=wall.length,
                    )
                )
    return found


def _tall_overlaps(project: Project, ws: Workshop) -> list[Finding]:
    """Пенал занимает оба ряда (тикет 23, решение мастера 2026-11-15): на своём месте (отступ в своём ряду) он
    не должен стоять на угловом смещении другого ряда той же стены и пересекать модуль другого ряда, который
    «перешагнул» его место. Одна находка на пенал: первым — смещение, затем модули по порядку рядов."""
    offsets = corner_offsets(project, ws)
    found: list[Finding] = []
    for wall in project.walls:
        placed = row_positions(wall, offsets)
        for row, entries in zip(wall.rows, placed, strict=True):
            for a, tall in entries:
                if not any(tall is e for e in row.modules) or not is_tall(tall):
                    continue
                b = a + tall.width
                hit: tuple[str, str] | None = None
                for kind in ("lower", "upper"):
                    if kind == row.kind or (wall.label, kind) in offsets.unknown:
                        continue
                    o = offsets.of(wall.label, kind)
                    if o.at_start and a < o.start:
                        hit = (o.at_start[0], kind)
                    elif o.at_end and b > wall.length - o.end:
                        hit = (o.at_end[0], kind)
                    if hit:
                        break
                for other, others in zip(wall.rows, placed, strict=True):
                    if hit or other is row:
                        continue
                    for x, entry in others:
                        if entry is tall:
                            break
                        own = any(entry is e for e in other.modules)
                        if own and x < b and a < x + entry.width:
                            hit = (entry.label, other.kind)
                            break
                if hit:
                    found.append(
                        Finding(
                            code="tall_overlap",
                            level="error",
                            subject=tall.label,
                            message=(
                                f"Пенал {tall.label} (x {num(a)}–{num(b)} на стене {wall.label}) стоит на месте "
                                f"углового смещения или пересекает модуль {hit[0]} {ROW_GEN[hit[1]]} ряда."
                            ),
                        )
                    )
    return found


def _gap_widths(project: Project) -> list[Finding]:
    """Ширина пустого места — целые миллиметры, как у модулей (общее правило «Целые миллиметры»)."""
    found: list[Finding] = []
    for _, entry in _row_entries(project):
        if not is_gap(entry):
            continue
        try:
            gap_width(entry)
        except KitchenError as e:
            found.append(
                Finding(code="gap_width", level="error", subject=entry.label, message="\n".join(e.problems))
            )
    return found


def _gap_upper_countertop(project: Project) -> list[Finding]:
    """Отметка «под столешницей» у пустого места верхнего ряда ничего не делает: столешница идёт над нижним рядом."""
    return [
        Finding(
            code="gap_upper_countertop",
            level="warning",
            subject=entry.label,
            message=(
                f"Место {entry.label} в верхнем ряду: отметка «под столешницей» не действует — "
                "столешница идёт только над нижним рядом."
            ),
        )
        for row, entry in _row_entries(project)
        if row.kind == "upper" and is_gap(entry) and entry.under_countertop is True
    ]


def _drawer_depth(module: ModuleSpec, dims: ModuleDims, ws: Workshop) -> Finding | None:
    """Глубина корпуса под ящик: нужная глубина считается `parts.drawer_depth_shortfall`, той же функцией,
    что и расчёт деталей. Сбой формул ящика здесь не назван: его назовёт расчёт модуля."""
    if not TEMPLATES[module.template].drawers:
        return None
    try:
        shortfall = drawer_depth_shortfall(module, dims, ws)
    except KitchenError:
        return None
    if shortfall is None:
        return None
    need, message = shortfall
    return Finding(
        code="drawer_depth",
        level="error",
        subject=module.label,
        message=message,
        value=dims.depth,
        limit=need,
    )


def _pair(project: Project, brief: list[BriefAppliance], ws: Workshop, found: list[Finding]):
    """Сверка состава. В каждом виде техники сначала сопоставление по модели, затем по порядку.
    Возвращает прибор проекта → прибор брифа (или `None`)."""
    paired: dict[int, BriefAppliance | None] = {}
    leftover_brief: dict[str, list[BriefAppliance]] = {}
    for type_ in dict.fromkeys([b.type for b in brief] + [a.type for a in project.appliances]):
        items = [b for b in brief if b.type == type_]
        mine = [a for a in project.appliances if a.type == type_]
        pairs, items, by_order = pair_by_model_then_order(mine, items, lambda i: i.model)
        for a, hit in pairs:
            paired[id(a)] = hit
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


NOT_IN_KITCHEN = "none"  # `appliances.fridge.wall` брифа: холодильник не на кухне


def _fridge_vs_brief(
    appliance: Appliance, label: str, pair: BriefAppliance | None, *, in_gap: bool
) -> list[Finding]:
    """Холодильник проекта против холодильника брифа: «не на кухне» (любое место) и «отдельностоящий, а ниша в
    модуле». Брифа или пары нет — сравнивать не с чем."""
    if appliance.type != "fridge" or pair is None:
        return []
    if pair.wall == NOT_IN_KITCHEN:
        return [
            Finding(
                code="fridge_not_in_kitchen",
                level="warning",
                subject=label,
                message=f"Холодильник в брифе не на кухне, а в проекте он есть ({label}) — проверьте.",
            )
        ]
    if not in_gap and pair.built_in is False:
        return [
            Finding(
                code="fridge_in_module",
                level="warning",
                subject=label,
                message=f"Холодильник в брифе отдельностоящий, а в проекте для него ниша в модуле {label}.",
            )
        ]
    return []


def _gap_appliances(
    apps: list[Appliance], gap: Gap, row_kind: str, paired: dict[int, BriefAppliance | None], ws: Workshop
) -> list[Finding]:
    """Техника в пустом месте: только нижний ряд, только отдельностоящие виды из `construction.yaml`, ширина
    места не меньше суммы ширин всей техники места (бриф, иначе проект) плюс зазор. Нишу пустое место не строит."""
    rules = ws.construction.gap
    if row_kind != "lower":
        return [
            Finding(
                code="gap_upper_appliance",
                level="error",
                subject=gap.label,
                message=f"Место {gap.label} в верхнем ряду: технику ставят только в пустое место нижнего ряда",
            ),
            *(f for a in apps for f in _fridge_vs_brief(a, gap.label, paired[id(a)], in_gap=True)),
        ]
    found: list[Finding] = []
    fits = []
    for appliance in apps:
        if appliance.type not in rules.appliances:
            allowed = ", ".join(gap_name(kind, 0) for kind in rules.appliances)
            named = APPLIANCE_NAMES.get(appliance.type, appliance.type)
            found.append(
                Finding(
                    code="gap_appliance",
                    level="error",
                    subject=gap.label,
                    message=(
                        f"В пустом месте {gap.label} может стоять только отдельностоящая техника ({allowed}), "
                        f"а привязан «{named}»"
                    ),
                )
            )
        else:
            fits.append(appliance)
    if fits:
        briefed = [
            paired[id(a)] is not None and paired[id(a)].width is not None  # type: ignore[union-attr]
            for a in fits
        ]
        widths = [paired[id(a)].width if has else a.width for a, has in zip(fits, briefed)]  # type: ignore[union-attr]
        need = num(sum(widths) + rules.clearance)
        if gap.width < need:
            source = (
                "по брифу" if all(briefed) else ("по проекту" if not any(briefed) else "по брифу и проекту")
            )
            names = " и ".join(gap_name(a.type, 1) for a in fits)
            found.append(
                Finding(
                    code="gap_too_small",
                    level="error",
                    subject=gap.label,
                    message=(
                        f"Место {gap.label}: {num(gap.width)} мм, а {names} "
                        f"{source} нужно {need} мм "
                        f"({' + '.join(str(num(w)) for w in widths)} + зазор {rules.clearance})"
                    ),
                    value=need,
                    limit=num(gap.width),
                )
            )
    for appliance in fits:
        pair = paired[id(appliance)]
        found += _fridge_vs_brief(appliance, gap.label, pair, in_gap=True)
        if appliance.type == "fridge" and pair is not None and pair.built_in and pair.wall != NOT_IN_KITCHEN:
            found.append(
                Finding(
                    code="gap_built_in",
                    level="warning",
                    subject=gap.label,
                    message=f"Холодильник в брифе встраиваемый, а в проекте стоит в пустом месте {gap.label}",
                )
            )
    return found


def _niches(
    project: Project,
    ws: Workshop,
    dims: dict[str, tuple[ModuleSpec, ModuleDims]],
    brief: list[BriefAppliance] | None,
    failed: set[str],
    gaps: dict[str, tuple[Gap, str]],
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
    for label, (gap, row_kind) in gaps.items():
        placed = [] if label in dims else project.appliances_at(label)
        if placed:
            found += _gap_appliances(placed, gap, row_kind, paired, ws)
    for a in project.appliances:
        if a.module in gaps and a.module not in dims:
            continue
        if a.module not in dims:
            if a.module in failed:  # модуль не рассчитался: причина уже названа находкой `module_build`
                continue
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
        found += _fridge_vs_brief(a, a.module, paired[id(a)], in_gap=False)
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
                        f"Техника «{a.type}» шириной {num(a.width)} мм в модуле {a.module}: такого типоразмера нет "
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
                        f"{num(need)} мм, а модуль строит нишу {num(have)} мм."
                    ),
                    value=num(need),
                    limit=num(have),
                )
            )
    return found


def _modules(project: Project):
    for wall in project.walls:
        for row in wall.rows:
            for module in row.cabinets:
                yield row, module


def _row_entries(project: Project):
    """Все записи рядов: модули и пустые места."""
    for wall in project.walls:
        for row in wall.rows:
            for entry in row.modules:
                yield row, entry


def _build(project: Project, ws: Workshop, found: list[Finding]):
    """Габариты и детали каждого модуля; сбой расчёта модуля — находка, а не обрыв. Модули с одной меткой
    считаются все (находка `duplicate_label` — отдельно): `dims` хранит первый, `built` — каждый."""
    dims: dict[str, tuple[ModuleSpec, ModuleDims]] = {}
    built: list[tuple[ModuleSpec, TemplateOut]] = []
    for row, module in _modules(project):
        if module.template not in TEMPLATES:
            found.append(_build_failure(module, unknown_template_error(module)))
            continue
        try:
            d = module_dims(module, row.kind, ws)
        except KitchenError as e:
            found.append(_build_failure(module, e))
            continue
        dims.setdefault(module.label, (module, d))
        depth = _drawer_depth(module, d, ws)
        if depth:
            found.append(depth)
            continue  # расчёт упёрся бы в ту же глубину
        try:
            built.append((module, build_module(module, row, project.materials, ws, project.appliances)))
        except PartTooSmallError as e:
            found += [_too_small_finding(module, part, e.limit) for part in e.parts]
        except KitchenError as e:
            found.append(_build_failure(module, e))
    return dims, built


_LOOKALIKE = str.maketrans("ABCEHKMOPTXYabcehkmoptxy", "АВСЕНКМОРТХУавсенкмортху")  # латиница → кириллица


def _label_key(label: str) -> str:
    """Метка для сравнения: NFKC (полноширинные цифры и буквы), без невидимых символов (категория Cf:
    U+200B, U+FEFF, мягкий перенос) и пробелов по краям, без различия регистра и латинских двойников кириллицы."""
    text = unicodedata.normalize("NFKC", label)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return text.strip().translate(_LOOKALIKE).casefold()


def duplicate_labels(project: Project) -> list[Finding]:
    """Метка модуля уникальна в проекте: пенал, записанный в двух рядах, тоже повтор. Метки, совпадающие после
    нормализации (регистр, пробелы по краям, латинские «H1» и кириллические «Н1»), — повтор. Одна находка на
    метку, в сообщении все записи."""
    groups: dict[str, list[str]] = {}
    for _, module in _row_entries(project):
        groups.setdefault(_label_key(module.label), []).append(module.label)
    return [
        Finding(
            code="duplicate_label",
            level="error",
            subject=labels[0],
            message=f"Метка модуля «{labels[0]}» не уникальна: повторяется как {', '.join(f'«{x}»' for x in labels[1:])}.",
        )
        for labels in groups.values()
        if len(labels) > 1
    ]


def _too_small_finding(module: ModuleSpec, part: Part, limit: float) -> Finding:
    return Finding(
        code="part_too_small",
        level="error",
        subject=module.label,
        message=part_too_small_message(part, limit),
        value=min(part.length, part.width),
        limit=limit,
    )


def _build_failure(module: ModuleSpec, error: KitchenError) -> Finding:
    return Finding(
        code="module_build", level="error", subject=module.label, message="\n".join(error.problems)
    )


def along_texture(sheet, ws_sheet: tuple[float, float]) -> tuple[float, float]:
    """(вдоль текстуры, поперёк) для листа с текстурой «вдоль N»."""
    match = re.fullmatch(r"вдоль\s+(\d+)", sheet.texture.strip()) if isinstance(sheet.texture, str) else None
    if match is None or float(match.group(1)) not in ws_sheet:
        raise WorkshopError(
            f"materials.yaml: у материала «{sheet.id}» текстура «{sheet.texture}» — "
            f"ожидалось «вдоль N», где N — одна из сторон листа {ws_sheet[0]:g}×{ws_sheet[1]:g}."
        )
    along = float(match.group(1))
    return along, ws_sheet[1] if along == ws_sheet[0] else ws_sheet[0]


def part_fit_findings(part: Part, sheet: SheetMaterial, ws: Workshop) -> list[Finding]:
    """Деталь в рабочем поле листа (лист минус обрезка с двух сторон). Текстура материала запрещает поворот
    всех его деталей."""
    trim = 2 * ws.equipment.sheet.perimeter_trim
    if sheet.texture:
        along, across = (v - trim for v in along_texture(sheet, sheet.sheet))
        limits = (("длина (вдоль текстуры)", part.length, along), ("ширина", part.width, across))
    else:
        big, small = (v - trim for v in sorted(sheet.sheet, reverse=True))
        limits = (("длина", part.length, big), ("ширина", part.width, small))
        if part.length <= big and part.width <= small:
            return []
    return [
        Finding(
            code="part_exceeds_sheet",
            level="error",
            subject=part.id,
            message=(
                f"Деталь {part.id}: {name} {num(value)} мм не помещается в рабочее поле листа "
                f"{num(limit)} мм (лист {sheet.sheet[0]:g}×{sheet.sheet[1]:g} минус обрезка "
                f"по {ws.equipment.sheet.perimeter_trim:g} мм с каждой стороны)."
            ),
            value=value,
            limit=limit,
        )
        for name, value, limit in limits
        if value > limit
    ]


def _sheet_fit(built: list[tuple[ModuleSpec, TemplateOut]], ws: Workshop) -> list[Finding]:
    """Все листовые детали проекта в рабочем поле листа."""
    sheets = {m.id: m for m in ws.materials.sheets}
    return [
        f
        for _, out in built
        for part in out.parts
        if part.material in sheets  # фасады и столешница — не листовые детали
        for f in part_fit_findings(part, sheets[part.material], ws)
    ]


def _no_back(built: list[tuple[ModuleSpec, TemplateOut]]) -> list[Finding]:
    """Корпус без задней стенки. Модуль без корпуса (только фасад) не в счёт."""
    return [
        Finding(
            code="no_back_panel",
            level="warning",
            subject=module.label,
            message=f"Модуль {module.label}: без задней стенки — проверьте, что так и задумано.",
        )
        for module, out in built
        if TEMPLATES[module.template].carcass and not any(p.name == BACK_PANEL for p in out.parts)
    ]


def _countertop_overrides(project: Project) -> list[Finding]:
    """Столешница одна на проект (`common.size_countertop`): переопределение у ряда или модуля цену, раскрой и
    закупку не меняло бы, а КП расходилось бы с ценой, поэтому проект с ним отклоняется."""
    found: list[Finding] = []
    for wall in project.walls:
        for row in wall.rows:
            places = [(f"ряд {ROW_NOM[row.kind]} на стене «{wall.label}»", wall.label, row.materials)]
            places += [(f"Модуль {m.label}", m.label, m.materials) for m in row.cabinets]
            for where, subject, overrides in places:
                if overrides is not None and overrides.countertop is not None:
                    found.append(
                        Finding(
                            code="countertop_override",
                            level="error",
                            subject=subject,
                            message=(
                                f"{where[:1].upper() + where[1:]}: столешница задаётся только для всего проекта "
                                "(materials.countertop), переопределение у ряда или модуля не поддерживается."
                            ),
                        )
                    )
    return found


def check_project(project: Project, ws: Workshop, brief: dict | None = None) -> list[Finding]:
    """Все проверки проекта, ошибки и предупреждения вместе. `brief` — последний бриф заказа (или `None`)."""
    found = (
        duplicate_labels(project)
        + _row_sums(project, ws)
        + _tall_overlaps(project, ws)
        + _gap_widths(project)
        + _gap_upper_countertop(project)
        + _countertop_overrides(project)
    )
    dims, built = _build(project, ws, found)
    failed = {f.subject for f in found if f.code == "module_build"}
    gaps = {e.label: (e, row.kind) for row, e in _row_entries(project) if is_gap(e)}
    found += _niches(project, ws, dims, None if brief is None else brief_appliances(brief), failed, gaps)
    found += _sheet_fit(built, ws)
    found += _no_back(built)
    return found
