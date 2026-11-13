"""Модели файлов мастерской. Поля английские, ключи YAML (русские) — алиасы."""

import re
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from kitchen.formulas import compile_formula, variables


def _not_bool(v):
    if isinstance(v, bool):
        # ValueError, не TypeError: только он превращается в ошибку валидации pydantic
        raise ValueError("ожидалось число, а не true/false")  # noqa: TRY004
    return v


# Числа без молчаливого превращения true/false в 1/0.
Num = Annotated[float, BeforeValidator(_not_bool)]
Int = Annotated[int, BeforeValidator(_not_bool)]


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _f(alias: str):
    return Field(alias=alias)


# --- equipment.yaml ---------------------------------------------------------


class Saw(Section):
    type: str = _f("тип")
    max_cut_length: Num = _f("макс_длина_реза")
    kerf: Num = _f("пропил")


class SheetTrim(Section):
    perimeter_trim: Num = _f("обрезка_по_периметру")


class EdgingMachine(Section):
    machine: str = _f("станок")
    allowance_per_side: Num = _f("припуск_на_сторону")


class Drilling(Section):
    method: str = _f("способ")
    hinges: str = _f("петли")


class Equipment(Section):
    mock: StrictBool
    saw: Saw = _f("пила")
    sheet: SheetTrim = _f("лист")
    edging: EdgingMachine = _f("кромка")
    drilling: Drilling = _f("присадка")
    case_assembly: str = _f("сборка_корпуса")


# --- construction.yaml ------------------------------------------------------


class LowerModule(Section):
    height: Num = _f("высота")
    depth: Num = _f("глубина")
    scheme: str = _f("схема")
    rail_width: Num = _f("царга_ширина")


class UpperModule(Section):
    height: Num = _f("высота")
    depth: Num = _f("глубина")
    scheme: str = _f("схема")


class TallModule(Section):
    height: Num = _f("высота")
    depth: Num = _f("глубина")


class BackPanel(Section):
    material: str = _f("материал")
    contour_offset: Num = _f("отступ_по_контуру")
    fastening_pitch: Num = _f("крепёж_шаг")


class Shelf(Section):
    side_gap: Num = _f("отступ_от_боковин")
    front_gap: Num = _f("отступ_спереди")
    pins: Int = _f("полкодержателей")
    height: Num = _f("высота")  # ДОБАВЛЕНО (МОК): положение полки от низа корпуса


class Legs(Section):
    height: Num = _f("высота")
    up_to_800: Int = _f("до_800")
    over_800: Int = _f("свыше_800")
    threshold: Num = _f("порог_ширины")  # ДОБАВЛЕНО (МОК): порог «до 800» — значением, а не именем ключа


class MeterRounding(BaseModel):
    """Округление длины в метрах («вверх до 0.1 м» — закупка кромки)."""

    model_config = ConfigDict(frozen=True)
    direction: Literal["up", "down"]
    step_m: float

    def apply_mm(self, mm: Decimal) -> Decimal:
        step = Decimal(str(self.step_m)) * 1000
        mode = ROUND_CEILING if self.direction == "up" else ROUND_FLOOR
        return (mm / step).to_integral_value(rounding=mode) * step


_METER_RE = re.compile(r"^\s*(вверх|вниз)\s+до\s+([0-9]+(?:\.[0-9]+)?)\s*м\s*$", re.IGNORECASE)


def parse_meter_rounding(value: object) -> MeterRounding:
    """«вверх до 0.1 м» -> MeterRounding. Неизвестная форма — ошибка загрузки по-русски."""
    if isinstance(value, MeterRounding):
        return value
    m = _METER_RE.match(value) if isinstance(value, str) else None
    if not m or float(m.group(2)) <= 0:
        raise ValueError(
            "неизвестная форма округления, ожидалось «вверх до N м» или «вниз до N м» (N больше нуля)"
        )
    return MeterRounding(direction=_DIRECTIONS[m.group(1).lower()], step_m=float(m.group(2)))


# Формула construction.yaml: строка, разобранная при загрузке (синтаксис и переменные).
# Поле — str: pydantic не строит схему для подклассов str, а compile_formula
# возвращает экземпляр Formula (подкласс str), проверив синтаксис заранее.
FormulaField = Annotated[str, BeforeValidator(compile_formula)]


def _check_formula_vars(spec: list[tuple[str, str]], allowed: set[str]) -> None:
    """Переменные формул должны быть из известного набора, иначе ошибка загрузки."""
    problems = []
    for key, text in spec:
        unknown = sorted(variables(text) - allowed)
        if unknown:
            listed = ", ".join(f"«{v}»" for v in unknown)
            problems.append(
                f"формула «{text}» (ключ «{key}») использует переменную {listed}, "
                f"а допускаются: {', '.join(sorted(allowed))}"
            )
    if problems:
        raise ValueError("; ".join(problems) + ".")


class ThreeDrawers(Section):
    top: Num = _f("верхний")
    others: str = _f("остальные")


class Facades(Section):
    gap: Num = _f("зазор")
    height_lower: FormulaField = _f("высота_нижнего")
    single_door_width: FormulaField = _f("одна_дверь_ширина")
    two_doors_if_wider_than: Num = _f("две_двери_если_ширина_больше")
    two_doors_width: FormulaField = _f("две_двери_ширина")
    three_drawers: ThreeDrawers = _f("ящики_3_шт")

    @model_validator(mode="after")
    def _formula_vars(self) -> "Facades":
        _check_formula_vars(
            [
                ("высота_нижнего", self.height_lower),
                ("одна_дверь_ширина", self.single_door_width),
                ("две_двери_ширина", self.two_doors_width),
            ],
            {"H", "W"},
        )
        return self


class HardwareRefs(Section):
    """Какие позиции hardware.yaml идут в модуль (МОК)."""

    hinges: str = _f("петли")
    plates: str = _f("ответные_планки")
    confirmats: str = _f("конфирматы")
    caps: str = _f("заглушки")
    shelf_pins: str = _f("полкодержатели")
    legs: str = _f("опоры")
    screws: str = _f("саморезы")


class Screws(Section):
    per_hinge: Int = _f("на_петлю")
    per_drawer: Int = _f("на_ящик")


class ConstructionEdging(Section):
    case: str = _f("корпус")
    rails: str = _f("царги")
    shelves: str = _f("полки")
    reserve: Num = _f("запас")
    purchase_rounding: Annotated["MeterRounding", BeforeValidator(parse_meter_rounding)] = _f(
        "округление_закупки"
    )


class Tandembox(Section):
    guide_length: Num = _f("длина_направляющей")
    bottom: FormulaField = _f("дно")
    back: FormulaField = _f("задняя_стенка")
    material: str = _f("материал")

    @model_validator(mode="after")
    def _formula_vars(self) -> "Tandembox":
        _check_formula_vars([("дно", self.bottom), ("задняя_стенка", self.back)], {"внутр_ширина"})
        return self


# Таблица ступеней: [[порог, значение], ...]; берётся первая ступень, где значение <= порога.
Steps = list[tuple[Num, Int]]


class Construction(Section):
    mock: StrictBool
    case_thickness: Num = _f("толщина_корпуса")
    lower: LowerModule = _f("нижний")
    upper: UpperModule = _f("верхний")
    tall: TallModule = _f("пенал")
    back_panel: BackPanel = _f("задняя_стенка")
    shelf: Shelf = _f("полка")
    legs: Legs = _f("ножки")
    facades: Facades = _f("фасады")
    hinges_by_facade_height: Steps = _f("петли_по_высоте_фасада")
    confirmats_by_joint_length: Steps = _f("конфирматы_по_длине_соединения")
    caps: str = _f("заглушки")
    screws_3_5x16: Screws = _f("саморезы_3_5x16")
    plates: str = _f("ответные_планки")  # ДОБАВЛЕНО (МОК): по одной на петлю
    handles: str = _f("ручки")  # ДОБАВЛЕНО (МОК): по одной на фасад
    furniture: HardwareRefs = _f("фурнитура")  # ДОБАВЛЕНО (МОК): id позиций hardware.yaml
    edging: ConstructionEdging = _f("кромка")
    tandembox: Tandembox = _f("ящик_tandembox")

    @model_validator(mode="after")
    def _steps_sorted(self) -> "Construction":
        """Ступенчатая таблица работает, только если пороги идут по возрастанию."""
        for key, steps in (
            ("петли_по_высоте_фасада", self.hinges_by_facade_height),
            ("конфирматы_по_длине_соединения", self.confirmats_by_joint_length),
        ):
            thresholds = [float(t) for t, _ in steps]
            if any(b < a for a, b in pairwise(thresholds)):
                raise ValueError(f"пороги таблицы «{key}» должны идти по возрастанию")
        return self


# --- materials.yaml ---------------------------------------------------------


class SheetMaterial(Section):
    id: str
    name: str = _f("название")
    sheet: tuple[Num, Num] = _f("лист")
    texture: bool | str = _f("текстура")
    thickness: Num | None = Field(
        default=None, alias="толщина"
    )  # ДОБАВЛЕНО (МОК): у ХДФ — 3 (у ЛДСП корпуса толщина из construction.yaml)
    price: Num = _f("цена")
    shop: str = _f("магазин")

    @field_validator("texture", mode="before")
    @classmethod
    def _texture(cls, v):
        if v is False or isinstance(v, str):
            return v
        raise ValueError("ожидалось false или строка вроде «вдоль 2800»")


class EdgeMaterial(Section):
    id: str
    name: str = _f("название")
    price_per_m: Num = _f("цена_за_м")
    sale: str = _f("продажа")
    shop: str = _f("магазин")


class Countertop(Section):
    id: str
    name: str = _f("название")
    sheet: tuple[Num, Num] = _f("лист")
    price: Num = _f("цена")
    shop: str = _f("магазин")


class FacadeMaterial(Section):
    id: str
    name: str = _f("название")
    thickness: Num | None = Field(default=None, alias="толщина")  # ДОБАВЛЕНО (МОК): 19 у МДФ
    price_per_m2: Num = _f("цена_за_м2")
    outsourced: bool = _f("заказ_на_стороне")
    lead_days: Int = _f("срок_раб_дней")
    shop: str = _f("магазин")


class Materials(Section):
    mock: StrictBool
    sheets: list[SheetMaterial] = _f("листовые")
    edges: list[EdgeMaterial] = _f("кромка")
    countertops: list[Countertop] = _f("столешницы")
    facades: list[FacadeMaterial] = _f("фасады")
    # ДОБАВЛЕНО (МОК): какая кромка клеится на какой материал корпуса
    edge_by_material: dict[str, str] = Field(default_factory=dict, alias="кромка_к_материалу")


# --- hardware.yaml ----------------------------------------------------------


class HardwareItem(Section):
    id: str
    name: str = _f("название")
    pack: Int = _f("упаковка")
    price: Num = _f("цена")
    shop: str = _f("магазин")


class Hardware(Section):
    mock: StrictBool
    items: list[HardwareItem] = _f("позиции")


# --- labor.yaml -------------------------------------------------------------


class LaborNorms(Section):
    cut_per_part: Num = _f("распил_на_деталь")
    edge_per_m: Num = _f("кромка_на_метр")
    drill_per_part: Num = _f("присадка_на_деталь")
    case_assembly: Num = _f("сборка_корпуса")
    drawer_assembly_extra: Num = _f("сборка_доп_на_ящик")
    install_per_module: Num = _f("монтаж_на_модуль")
    sink_cutout: Num = _f("врезка_мойки")
    hob_cutout: Num = _f("врезка_варочной")
    site_visit: Num = _f("выезд")
    measuring: Num = _f("замер")
    design: Num = _f("проектирование")


class Labor(Section):
    mock: StrictBool
    hourly_rate: Num = _f("ставка_в_час")
    norms_min: LaborNorms = _f("нормы_минут")


# --- pricing.yaml -----------------------------------------------------------


class RoundingRule(BaseModel):
    """Правило округления цены: направление и шаг в рублях («вверх до 1000»)."""

    model_config = ConfigDict(frozen=True)
    direction: Literal["up", "down"]
    step: int

    def describe(self) -> str:
        return f"{'вверх' if self.direction == 'up' else 'вниз'} до {self.step}"

    def apply(self, value: Decimal) -> int:
        mode = ROUND_CEILING if self.direction == "up" else ROUND_FLOOR
        return int((value / self.step).to_integral_value(rounding=mode)) * self.step


_ROUNDING_RE = re.compile(r"^\s*(вверх|вниз)\s+до\s+([0-9]+)\s*$", re.IGNORECASE)
_DIRECTIONS = {"вверх": "up", "вниз": "down"}


def parse_rounding(value: object) -> RoundingRule:
    """«вверх до 1000» -> RoundingRule. Неизвестная форма — ошибка загрузки по-русски."""
    if isinstance(value, RoundingRule):
        return value
    m = _ROUNDING_RE.match(value) if isinstance(value, str) else None
    if not m or int(m.group(2)) <= 0:
        raise ValueError(
            "неизвестная форма округления, ожидалось «вверх до N» или «вниз до N» (N больше нуля)"
        )
    return RoundingRule(direction=_DIRECTIONS[m.group(1).lower()], step=int(m.group(2)))


class Pricing(Section):
    mock: StrictBool
    materials_markup: Num = _f("наценка_на_материалы")
    consumables_share: Num = _f("расходники_от_материалов")
    materials_delivery: Num = _f("доставка_материалов")
    price_rounding: Annotated[RoundingRule, BeforeValidator(parse_rounding)] = _f("округление_цены")
    prepayment: Num = _f("предоплата")
    quick_estimate_per_m: tuple[Num, Num] = _f("быстрая_оценка_за_пог_м")


class Workshop(BaseModel):
    """Мастерская целиком. Заполняется загрузчиком, не из YAML напрямую."""

    equipment: Equipment
    construction: Construction
    materials: Materials
    hardware: Hardware
    labor: Labor
    pricing: Pricing
    mock_files: list[str]

    @property
    def is_mock(self) -> bool:
        return bool(self.mock_files)
