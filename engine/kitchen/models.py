"""Модели файлов мастерской. Поля английские, ключи YAML (русские) — алиасы."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _f(alias: str):
    return Field(alias=alias)


# --- equipment.yaml ---------------------------------------------------------


class Saw(Section):
    type: str = _f("тип")
    max_cut_length: float = _f("макс_длина_реза")
    kerf: float = _f("пропил")


class SheetTrim(Section):
    perimeter_trim: float = _f("обрезка_по_периметру")


class EdgingMachine(Section):
    machine: str = _f("станок")
    allowance_per_side: float = _f("припуск_на_сторону")


class Drilling(Section):
    method: str = _f("способ")
    hinges: str = _f("петли")


class Equipment(Section):
    mock: bool
    saw: Saw = _f("пила")
    sheet: SheetTrim = _f("лист")
    edging: EdgingMachine = _f("кромка")
    drilling: Drilling = _f("присадка")
    case_assembly: str = _f("сборка_корпуса")


# --- construction.yaml ------------------------------------------------------


class LowerModule(Section):
    height: float = _f("высота")
    depth: float = _f("глубина")
    scheme: str = _f("схема")
    rail_width: float = _f("царга_ширина")


class UpperModule(Section):
    height: float = _f("высота")
    depth: float = _f("глубина")
    scheme: str = _f("схема")


class TallModule(Section):
    height: float = _f("высота")
    depth: float = _f("глубина")


class BackPanel(Section):
    material: str = _f("материал")
    contour_offset: float = _f("отступ_по_контуру")
    fastening_pitch: float = _f("крепёж_шаг")


class Shelf(Section):
    side_gap: float = _f("отступ_от_боковин")
    front_gap: float = _f("отступ_спереди")
    pins: int = _f("полкодержателей")


class Legs(Section):
    height: float = _f("высота")
    up_to_800: int = _f("до_800")
    over_800: int = _f("свыше_800")


class ThreeDrawers(Section):
    top: float = _f("верхний")
    others: str = _f("остальные")


class Facades(Section):
    gap: float = _f("зазор")
    height_lower: str = _f("высота_нижнего")
    single_door_width: str = _f("одна_дверь_ширина")
    two_doors_if_wider_than: float = _f("две_двери_если_ширина_больше")
    two_doors_width: str = _f("две_двери_ширина")
    three_drawers: ThreeDrawers = _f("ящики_3_шт")


class Screws(Section):
    per_hinge: int = _f("на_петлю")
    per_drawer: int = _f("на_ящик")


class ConstructionEdging(Section):
    case: str = _f("корпус")
    rails: str = _f("царги")
    shelves: str = _f("полки")
    reserve: float = _f("запас")
    purchase_rounding: str = _f("округление_закупки")


class Tandembox(Section):
    guide_length: float = _f("длина_направляющей")
    bottom: str = _f("дно")
    back: str = _f("задняя_стенка")
    material: str = _f("материал")


# Таблица ступеней: [[порог, значение], ...]; берётся первая ступень, где значение <= порога.
Steps = list[tuple[float, int]]


class Construction(Section):
    mock: bool
    case_thickness: float = _f("толщина_корпуса")
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
    edging: ConstructionEdging = _f("кромка")
    tandembox: Tandembox = _f("ящик_tandembox")


# --- materials.yaml ---------------------------------------------------------


class SheetMaterial(Section):
    id: str
    name: str = _f("название")
    sheet: tuple[float, float] = _f("лист")
    texture: bool | str = _f("текстура")
    price: float = _f("цена")
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
    price_per_m: float = _f("цена_за_м")
    sale: str = _f("продажа")
    shop: str = _f("магазин")


class Countertop(Section):
    id: str
    name: str = _f("название")
    sheet: tuple[float, float] = _f("лист")
    price: float = _f("цена")
    shop: str = _f("магазин")


class FacadeMaterial(Section):
    id: str
    name: str = _f("название")
    price_per_m2: float = _f("цена_за_м2")
    outsourced: bool = _f("заказ_на_стороне")
    lead_days: int = _f("срок_раб_дней")
    shop: str = _f("магазин")


class Materials(Section):
    mock: bool
    sheets: list[SheetMaterial] = _f("листовые")
    edges: list[EdgeMaterial] = _f("кромка")
    countertops: list[Countertop] = _f("столешницы")
    facades: list[FacadeMaterial] = _f("фасады")


# --- hardware.yaml ----------------------------------------------------------


class HardwareItem(Section):
    id: str
    name: str = _f("название")
    pack: int = _f("упаковка")
    price: float = _f("цена")
    shop: str = _f("магазин")


class Hardware(Section):
    mock: bool
    items: list[HardwareItem] = _f("позиции")


# --- labor.yaml -------------------------------------------------------------


class LaborNorms(Section):
    cut_per_part: float = _f("распил_на_деталь")
    edge_per_m: float = _f("кромка_на_метр")
    drill_per_part: float = _f("присадка_на_деталь")
    case_assembly: float = _f("сборка_корпуса")
    drawer_assembly_extra: float = _f("сборка_доп_на_ящик")
    install_per_module: float = _f("монтаж_на_модуль")
    sink_cutout: float = _f("врезка_мойки")
    hob_cutout: float = _f("врезка_варочной")
    site_visit: float = _f("выезд")
    measuring: float = _f("замер")
    design: float = _f("проектирование")


class Labor(Section):
    mock: bool
    hourly_rate: float = _f("ставка_в_час")
    norms_min: LaborNorms = _f("нормы_минут")


# --- pricing.yaml -----------------------------------------------------------


class Pricing(Section):
    mock: bool
    materials_markup: float = _f("наценка_на_материалы")
    consumables_share: float = _f("расходники_от_материалов")
    materials_delivery: float = _f("доставка_материалов")
    price_rounding: str = _f("округление_цены")
    prepayment: float = _f("предоплата")
    quick_estimate_per_m: tuple[float, float] = _f("быстрая_оценка_за_пог_м")


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
