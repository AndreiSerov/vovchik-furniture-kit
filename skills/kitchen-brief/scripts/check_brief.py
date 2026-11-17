#!/usr/bin/env python3
"""Проверка брифа кухни по правилам мастера.

Запуск:
    python3 check_brief.py brief.json [--settings settings.json] [--today ГГГГ-ММ-ДД]

На входе — JSON брифа по схеме references/brief_schema.json.
На выходе (stdout) — JSON:
    summary           — погонаж и счётчики
    missing_required  — пустые обязательные поля (covered_by: правило, которое уже задаёт этот вопрос)
    conflicts         — противоречия и риски: designer_note — заметка мастеру,
                        client_question для клиента, ask = now | measure
    unknown_fields    — поля, которых нет в схеме (обычно опечатка)
    errors            — то, что не удалось разобрать

Как добавить своё правило: напишите функцию с декоратором @rule("id"),
которая принимает (brief, settings) и возвращает список Finding,
и опишите её в references/rules.md. Только стандартная библиотека.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE.parent / "references" / "brief_schema.json"
SETTINGS_PATH = HERE.parent / "references" / "settings.json"

WRAPPER_KEYS = {"value", "source", "note"}
TYPICALLY_BUILT_IN = {"hob", "oven", "dishwasher", "microwave"}
FREESTANDING_TYPES = {"sink", "cooker"}
BUILT_IN_HOODS = {"built_in", "telescopic", "worktop"}
APPLIANCE_NAMES = {
    "sink": "мойка",
    "cooker": "отдельностоящая плита",
    "hob": "варочная панель",
    "oven": "духовой шкаф",
    "microwave": "микроволновка",
    "dishwasher": "посудомойка",
    "washing_machine": "стиральная машина",
    "coffee_machine": "кофемашина",
    "wine_cooler": "винный шкаф",
}


@dataclass
class Finding:
    rule: str
    severity: str  # conflict | risk | question
    designer_note: str
    client_question: str
    ask: str  # now | measure
    paths: list[str] = field(default_factory=list)


# ---------- доступ к брифу ----------


def unwrap(node: Any) -> Any:
    if isinstance(node, dict) and "value" in node and set(node) <= WRAPPER_KEYS:
        return node["value"]
    return node


def get(brief: dict, path: str) -> Any:
    section, _, name = path.partition(".")
    node = brief.get(section)
    if not isinstance(node, dict):
        return None
    return unwrap(node.get(name))


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def sub(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else None


def text(value: Any) -> str:
    return str(value or "").lower()


def mentions(value: Any, *stems: str) -> bool:
    t = text(value)
    return any(s in t for s in stems)


def fmt_rub(n: float) -> str:
    return f"{round(n):,}".replace(",", " ") + " ₽"


def appliances(brief: dict) -> list[dict]:
    items = get(brief, "appliances.items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def appliance_name(item: dict) -> str:
    name = APPLIANCE_NAMES.get(item.get("type", ""), item.get("type") or "техника")
    if item.get("width_mm"):
        name += f" {round(item['width_mm'] / 10)} см"
    return name


CORNERS = {"corner": 1, "u_shape": 2}


def running_meters(brief: dict, base_depth_mm: int = 600) -> float | None:
    """Погонаж низа: стены минус перекрытие в углах плюс длина острова."""
    walls = get(brief, "room.walls")
    if not isinstance(walls, list):
        walls = []
    lengths = [
        w.get("length_mm") or 0
        for w in walls
        if isinstance(w, dict) and w.get("furnished", True) is not False
    ]
    total = sum(lengths)
    if total:
        corners = min(len(lengths) - 1, CORNERS.get(get(brief, "room.shape"), 0))
        total -= max(corners, 0) * base_depth_mm
    total += sub(get(brief, "room.island"), "length_mm") or 0
    return round(total / 1000, 2) if total else None


def budget_threshold(brief: dict, settings: dict, key: str) -> float:
    rm = running_meters(brief, settings["base_depth_mm"]) or settings["reference_length_m"]
    return settings[key] * rm / settings["reference_length_m"]


# ---------- правила ----------

Rule = Callable[[dict, dict], list[Finding]]
RULES: dict[str, Rule] = {}


def rule(rule_id: str) -> Callable[[Rule], Rule]:
    def register(fn: Rule) -> Rule:
        RULES[rule_id] = fn
        return fn

    return register


@rule("window_uppers")
def window_uppers(brief: dict, s: dict) -> list[Finding]:
    window = get(brief, "room.window")
    upper = get(brief, "storage.upper_height")
    if not isinstance(window, dict) or window.get("present") is False or upper == "none":
        return []
    wall = window.get("wall")
    upper_walls = get(brief, "storage.upper_walls") or []
    if wall and upper_walls and wall not in upper_walls:
        return []
    unknown = [
        label
        for key, label in (
            ("wall", "на какой стене окно"),
            ("offset_from_corner_mm", "отступ окна от угла"),
            ("width_mm", "ширина окна"),
        )
        if is_empty(window.get(key))
    ]
    where = f"на стене «{wall}»" if wall else "на неизвестной стене"
    note = f"Окно {where}, верхние шкафы планируются, но неясно, где они проходят относительно окна."
    if unknown:
        note += " Не хватает: " + ", ".join(unknown) + "."
    note += " Обсудить: где вешать верх, если мойка у окна; что вместо верха над окном."
    return [
        Finding(
            "window_uppers",
            "conflict",
            note,
            "Сколько сантиметров от угла до окна и какая ширина окна? "
            "Рядом с окном верхние шкафы не повесить — хотим понять, сколько места остаётся под верх.",
            "now",
            ["room.window", "storage.upper_walls"],
        )
    ]


@rule("sink_window")
def sink_window(brief: dict, s: dict) -> list[Finding]:
    window = get(brief, "room.window")
    sinks = [i for i in appliances(brief) if i.get("type") == "sink"]
    if not any(mentions(i.get("location"), "окн") for i in sinks):
        return []
    sill = sub(window, "sill_height_mm")
    top = s["countertop_height_mm"]
    out = []
    if is_empty(sill):
        out.append(
            Finding(
                "sink_window",
                "question",
                f"Мойка у окна, высота подоконника неизвестна. Столешница ~{top} мм: "
                "если подоконник ниже, столешница упрётся в окно; проверить и открывание створки над смесителем.",
                "Какая высота подоконника от пола? Можно рулеткой, примерно. "
                "Мойку у окна ставят, когда подоконник не ниже столешницы, около 90 см.",
                "now",
                ["room.window"],
            )
        )
    elif sill < top:
        out.append(
            Finding(
                "sink_window",
                "conflict",
                f"Подоконник {sill} мм ниже столешницы {top} мм — мойку у окна без переделки окна "
                "или понижения столешницы не поставить.",
                "Подоконник ниже обычной высоты столешницы. Готовы ли рассмотреть мойку "
                "в другом месте или столешницу на уровне подоконника?",
                "now",
                ["room.window", "appliances.items"],
            )
        )
    if sub(window, "radiator_below"):
        out.append(
            Finding(
                "sink_window",
                "risk",
                "Под окном батарея, а мойка у окна: тумба под мойку встанет на место батареи. "
                "Обсудить перенос батареи или решётку в столешнице для конвекции.",
                "Батарею под окном планируете оставить или перенести? От этого зависит, "
                "можно ли поставить мойку прямо у окна.",
                "now",
                ["room.window"],
            )
        )
    return out


@rule("tall_uppers_budget")
def tall_uppers_budget(brief: dict, s: dict) -> list[Finding]:
    amount = get(brief, "budget.amount_rub")
    if get(brief, "storage.upper_height") != "to_ceiling" or is_empty(amount):
        return []
    threshold = budget_threshold(brief, s, "min_budget_to_ceiling_per_reference_length")
    if amount >= threshold:
        return []
    rm = running_meters(brief, s["base_depth_mm"])
    length = f"{rm} п. м" if rm else f"{s['reference_length_m']} п. м (длина неизвестна)"
    return [
        Finding(
            "tall_uppers_budget",
            "risk",
            f"Верх до потолка на {length} обычно от {fmt_rub(threshold)}, бюджет клиента "
            f"{fmt_rub(amount)} — вероятно превышение. Варианты для обсуждения: верх 900 мм, "
            "антресоли только на части стены, верх до потолка в одном модуле.",
            f"Шкафы до потолка заметно дороже стандартных. Если смета выйдет больше "
            f"{fmt_rub(amount)}, что для вас важнее — высота шкафов или уложиться в бюджет?",
            "now",
            ["storage.upper_height", "budget.amount_rub", "budget.flexibility"],
        )
    ]


@rule("budget_tight")
def budget_tight(brief: dict, s: dict) -> list[Finding]:
    amount = get(brief, "budget.amount_rub")
    if is_empty(amount):
        return []
    to_ceiling = get(brief, "storage.upper_height") == "to_ceiling"
    key = (
        "min_budget_to_ceiling_per_reference_length"
        if to_ceiling
        else "min_budget_standard_per_reference_length"
    )
    threshold = budget_threshold(brief, s, key)
    reserve = threshold * (1 + s["budget_reserve_pct"] / 100)
    if amount < threshold:
        if to_ceiling:
            return []  # уже сказано в tall_uppers_budget
        note = (
            f"Бюджет {fmt_rub(amount)} ниже моего обычного минимума {fmt_rub(threshold)} "
            "для такого погонажа. Обсудить упрощения: материалы фасадов, фурнитура, без верха на части стены."
        )
    elif amount < reserve and get(brief, "budget.flexibility") != "flexible":
        note = (
            f"Бюджет {fmt_rub(amount)} впритык к ориентиру {fmt_rub(threshold)}: запаса "
            f"{s['budget_reserve_pct']}% на доработки после замера нет."
        )
    else:
        return []
    if get(brief, "budget.includes_appliances"):
        note += " Бюджет включает технику — на мебель останется ещё меньше."
    return [
        Finding(
            "budget_tight",
            "risk",
            note,
            "Бюджет — это жёсткий потолок или можно немного выйти за него, если будет понятно, за что?",
            "now",
            ["budget.amount_rub", "budget.flexibility"],
        )
    ]


@rule("builtin_without_model")
def builtin_without_model(brief: dict, s: dict) -> list[Finding]:
    no_model, unclear = [], []
    model_paths = set()
    for item in appliances(brief):
        if item.get("type") in FREESTANDING_TYPES:
            continue
        if item.get("built_in") is True and is_empty(item.get("model")):
            no_model.append(appliance_name(item))
            model_paths.add("appliances.items")
        elif item.get("built_in") is None and item.get("type") in TYPICALLY_BUILT_IN:
            unclear.append(appliance_name(item))
    fridge = get(brief, "appliances.fridge")
    if sub(fridge, "built_in") is True and is_empty(sub(fridge, "model")):
        no_model.append("холодильник")
        model_paths.add("appliances.fridge")
    hood = get(brief, "appliances.hood")
    if sub(hood, "kind") in BUILT_IN_HOODS and is_empty(sub(hood, "model")):
        no_model.append("вытяжка")
        model_paths.add("appliances.hood")
    out = []
    if no_model:
        out.append(
            Finding(
                "builtin_without_model",
                "conflict",
                "Встраиваемая техника без модели: " + ", ".join(no_model) + ". Без моделей и "
                "монтажных размеров проект нельзя отдавать в производство.",
                "Пришлите, пожалуйста, модели или ссылки: " + ", ".join(no_model) + ". "
                "Если ещё не выбрали — подскажу, на какие размеры ориентироваться.",
                "now",
                sorted(model_paths),
            )
        )
    if unclear:
        out.append(
            Finding(
                "builtin_without_model",
                "question",
                "Неясно, встраиваемая ли техника: " + ", ".join(unclear) + ". Если да — понадобятся модели.",
                "Подскажите: " + ", ".join(unclear) + " — встраиваемые? Если уже выбрали, "
                "пришлите модели или ссылки.",
                "now",
                ["appliances.items"],
            )
        )
    return out


@rule("renovation_stage")
def renovation_stage(brief: dict, s: dict) -> list[Finding]:
    stage = get(brief, "room.renovation_stage")
    if is_empty(stage):
        return [
            Finding(
                "renovation_stage",
                "question",
                "Стадия ремонта неизвестна: от неё зависит, когда делать финальный замер "
                "(после выравнивания стен и стяжки) и реален ли срок.",
                "На какой стадии ремонт: стены уже выровнены, плитка и пол готовы?",
                "now",
                ["room.renovation_stage"],
            )
        ]
    if stage == "rough":
        return [
            Finding(
                "renovation_stage",
                "risk",
                "Черновой ремонт: сейчас возможен только предварительный замер. "
                "Финальный — после выравнивания стен, стяжки и выводов коммуникаций.",
                "Когда примерно стены будут выровнены и будет готов пол? "
                "Финальный замер делаю после этого, иначе размеры уйдут.",
                "now",
                ["room.renovation_stage", "timeline.desired_date"],
            )
        ]
    return []


@rule("fridge_door")
def fridge_door(brief: dict, s: dict) -> list[Finding]:
    fridge = get(brief, "appliances.fridge")
    if not mentions(sub(fridge, "location"), "вход", "двер"):
        return []
    door = get(brief, "room.door")
    if not is_empty(sub(door, "opening_side")) and not is_empty(sub(door, "offset_from_corner_mm")):
        note = (
            "Холодильник у входа: на замере проверить, что дверь кухни и дверь холодильника не сталкиваются."
        )
    else:
        note = (
            "Холодильник у входа, а открывание двери кухни и её положение неизвестны — "
            "возможен конфликт двери кухни с холодильником и с проходом."
        )
    return [
        Finding(
            "fridge_door",
            "conflict",
            note,
            "Дверь на кухню открывается внутрь или наружу, и с какой стороны петли? "
            "Это нужно, чтобы дверь холодильника и дверь кухни не мешали друг другу.",
            "measure",
            ["room.door", "appliances.fridge"],
        )
    ]


@rule("fridge_place")
def fridge_place(brief: dict, s: dict) -> list[Finding]:
    fridge = get(brief, "appliances.fridge")
    if is_empty(fridge) or not isinstance(fridge, dict) or sub(fridge, "wall") == "none":
        return []
    names = {w.get("name") for w in get(brief, "room.walls") or [] if isinstance(w, dict)}
    wall = sub(fridge, "wall")
    known_wall = not is_empty(wall) and (not names or wall in names)
    if known_wall and not is_empty(sub(fridge, "side")):
        return []
    return [
        Finding(
            "fridge_place",
            "question",
            "Без места холодильника не собрать проект: он занимает длину стены.",
            "У какой стены и с какого края будет стоять холодильник, если стоять лицом к стене? "
            "Какой он ширины и высоты?",
            "now",
            ["appliances.fridge"],
        )
    ]


@rule("over_fridge")
def over_fridge(brief: dict, s: dict) -> list[Finding]:
    fridge = get(brief, "appliances.fridge")
    if get(brief, "storage.upper_height") != "to_ceiling" or not is_empty(get(brief, "storage.over_fridge")):
        return []
    if sub(fridge, "built_in") is True or sub(fridge, "wall") == "none":
        return []
    return [
        Finding(
            "over_fridge",
            "question",
            "Верх до потолка и отдельностоящий холодильник: решите про антресоль.",
            "Над холодильником можно сделать шкаф до потолка — нужен?",
            "now",
            ["storage.over_fridge"],
        )
    ]


@rule("hood_exhaust")
def hood_exhaust(brief: dict, s: dict) -> list[Finding]:
    hood = get(brief, "appliances.hood")
    if not isinstance(hood, dict) or hood.get("kind") in (None, "none"):
        return []
    exhaust = hood.get("exhaust")
    if is_empty(exhaust):
        return [
            Finding(
                "hood_exhaust",
                "question",
                "Вытяжка есть, тип отвода не указан: отвод в вентканал требует короба и места "
                "в верхнем шкафу, рециркуляция — угольного фильтра.",
                "Вытяжку подключать к вентканалу (воздух уходит в шахту дома) или ставить "
                "рециркуляцию с угольным фильтром? Если не знаете — расскажу плюсы и минусы.",
                "now",
                ["appliances.hood"],
            )
        ]
    if exhaust == "duct" and is_empty(get(brief, "room.vent_duct")):
        return [
            Finding(
                "hood_exhaust",
                "question",
                "Отвод в вентканал, но положение вентканала неизвестно — длина и трасса короба не ясны.",
                "Где на кухне вентканал (решётка вентиляции)? Пришлите, пожалуйста, фото.",
                "now",
                ["room.vent_duct"],
            )
        ]
    return []


@rule("gas")
def gas(brief: dict, s: dict) -> list[Finding]:
    gas_info = get(brief, "room.gas")
    cooking = [i for i in appliances(brief) if i.get("type") in ("hob", "cooker")]
    fuel = next((i.get("fuel") for i in cooking if i.get("fuel")), None)
    gas_present = sub(gas_info, "present")
    if gas_present is not True:
        if fuel == "gas":
            return [
                Finding(
                    "gas",
                    "conflict",
                    "Указана газовая панель или плита, но газ в помещении не подтверждён.",
                    "На кухне есть газ? Газовую панель можно подключить только к газовой трубе.",
                    "now",
                    ["room.gas", "appliances.items"],
                )
            ]
        if gas_present is None and cooking and fuel is None:
            return [
                Finding(
                    "gas",
                    "question",
                    "Неизвестно, есть ли газ, и не выбран тип варочной панели.",
                    "На кухне есть газ? И варочную панель хотите газовую или индукционную?",
                    "now",
                    ["room.gas"],
                )
            ]
        return []
    gap_gas, gap_el = s["hood_min_height_over_gas_mm"], s["hood_min_height_over_electric_mm"]
    pipe = "" if sub(gas_info, "pipe_location") else " Положение трубы и счётчика неизвестно — по фото."
    if fuel == "gas":
        note = (
            f"Газ, газовая панель/плита: вытяжка не ниже {gap_gas} мм над ней, кран и счётчик "
            "в свободном доступе (не зашивать глухо), шкаф над трубой — с вырезами/съёмной задней стенкой."
        )
        q = "Где проходит газовая труба и есть ли на кухне счётчик? Это видно на фото стен."
    elif fuel in ("induction", "electric"):
        note = (
            "Газ в помещении, но выбрана электрическая/индукционная панель: отключение газа "
            "делает газовая служба, проверить мощность электросети под панель. "
            f"Вытяжка не ниже {gap_el} мм."
        )
        q = "Газ на кухне планируете отключать? Это делает газовая служба, лучше заранее."
    else:
        note = (
            "Газ в помещении, тип варочной панели не выбран: газовая (вытяжка не ниже "
            f"{gap_gas} мм, доступ к крану) или индукция (отключение газа, мощность сети)."
        )
        q = "Варочную панель хотите газовую или индукционную? Если не решили — расскажу, чем они отличаются."
    return [Finding("gas", "question", note + pipe, q, "now", ["room.gas"])]


@rule("budget_unclear")
def budget_unclear(brief: dict, s: dict) -> list[Finding]:
    amount = get(brief, "budget.amount_rub")
    assembly = get(brief, "budget.includes_assembly")
    tech = get(brief, "budget.includes_appliances")
    gaps, asks, paths = [], [], []
    if is_empty(amount):
        gaps.append("сумма не названа")
        paths.append("budget.amount_rub")
    if assembly is not True:
        gaps.append("без сборки" if assembly is False else "неясно, входит ли сборка")
        asks.append("сборка и монтаж" if assembly is None else "")
        paths.append("budget.includes_assembly")
    if tech is None:
        gaps.append("неясно, входит ли техника")
        asks.append("техника")
        paths.append("budget.includes_appliances")
    if not gaps:
        return []
    asks = [a for a in asks if a]
    if is_empty(amount):
        q = "На какую сумму вы ориентируетесь? Можно вилкой."
        if asks:
            q += " Входит ли в неё " + " и ".join(asks) + "?"
    elif asks:
        q = "Уточните, пожалуйста: в эту сумму входит " + " и ".join(asks) + "?"
    else:
        q = "Сборка и монтаж считаются отдельно — на них есть бюджет сверх названной суммы?"
    return [
        Finding(
            "budget_unclear",
            "question",
            "Бюджет: " + ", ".join(gaps) + ". Без этого нельзя сопоставить смету с ожиданиями.",
            q,
            "now",
            paths,
        )
    ]


@rule("deadline")
def deadline(brief: dict, s: dict) -> list[Finding]:
    raw = get(brief, "timeline.desired_date")
    if is_empty(raw):
        return []
    try:
        target = date.fromisoformat(str(raw))
    except ValueError:
        return []
    today = s["_today"]
    weeks_left = (target - today).days / 7
    need = s["production_weeks"] + s["measure_to_contract_weeks"]
    stage = get(brief, "room.renovation_stage")
    if weeks_left < need:
        note = (
            f"До желаемой даты ~{weeks_left:.0f} нед., а замер → договор → производство → монтаж "
            f"занимает от {need} нед. Срок нереален без ускорения."
        )
    elif stage in (None, "", "rough") and weeks_left < need + 4:
        note = (
            f"До желаемой даты ~{weeks_left:.0f} нед. при минимуме {need} нед., а ремонт "
            f"{'черновой' if stage == 'rough' else 'неизвестной стадии'} — финальный замер может сдвинуть срок."
        )
    else:
        return []
    return [
        Finding(
            "deadline",
            "risk",
            note,
            f"Производство занимает около {s['production_weeks']} недель после финального замера "
            "и согласования проекта. Срок к этой дате жёсткий или есть запас?",
            "now",
            ["timeline.desired_date", "timeline.hardness", "room.renovation_stage"],
        )
    ]


def has_island(brief: dict) -> bool:
    return bool(
        get(brief, "room.has_island") or get(brief, "room.island") or get(brief, "room.shape") == "island"
    )


@rule("island_utilities")
def island_utilities(brief: dict, s: dict) -> list[Finding]:
    if not has_island(brief):
        return []
    on_island = [i for i in appliances(brief) if mentions(i.get("location"), "остров")]
    hood = get(brief, "appliances.hood")
    needs = []
    if any(i.get("type") in ("sink", "dishwasher") for i in on_island):
        needs.append("вода и канализация в полу с уклоном (учесть высоту стяжки)")
    if on_island:
        needs.append("электрика в полу под технику острова")
    if any(i.get("type") in ("hob", "cooker") for i in on_island) and sub(hood, "kind") not in (
        "island",
        "worktop",
    ):
        needs.append("островная или встроенная в столешницу вытяжка")
    if not needs:
        return []
    stage = get(brief, "room.renovation_stage")
    if stage == "rough":
        q = (
            "Когда планируется стяжка пола? Воду, слив и электрику к острову нужно вывести в пол "
            "до неё — хорошо, что вы написали заранее."
        )
    elif stage in ("finish", "done"):
        q = (
            "Пол уже готов — вывести воду, слив и электрику к острову получится только через пол. "
            "Готовы к этим работам, или лучше перенести технику острова к стене?"
        )
    else:
        q = (
            "Стяжку пола уже залили? Чтобы поставить на остров мойку или плиту, воду, слив и "
            "электрику нужно вывести в пол до стяжки."
        )
    return [
        Finding(
            "island_utilities",
            "risk",
            "Остров с техникой, нужны: " + "; ".join(needs) + ".",
            q,
            "now",
            ["room.has_island"],
        )
    ]


@rule("island_fits")
def island_fits(brief: dict, s: dict) -> list[Finding]:
    if not has_island(brief):
        return []
    island = get(brief, "room.island") or {}
    passage, depth = s["island_min_passage_mm"], s["base_depth_mm"]
    length = sub(island, "length_mm")
    walls = [w for w in get(brief, "room.walls") or [] if isinstance(w, dict) and w.get("length_mm")]
    if get(brief, "room.shape") == "u_shape" and length and walls:
        inner = max(w["length_mm"] for w in walls) - 2 * depth
        need = length + 2 * passage
        if inner < need:
            return [
                Finding(
                    "island_fits",
                    "conflict",
                    f"Остров {length} мм внутри «П»: между боковыми рядами ~{inner} мм, "
                    f"а с проходами по {passage} мм нужно {need} мм. Обсудить: остров за линией "
                    "боковых стен (в зоне гостиной), короче остров или боковые ряды, развернуть остров.",
                    "Остров планируете внутри буквы П или дальше, в сторону гостиной? По размерам "
                    "внутри П вокруг острова остаётся мало места для прохода.",
                    "now",
                    ["room.island"],
                )
            ]
    return [
        Finding(
            "island_fits",
            "question",
            f"Остров: на замере проверить проходы вокруг (не меньше {passage} мм) и открывание "
            "ящиков и посудомойки навстречу друг другу.",
            "Сколько места от острова до шкафов вы представляете? Обычно для прохода нужно около метра.",
            "measure",
            ["room.island"],
        )
    ]


# ---------- обязательные поля и схема ----------


def check_required(brief: dict, schema: dict) -> list[dict]:
    out = []
    for sec_key, sec in schema["sections"].items():
        for name, spec in sec["fields"].items():
            if spec.get("required") and is_empty(get(brief, f"{sec_key}.{name}")):
                out.append(
                    {
                        "path": f"{sec_key}.{name}",
                        "label": f"{sec['label']}: {spec['label']}",
                        "ask": spec.get("ask", "now"),
                    }
                )
    return out


def unknown_fields(brief: dict, schema: dict) -> list[str]:
    out = []
    for sec_key, node in brief.items():
        sec = schema["sections"].get(sec_key)
        if sec is None:
            out.append(sec_key)
        elif isinstance(node, dict):
            out += [f"{sec_key}.{n}" for n in node if n not in sec["fields"]]
    return out


def enum_text(value: Any) -> str:
    """Значение для сравнения с перечнем: дробное без дробной части равно целому («720.0» — «720»)."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _enum_values(desc: Any) -> list[str] | None:
    """Перечень из описания ключа «enum: a, b (пояснение), c»; не перечень — `None`."""
    if not str(desc).startswith("enum: "):
        return None
    listed = re.sub(r"\([^)]*\)", "", str(desc)[6:]).split("—")[0]
    return [v.strip() for v in listed.split(",")]


def _keyed(value: Any, spec: dict, path: str) -> list[tuple[str, Any, str]]:
    """Пары (путь, значение, описание) ключей объекта и элементов списка по `item` схемы."""
    item = spec.get("item")
    if not isinstance(item, dict):
        return []
    if spec.get("type") == "object" and isinstance(value, dict):
        return [(f"{path}.{k}", value[k], d) for k, d in item.items() if not is_empty(value.get(k))]
    if spec.get("type") == "list" and isinstance(value, list):
        return [
            (f"{path}[{i}].{k}", el[k], d)
            for i, el in enumerate(value)
            if isinstance(el, dict)
            for k, d in item.items()
            if not is_empty(el.get(k))
        ]
    return []


def enum_errors(brief: dict, schema: dict) -> list[str]:
    """Значение enum-поля не из перечня схемы; у полей-объектов и элементов списков — ключи «enum: a, b»."""
    out = []
    for sec_key, sec in schema["sections"].items():
        for name, spec in sec["fields"].items():
            value = get(brief, f"{sec_key}.{name}")
            if is_empty(value):
                continue
            path = f"{sec_key}.{name}"
            if spec.get("type") == "enum" and enum_text(value) not in spec["values"]:
                out.append(f"{path}: {value!r} не из перечня {', '.join(spec['values'])}")
            for key_path, key_value, desc in _keyed(value, spec, path):
                allowed = _enum_values(desc)
                if allowed is not None and enum_text(key_value) not in allowed:
                    out.append(f"{key_path}: {key_value!r} не из перечня {', '.join(allowed)}")
    return out


# тип схемы -> (проверка значения, название в сообщении)
TYPE_CHECKS: dict[str, tuple[Callable[[Any], bool], str]] = {
    # целое: int или дробное без дробной части (2700.0); bool и строки — нет
    "integer": (
        lambda v: (
            not isinstance(v, bool) and (isinstance(v, int) or (isinstance(v, float) and v.is_integer()))
        ),
        "целое число",
    ),
    "string": (lambda v: isinstance(v, str), "строка"),
    "boolean": (lambda v: isinstance(v, bool), "да/нет"),
    "object": (lambda v: isinstance(v, dict), "объект"),
    "list": (lambda v: isinstance(v, list), "список"),
}


def _type_error(path: str, value: Any, kind: str) -> str | None:
    check_value, label = TYPE_CHECKS.get(kind, (None, ""))
    if check_value is None or check_value(value):
        return None
    return f"{path}: {value!r} — ожидалось {label}"


def type_errors(brief: dict, schema: dict) -> list[str]:
    """Тип значения не тот, что в схеме: поля разделов, ключи объектов, элементы и ключи списков.

    Пустое значение (`None`, `""`, `[]`, `{}`) не проверяется. Перечни — в `enum_errors`.
    """
    out = []
    for sec_key, sec in schema["sections"].items():
        for name, spec in sec["fields"].items():
            value = get(brief, f"{sec_key}.{name}")
            if is_empty(value):
                continue
            path = f"{sec_key}.{name}"
            error = _type_error(path, value, spec.get("type", ""))
            if error:
                out.append(error)
                continue
            if spec.get("type") == "list" and isinstance(spec.get("item"), dict):
                out += [
                    e
                    for i, el in enumerate(value)
                    if not is_empty(el)
                    for e in [_type_error(f"{path}[{i}]", el, "object")]
                    if e
                ]
            for key_path, key_value, desc in _keyed(value, spec, path):
                kind = str(desc).split(":")[0].split()[0]
                error = _type_error(key_path, key_value, kind) if kind != "enum" else None
                if error:
                    out.append(error)
    return out


def check(brief: dict, settings: dict, schema: dict) -> dict:
    conflicts: list[Finding] = []
    errors: list[str] = [*type_errors(brief, schema), *enum_errors(brief, schema)]
    for rule_id, fn in RULES.items():
        if rule_id in settings.get("disabled_rules", []):
            continue
        try:
            conflicts += fn(brief, settings)
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            errors.append(f"{rule_id}: {exc!r}")
    raw_date = get(brief, "timeline.desired_date")
    if not is_empty(raw_date):
        try:
            date.fromisoformat(str(raw_date))
        except ValueError:
            errors.append(f"timeline.desired_date: не ГГГГ-ММ-ДД: {raw_date!r}")
    missing = check_required(brief, schema)
    for m in missing:
        m["covered_by"] = next((c.rule for c in conflicts if m["path"] in c.paths), None)
    try:
        meters = running_meters(brief, settings["base_depth_mm"])
    except (TypeError, ValueError):  # неверный тип длины — об этом уже сказано в errors
        meters = None
    return {
        "summary": {
            "running_meters": meters,
            "missing_required": len(missing),
            "conflicts": len(conflicts),
        },
        "missing_required": missing,
        "conflicts": [asdict(c) for c in conflicts],
        "unknown_fields": unknown_fields(brief, schema),
        "errors": errors,
    }


def load_settings(path: Path, today: date) -> dict:
    settings = json.loads(path.read_text(encoding="utf-8"))
    settings["_today"] = today
    return settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка брифа кухни")
    parser.add_argument("brief", type=Path)
    parser.add_argument("--settings", type=Path, default=SETTINGS_PATH)
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    args = parser.parse_args(argv)
    brief = json.loads(args.brief.read_text(encoding="utf-8"))
    if not isinstance(brief, dict):
        print(f"Бриф должен быть JSON-объектом {{...}}, а не {type(brief).__name__}.", file=sys.stderr)
        return 1
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    result = check(brief, load_settings(args.settings, args.today), schema)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
