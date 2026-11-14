"""Пенал с двумя нишами, нижним ящиком и дверью над техникой."""

from kitchen import formulas
from kitchen.errors import OrderError
from kitchen.formulas import FormulaError
from kitchen.models import Workshop
from kitchen.parts import (
    EDGE_SIDES,
    ModuleDims,
    Position,
    Span,
    TemplateOut,
    _calc,
    _edge_id,
    _material,
    _required_thickness,
    build_base_door,
    built_niche,
    make_part,
    niche_item,
    steps_pick,
)
from kitchen.project import Appliance, ModuleSpec, ProjectMaterials


def build_tall_oven(
    module: ModuleSpec,
    dims: ModuleDims,
    materials: ProjectMaterials,
    ws: Workshop,
    project_appliances: list[Appliance],
) -> TemplateOut:
    c = ws.construction
    rules = c.tall_rules
    W, H, D = dims.width, dims.height, dims.depth
    t, gap = c.case_thickness, c.facades.gap
    oven, microwave = niche_item("tall_oven", "oven", ws), niche_item("tall_oven", "microwave", ws)
    for item, id_ in ((oven, rules.oven), (microwave, rules.microwave)):
        if item is None:
            raise OrderError(f"Модуль {module.label}: техника {id_} отсутствует в appliances.yaml.")

    def niche(item):
        matches = [a for a in project_appliances if a.module == module.label and a.type == item.type]
        if len(matches) > 1:
            raise OrderError(f"Модуль {module.label}: указано несколько приборов {item.type}.")
        width, height, depth = built_niche(matches[0] if matches else None, item)
        if min(width, height, depth) <= 0:
            raise OrderError(f"Модуль {module.label}: габариты техники должны быть положительными.")
        return width, height, depth

    oven_w, oven_h, oven_d = niche(oven)
    microwave_w, microwave_h, microwave_d = niche(microwave)
    bottom_z = gap + rules.drawer_facade_height
    oven_top = bottom_z + t + oven_h
    microwave_top = oven_top + t + microwave_h
    upper_z = microwave_top + t
    try:
        shelf_z = formulas.evaluate(rules.top_shelf_height, Z=upper_z, H=H, HALF=(H - upper_z) // 2)
    except FormulaError as e:
        raise OrderError(f"Модуль {module.label}: формула высоты верхней полки: {e}.") from e
    if not isinstance(shelf_z, int):
        raise OrderError(f"Модуль {module.label}: формула высоты верхней полки должна давать одно число.")
    door_h = H - gap - upper_z
    if min(W - 2 * t, D, rules.drawer_facade_height, door_h, shelf_z - upper_z, H - t - shelf_z) <= 0:
        raise OrderError(f"Модуль {module.label}: недостаточные габариты для ниш, ящика и верхней полки.")
    if max(oven_d, microwave_d) > D or max(oven_w, microwave_w) > W - 2 * t:
        raise OrderError(f"Модуль {module.label}: размеры корпуса не вмещают нишу техники.")
    if min(W - 2 * t - 2 * c.shelf.side_gap, D - c.shelf.front_gap) <= 0 or shelf_z + t > H - t:
        raise OrderError(f"Модуль {module.label}: верхняя полка не помещается в корпусе.")
    local = module.model_copy(update={"shelves": 1})
    out = build_base_door(local, dims, materials, ws, no_doors=True, no_shelf=True, upper_carcass=True)
    body = _material(ws.materials.sheets, materials.body, "Материал корпуса", "листовые")
    edge = _edge_id(ws, body.id)
    for pid, name, z in (
        ("дно-ниши", "дно ниши", bottom_z),
        ("перег-1", "перегородка ниши", oven_top),
        ("перег-2", "перегородка ниши", microwave_top),
    ):
        out.parts.append(
            make_part(
                pid=f"{module.label}-{pid}",
                module_label=module.label,
                name=name,
                a=Span(W - 2 * t, "x"),
                b=Span(D, "y"),
                thickness=t,
                material_id=body.id,
                textured=bool(body.texture),
                at=Position(x=t, y=0, z=z),
                edges={c.edging.case.face: edge},
            )
        )
    shelf = c.shelf
    out.parts.append(
        make_part(
            pid=f"{module.label}-полка",
            module_label=module.label,
            name="полка",
            a=Span(W - 2 * t - 2 * shelf.side_gap, "x"),
            b=Span(D - shelf.front_gap, "y"),
            thickness=t,
            material_id=body.id,
            textured=bool(body.texture),
            at=Position(x=t + shelf.side_gap, y=0, z=shelf_z),
            edges={c.edging.shelves.face: edge},
        )
    )
    facade = _material(ws.materials.facades, materials.facades_lower, "Материал фасадов", "фасады")
    facade_t = _required_thickness(facade, "фасадов")
    facade_w = _calc(module, dims, "ширина фасада", c.facades.single_door_width, H=H, W=W)
    for suffix, z, height in (("-1", gap, rules.drawer_facade_height), ("", upper_z, door_h)):
        out.parts.append(
            make_part(
                pid=f"{module.label}-фасад{suffix}",
                module_label=module.label,
                name="фасад",
                a=Span(facade_w, "x"),
                b=Span(height, "z"),
                thickness=facade_t,
                material_id=facade.id,
                textured=False,
                at=Position(x=gap, y=D, z=z),
                area=True,
            )
        )
    inner = _calc(module, dims, "внутренняя ширина ящика", c.drawers.inner_width, W=W, T=t)
    try:
        bottom = formulas.evaluate(c.tandembox.bottom, внутр_ширина=inner)
        back = formulas.evaluate(c.tandembox.back, внутр_ширина=inner)
    except FormulaError as e:
        raise OrderError(f"Модуль {module.label}: размеры ящика: {e}.") from e
    if (
        not isinstance(bottom, tuple)
        or not isinstance(back, tuple)
        or min(*bottom, *back) <= 0
        or bottom[1] > D
    ):
        raise OrderError(f"Модуль {module.label}: неверные размеры ящика.")
    drawer_material = _material(ws.materials.sheets, c.tandembox.material, "Материал ящика", "листовые")
    drawer_t = _required_thickness(drawer_material, "деталей ящика")
    drawer_z = _calc(module, dims, "высота дна ящика", c.drawers.bottom_height, Z=gap, T=t)
    if D - bottom[1] - drawer_t < 0 or drawer_z < t or drawer_z + max(drawer_t, back[1]) > bottom_z:
        raise OrderError(f"Модуль {module.label}: ящик не помещается под дном ниши.")
    for pid, name, sizes, axes, y in (
        ("дно-ящ-1", "дно ящика", bottom, ("x", "y"), D - bottom[1]),
        ("зад-ящ-1", "задняя стенка ящика", back, ("x", "z"), D - bottom[1] - drawer_t),
    ):
        out.parts.append(
            make_part(
                pid=f"{module.label}-{pid}",
                module_label=module.label,
                name=name,
                a=Span(sizes[0], axes[0]),
                b=Span(sizes[1], axes[1]),
                thickness=drawer_t,
                material_id=drawer_material.id,
                textured=bool(drawer_material.texture),
                at=Position(x=(W - sizes[0]) // 2, y=y, z=drawer_z),
                edges=dict.fromkeys(EDGE_SIDES, _edge_id(ws, drawer_material.id))
                if c.drawers.edge_parts
                else None,
            )
        )
    hw = c.furniture
    joints = 6 * steps_pick(c.confirmats_by_joint_length, D, "конфирматы_по_длине_соединения")
    hinges = steps_pick(c.hinges_by_facade_height, door_h, "петли_по_высоте_фасада")
    out.hardware[hw.confirmats] += joints
    out.hardware[hw.caps] += joints * c.caps.per_unit
    out.hardware[hw.shelf_pins] += shelf.pins
    out.hardware[hw.legs] += c.legs.up_to_800 if W <= c.legs.threshold else c.legs.over_800
    out.hardware[hw.hinges] += hinges
    out.hardware[hw.plates] += hinges * c.plates.per_unit
    out.hardware[hw.screws] += hinges * c.screws_3_5x16.per_hinge + c.screws_3_5x16.per_drawer
    out.hardware[materials.handles] += 2 * c.handles.per_unit
    out.hardware[c.drawers.hardware] += 1
    return out
