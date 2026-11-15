"""Модели файлов мастерской. Поля английские, ключи YAML (русские) — алиасы."""

import math
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
    StringConstraints,
    WithJsonSchema,
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
# Конечные: `.inf` и `.nan` в YAML — ошибка загрузки, а не цена или норма.
Num = Annotated[float, BeforeValidator(_not_bool), Field(allow_inf_nan=False)]
Int = Annotated[int, BeforeValidator(_not_bool)]
# Диапазоны живут в типах: цена, ставка, норма, доля — не меньше нуля; упаковка, шаг, размер листа — больше нуля.
NonNegNum = Annotated[Num, Field(ge=0)]
NonNegInt = Annotated[Int, Field(ge=0)]
PosInt = Annotated[Int, Field(gt=0)]
# В JSON Schema — `exclusiveMinimum`: из-за `BeforeValidator` в `Num` pydantic иначе пишет нестандартное `gt`.
PosNum = Annotated[Num, Field(gt=0), WithJsonSchema({"type": "number", "exclusiveMinimum": 0})]


# Виды техники, которые знает движок: перечень брифа (тест сверяет с его схемой) и вытяжка из `appliances.hood`.
APPLIANCE_KINDS = frozenset(
    {
        "sink",
        "hob",
        "cooker",
        "oven",
        "microwave",
        "dishwasher",
        "washing_machine",
        "coffee_machine",
        "wine_cooler",
        "other",
        "hood",
    }
)


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _f(alias: str):
    return Field(alias=alias)


# --- equipment.yaml ---------------------------------------------------------


class Saw(Section):
    type: str = _f("тип")
    max_cut_length: PosInt = _f("макс_длина_реза")
    kerf: NonNegNum = _f("пропил")  # решение мастера: дробный учитывается вверх до целого мм (`kerf_mm`)

    @property
    def kerf_mm(self) -> int:
        """Пропил, который берёт раскрой: целый, дробный — вверх (иначе детали выйдут меньше номинала)."""
        return math.ceil(self.kerf)


class SheetTrim(Section):
    perimeter_trim: NonNegInt = _f("обрезка_по_периметру")


class EdgingMachine(Section):
    machine: str = _f("станок")
    allowance_per_side: NonNegInt = _f("припуск_на_сторону")


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
    height: PosInt = _f("высота")
    depth: PosInt = _f("глубина")
    scheme: str = _f("схема")
    rail_width: PosInt = _f("царга_ширина")


class UpperModule(Section):
    height: PosInt = _f("высота")
    depth: PosInt = _f("глубина")
    scheme: str = _f("схема")


class TallModule(Section):
    height: PosInt = _f("высота")
    depth: PosInt = _f("глубина")


class BackPanel(Section):
    material: str = _f("материал")
    contour_offset: NonNegInt = _f("отступ_по_контуру")
    fastening_pitch: PosInt = _f("крепёж_шаг")


class Shelf(Section):
    side_gap: NonNegInt = _f("отступ_от_боковин")
    front_gap: NonNegInt = _f("отступ_спереди")
    pins: NonNegInt = _f("полкодержателей")
    height: PosInt = _f("высота")  # ДОБАВЛЕНО (МОК): положение полки от низа корпуса
    step: PosInt = _f("шаг")  # ДОБАВЛЕНО (МОК): расстояние между полками по высоте


class Legs(Section):
    height: PosInt = _f("высота")
    up_to_800: NonNegInt = _f("до_800")
    over_800: NonNegInt = _f("свыше_800")
    threshold: PosNum = _f("порог_ширины")  # ДОБАВЛЕНО (МОК): порог «до 800» — значением, а не именем ключа


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


class RatioRule(BaseModel):
    """Сколько штук на единицу («по одной на конфирмат» — 1, «по 2 на петлю» — 2)."""

    model_config = ConfigDict(frozen=True)
    per_unit: int


_COUNT_WORDS = {"одной": 1, "одному": 1}


def _ratio(unit: str):
    """Разбор «по одной на <unit>» / «по N на <unit>» (N больше нуля) в `RatioRule`."""
    pattern = re.compile(rf"^\s*по\s+(одной|одному|[0-9]+)\s+на\s+{unit}\s*$", re.IGNORECASE)

    def parse(value: object) -> RatioRule:
        if isinstance(value, RatioRule):
            return value
        m = pattern.match(value) if isinstance(value, str) else None
        count = None
        if m:
            word = m.group(1).lower()
            count = _COUNT_WORDS.get(word) or int(word)
        if not count:
            raise ValueError(f"неизвестная формулировка, ожидалось «по одной на {unit}» или «по N на {unit}»")
        return RatioRule(per_unit=count)

    return parse


class EdgeRule(BaseModel):
    """Какая сторона детали кромится: передняя (грань `+y`) или длинная сторона L1 (в комнату)."""

    model_config = ConfigDict(frozen=True)
    side: Literal["front", "long"]

    @property
    def face(self) -> str:
        """Ключ для `make_part`: геометрическая грань `+y` или сторона детали `L1`."""
        return "+y" if self.side == "front" else "L1"


_EDGE_PHRASES = {
    "передняя видимая кромка, пвх 0.8": "front",
    "передняя кромка": "front",
    "одна длинная сторона": "long",
}


def parse_edge_rule(value: object) -> EdgeRule:
    """«передняя кромка», «одна длинная сторона» -> EdgeRule. Иная формулировка — ошибка загрузки."""
    if isinstance(value, EdgeRule):
        return value
    side = _EDGE_PHRASES.get(" ".join(value.lower().split())) if isinstance(value, str) else None
    if side is None:
        accepted = ", ".join(
            f"«{p}»" for p in ("передняя видимая кромка, ПВХ 0.8", "передняя кромка", "одна длинная сторона")
        )
        raise ValueError(f"неизвестная формулировка кромки, допустимы: {accepted}")
    return EdgeRule(side=side)  # type: ignore[arg-type]


class ThreeDrawers(Section):
    top: PosNum = _f("верхний")
    others: str = _f("остальные")

    @field_validator("others")
    @classmethod
    def equal_remaining(cls, value: str) -> str:
        if value != "делят остаток поровну":
            raise ValueError("ожидалось «делят остаток поровну»")
        return value


class Facades(Section):
    gap: NonNegInt = _f("зазор")
    height_lower: FormulaField = _f("высота_нижнего")
    height_upper: FormulaField = _f("высота_верхнего")
    single_door_width: FormulaField = _f("одна_дверь_ширина")
    two_doors_if_wider_than: PosNum = _f("две_двери_если_ширина_больше")
    two_doors_width: FormulaField = _f("две_двери_ширина")
    single_door_max_module_width: PosNum = _f("одна_дверь_макс_ширина_модуля")  # ДОБАВЛЕНО (МОК)
    two_doors_min_module_width: PosNum = _f("две_двери_мин_ширина_модуля")  # ДОБАВЛЕНО (МОК)
    three_drawers: ThreeDrawers = _f("ящики_3_шт")

    @model_validator(mode="after")
    def _formula_vars(self) -> "Facades":
        _check_formula_vars(
            [
                ("высота_нижнего", self.height_lower),
                ("высота_верхнего", self.height_upper),
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


class SinkRules(Section):
    without_shelf: StrictBool = _f("без_полки")
    without_back: StrictBool = _f("без_задней_стенки")


class BottleRules(Section):
    insert: str = _f("вставка")
    max_width: PosInt = _f("макс_ширина")


class DrawerRules(Section):
    hardware: str = _f("комплект")
    edge_parts: StrictBool = _f("кромить_детали")
    inner_width: FormulaField = _f("внутренняя_ширина")
    lower_facade_height: FormulaField = _f("высота_нижнего_фасада")
    bottom_height: FormulaField = _f("высота_дна")

    @model_validator(mode="after")
    def check_formula_vars(self) -> "DrawerRules":
        _check_formula_vars([("внутренняя_ширина", self.inner_width)], {"W", "T"})
        _check_formula_vars([("высота_нижнего_фасада", self.lower_facade_height)], {"H", "TOP", "GAP"})
        _check_formula_vars([("высота_дна", self.bottom_height)], {"Z", "T"})
        return self


class Screws(Section):
    per_hinge: NonNegInt = _f("на_петлю")
    per_drawer: NonNegInt = _f("на_ящик")


class ConstructionEdging(Section):
    case: Annotated[EdgeRule, BeforeValidator(parse_edge_rule)] = _f("корпус")
    rails: Annotated[EdgeRule, BeforeValidator(parse_edge_rule)] = _f("царги")
    shelves: Annotated[EdgeRule, BeforeValidator(parse_edge_rule)] = _f("полки")
    reserve: NonNegNum = _f("запас")
    purchase_rounding: Annotated["MeterRounding", BeforeValidator(parse_meter_rounding)] = _f(
        "округление_закупки"
    )


class Tandembox(Section):
    guide_length: PosNum = _f("длина_направляющей")
    bottom: FormulaField = _f("дно")
    back: FormulaField = _f("задняя_стенка")
    material: str = _f("материал")

    @model_validator(mode="after")
    def _formula_vars(self) -> "Tandembox":
        _check_formula_vars([("дно", self.bottom), ("задняя_стенка", self.back)], {"внутр_ширина"})
        return self


# Таблица ступеней: [[порог, значение], ...]; берётся первая ступень, где значение <= порога.
Steps = list[tuple[NonNegNum, NonNegInt]]


class TallRules(Section):
    oven: str = _f("духовка")
    microwave: str = _f("свч")
    drawer_facade_height: Annotated[Int, Field(gt=0)] = _f("высота_фасада_ящика")
    top_shelf_height: FormulaField = _f("высота_верхней_полки")
    shelf_rounding: Literal["вниз"] = _f("округление_полки")

    @model_validator(mode="after")
    def _formula_vars(self) -> "TallRules":
        _check_formula_vars([("высота_верхней_полки", self.top_shelf_height)], {"Z", "H", "HALF"})
        return self


class UpperRules(Section):
    hanger: str = _f("навес")
    hangers: NonNegInt = _f("навесов")
    lift: str = _f("подъёмник")
    dryer: str = _f("сушка")
    dryer_width: PosInt = _f("ширина_сушки")
    hood: str = _f("вытяжка")
    hood_rail_min: NonNegInt = _f("мин_глубина_планки")
    hood_bottom: Literal["передняя планка, открытый проём сзади"] = _f("дно_вытяжки")


class CornerRules(Section):
    lower_offset: Annotated[Int, Field(ge=0)] = _f("смещение_нижнего")
    upper_offset: Annotated[Int, Field(ge=0)] = _f("смещение_верхнего")
    wall_tolerance: Annotated[Int, Field(ge=0)] = _f("допуск_стены")
    base_facade_width: Annotated[Int, Field(gt=0)] = _f("ширина_фасада_нижнего")
    upper_filler_width: Annotated[Int, Field(gt=0)] = _f("ширина_заглушки_верхнего")
    upper_depth: Annotated[Int, Field(gt=0)] = _f("глубина_верхнего")
    carousel: str = _f("карусель")
    filler_screws: Annotated[Int, Field(ge=0)] = _f("саморезов_заглушки")
    filler_material: str = _f("материал_заглушки")
    filler_edges: StrictBool = _f("кромить_заглушку")


class LinearItem(Section):
    """Погонная позиция из `hardware.yaml`, которая продаётся штуками фиксированной длины."""

    item: str = _f("позиция")
    piece_length: Annotated[Int, Field(gt=0)] = _f("длина_штуки")


class LedRules(Section):
    profile: str = _f("профиль")
    power_supply: str = _f("блок_питания")
    meters_per_supply: Annotated[Num, Field(gt=0)] = _f("метров_на_блок")
    meter_rounding: Annotated[Int, Field(gt=0)] = _f("округление_метров")  # вверх до кратного N метров


class CountertopRules(Section):
    corner_joint: Literal["встык"] = _f("стык_в_углу")


class CommonRules(Section):
    """Общие позиции кухни: не принадлежат одному модулю, считаются по длинам рядов проекта."""

    joint_reserve: Annotated[Num, Field(ge=0)] = _f("запас_на_стыки")
    plinth: LinearItem = _f("цоколь")
    rail: LinearItem = _f("шина")
    led: LedRules = _f("led")
    countertop: CountertopRules = _f("столешница")


class Construction(Section):
    mock: StrictBool
    case_thickness: PosInt = _f("толщина_корпуса")
    lower: LowerModule = _f("нижний")
    upper: UpperModule = _f("верхний")
    tall: TallModule = _f("пенал")
    back_panel: BackPanel = _f("задняя_стенка")
    shelf: Shelf = _f("полка")
    legs: Legs = _f("ножки")
    facades: Facades = _f("фасады")
    hinges_by_facade_height: Steps = _f("петли_по_высоте_фасада")
    confirmats_by_joint_length: Steps = _f("конфирматы_по_длине_соединения")
    caps: Annotated[RatioRule, BeforeValidator(_ratio("конфирмат"))] = _f("заглушки")
    screws_3_5x16: Screws = _f("саморезы_3_5x16")
    # ДОБАВЛЕНО (МОК): ответные планки — по одной на петлю, ручки — по одной на фасад
    plates: Annotated[RatioRule, BeforeValidator(_ratio("петлю"))] = _f("ответные_планки")
    handles: Annotated[RatioRule, BeforeValidator(_ratio("фасад"))] = _f("ручки")
    furniture: HardwareRefs = _f("фурнитура")  # ДОБАВЛЕНО (МОК): id позиций hardware.yaml
    sink: SinkRules = _f("мойка")
    bottle: BottleRules = _f("бутылочница")
    drawers: DrawerRules = _f("ящики")
    tall_rules: TallRules = _f("правила_пенала")
    upper_rules: UpperRules = _f("верхние_модули")
    corner_rules: CornerRules = _f("угловые_модули")
    # ДОБАВЛЕНО (МОК): виды техники, которые лежат на столешнице (ширину корпуса с нишей не сравнивать)
    countertop_appliances: list[str] = _f("техника_в_столешницу")
    common: CommonRules = _f("общие_позиции")  # ДОБАВЛЕНО (МОК): цоколь, шина, LED, столешница
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
    sheet: tuple[PosInt, PosInt] = _f("лист")
    texture: bool | str = _f("текстура")
    thickness: PosInt | None = Field(
        default=None, alias="толщина"
    )  # ДОБАВЛЕНО (МОК): у ХДФ — 3 (у ЛДСП корпуса толщина из construction.yaml)
    price: NonNegNum = _f("цена")
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
    price_per_m: NonNegNum = _f("цена_за_м")
    sale: str = _f("продажа")
    shop: str = _f("магазин")


class Countertop(Section):
    id: str
    name: str = _f("название")
    sheet: tuple[PosInt, PosInt] = _f("лист")
    price: NonNegNum = _f("цена")
    shop: str = _f("магазин")


class FacadeMaterial(Section):
    id: str
    name: str = _f("название")
    thickness: PosInt | None = Field(default=None, alias="толщина")  # ДОБАВЛЕНО (МОК): 19 у МДФ
    price_per_m2: NonNegNum = _f("цена_за_м2")
    outsourced: bool = _f("заказ_на_стороне")
    lead_days: NonNegInt = _f("срок_раб_дней")
    shop: str = _f("магазин")


def _unique_ids(items: list, what: str, key: str) -> None:
    """Повторный `id` в одном списке — ошибка загрузки: какая цена верна, решал бы порядок поиска."""
    seen: set[str] = set()
    repeated = sorted({i.id for i in items if i.id in seen or seen.add(i.id)})
    if repeated:
        listed = ", ".join(f"«{i}»" for i in repeated)
        raise ValueError(f"ключ «{key}»: повторяется id {what}: {listed}")


class Materials(Section):
    mock: StrictBool
    sheets: list[SheetMaterial] = _f("листовые")
    edges: list[EdgeMaterial] = _f("кромка")
    countertops: list[Countertop] = _f("столешницы")
    facades: list[FacadeMaterial] = _f("фасады")

    @model_validator(mode="after")
    def _ids_unique(self) -> "Materials":
        for items, what, key in (
            (self.sheets, "листового материала", "листовые"),
            (self.edges, "кромки", "кромка"),
            (self.countertops, "столешницы", "столешницы"),
            (self.facades, "фасада", "фасады"),
        ):
            _unique_ids(items, what, key)
        return self

    # ДОБАВЛЕНО (МОК): какая кромка клеится на какой материал корпуса
    edge_by_material: dict[str, str] = Field(default_factory=dict, alias="кромка_к_материалу")


# --- hardware.yaml ----------------------------------------------------------


class HardwareItem(Section):
    id: str
    name: str = _f("название")
    pack: PosInt = _f("упаковка")
    price: NonNegNum = _f("цена")
    shop: str = _f("магазин")
    client_name: str | None = Field(default=None, alias="для_клиента", min_length=1)


class Hardware(Section):
    mock: StrictBool
    items: list[HardwareItem] = _f("позиции")

    @model_validator(mode="after")
    def _ids_unique(self) -> "Hardware":
        _unique_ids(self.items, "позиции фурнитуры", "позиции")
        return self


# --- labor.yaml -------------------------------------------------------------


class LaborNorms(Section):
    cut_per_part: NonNegNum = _f("распил_на_деталь")
    edge_per_m: NonNegNum = _f("кромка_на_метр")
    drill_per_part: NonNegNum = _f("присадка_на_деталь")
    case_assembly: NonNegNum = _f("сборка_корпуса")
    drawer_assembly_extra: NonNegNum = _f("сборка_доп_на_ящик")
    install_per_module: NonNegNum = _f("монтаж_на_модуль")
    sink_cutout: NonNegNum = _f("врезка_мойки")
    hob_cutout: NonNegNum = _f("врезка_варочной")
    site_visit: NonNegNum = _f("выезд")
    measuring: NonNegNum = _f("замер")
    design: NonNegNum = _f("проектирование")


class Labor(Section):
    mock: StrictBool
    hourly_rate: NonNegNum = _f("ставка_в_час")
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
    materials_markup: NonNegNum = _f("наценка_на_материалы")
    consumables_share: NonNegNum = _f("расходники_от_материалов")
    materials_delivery: NonNegNum = _f("доставка_материалов")
    price_rounding: Annotated[RoundingRule, BeforeValidator(parse_rounding)] = _f("округление_цены")
    prepayment: Annotated[Num, Field(ge=0, le=1)] = _f(
        "предоплата"
    )  # доля итога КП; больше 1 — отрицательный остаток
    quick_estimate_per_m: tuple[Num, Num] = _f("быстрая_оценка_за_пог_м")

    @field_validator("quick_estimate_per_m")
    @classmethod
    def _range_positive_ascending(cls, value: tuple[float, float]) -> tuple[float, float]:
        low, high = value
        if low <= 0 or high < low:
            raise ValueError(
                "вилка должна быть из двух положительных чисел по возрастанию, например [85000, 110000]"
            )
        return value


class ApplianceItem(Section):
    id: str
    type: str = _f("тип")
    nominal_width: Int = Field(alias="типоразмер", gt=0)
    width: Int = Field(alias="ширина", gt=0)
    height: Int = Field(alias="высота", gt=0)
    depth: Int = Field(alias="глубина", gt=0)
    niche_width: Int = Field(alias="ниша_ширина", gt=0)
    niche_height: Int = Field(alias="ниша_высота", gt=0)
    niche_depth: Int = Field(alias="ниша_глубина", gt=0)


class Appliances(Section):
    mock: StrictBool
    items: list[ApplianceItem] = _f("позиции")

    @model_validator(mode="after")
    def unique_ids(self):
        _unique_ids(self.items, "прибора", "позиции")
        return self


# --- economy.yaml -----------------------------------------------------------


class EconomyWhere(Section):
    """Какие модули подходят правилу: шаблон, по желанию — подъёмник в наполнении или вставка."""

    template: str = _f("шаблон")
    lift: StrictBool | None = Field(default=None, alias="подъёмник")
    insert: str | None = Field(default=None, alias="вставка")


class EconomyChange(Section):
    """Что меняется в копии модуля. Новый шаблон собирает наполнение заново: остаётся только указанное."""

    template: str | None = Field(default=None, alias="шаблон")
    shelves: NonNegInt | None = Field(default=None, alias="полок")
    remove_lift: StrictBool = Field(default=False, alias="убрать_подъёмник")
    remove_insert: str | None = Field(default=None, alias="убрать_вставку")

    @model_validator(mode="after")
    def _not_empty(self) -> "EconomyChange":
        if self.template is None and self.shelves is None and not self.remove_lift and not self.remove_insert:
            raise ValueError(
                "ключ «на»: пусто, укажите хотя бы одно из «шаблон», «полок», «убрать_подъёмник», «убрать_вставку»"
            )
        return self


class EconomyCase(Section):
    where: EconomyWhere = _f("где")
    change: EconomyChange = _f("на")


class EconomyRule(Section):
    id: str = Field(min_length=1)
    name: str = Field(alias="название", min_length=1)
    client_text: str = Field(alias="текст_клиенту", min_length=1)
    cases: list[EconomyCase] = Field(alias="случаи", min_length=1)


class Economy(Section):
    mock: StrictBool
    rules: list[EconomyRule] = _f("замены")

    @model_validator(mode="after")
    def _ids_unique(self) -> "Economy":
        _unique_ids(self.rules, "замены", "замены")
        return self


# --- master.yaml ------------------------------------------------------------


Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]  # пустая и из одних пробелов — ошибка


class Master(Section):
    """Реквизиты мастера для КП."""

    mock: StrictBool
    name: Text = Field(alias="имя")
    phone: Text = Field(alias="телефон")
    city: Text = Field(alias="город")
    status: Literal["самозанятый", "ИП"] = _f("статус")
    inn: str = Field(alias="инн", pattern=r"^[0-9]{12}$")
    offer_days: PosInt = _f("срок_действия_предложения_дней")


class Workshop(BaseModel):
    """Мастерская целиком. Заполняется загрузчиком, не из YAML напрямую."""

    equipment: Equipment
    construction: Construction
    materials: Materials
    hardware: Hardware
    labor: Labor
    pricing: Pricing
    appliances: Appliances
    economy: Economy
    master: Master
    mock_files: list[str]

    @property
    def is_mock(self) -> bool:
        return bool(self.mock_files)
