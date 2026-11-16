"""Слова, числа и метки для текстов вывода: склонение по числу, целое число без «.0», сравнение меток.

Одно место для того, что раньше было записано в `quote`, `cli`, `views`, `checks`, `placement`, `scene3d`
(уборка этапа 5, тикет 47). Сравнение меток — повторы меток (`checks`) и порядок пеналов (`common`, тикет 54).
"""

import re
import unicodedata
from decimal import Decimal

__all__ = ["group_digits", "label_key", "label_order", "num", "plural", "purchase_digits"]

_LOOKALIKE = str.maketrans("ABCEHKMOPTXYabcehkmoptxy", "АВСЕНКМОРТХУавсенкмортху")  # латиница → кириллица


def label_key(label: str) -> str:
    """Метка для сравнения: NFKC (полноширинные цифры и буквы), без невидимых символов (категория Cf:
    U+200B, U+FEFF, мягкий перенос) и пробелов по краям, без различия регистра и латинских двойников кириллицы."""
    text = unicodedata.normalize("NFKC", label)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return text.strip().translate(_LOOKALIKE).casefold()


def label_order(label: str) -> tuple[str | int, ...]:
    """Ключ «естественного» порядка меток: буквы после `label_key`, числа как числа — П2 раньше П10."""
    pieces = re.split(r"(\d+)", label_key(label))
    return tuple(int(p) if i % 2 else p for i, p in enumerate(pieces))


def plural(count: int, one: str, few: str, many: str) -> str:
    """«1 день», «2 дня», «5 дней», «21 день», «12 дней»."""
    last, tens = count % 10, count % 100
    form = one if last == 1 and tens != 11 else few if 2 <= last <= 4 and not 12 <= tens <= 14 else many
    return f"{count} {form}"


def working_days(count: int) -> str:
    """Срок в рабочих днях: «21 рабочий день», «3 рабочих дня» (КП, PDF и журнал — одно место)."""
    return plural(count, "рабочий день", "рабочих дня", "рабочих дней")


def num(value: float, digits: int | None = None) -> int | float:
    """Целое число — целым (85000, не 85000.0); дробное — как есть или округлённое до `digits` знаков."""
    if float(value).is_integer():
        return int(value)
    return value if digits is None else round(value, digits)


def group_digits(value: int) -> str:
    """222000 -> «222 000»: пробел по разрядам, без знака рубля (КП и вилка цены)."""
    return f"{value:,}".replace(",", " ")


def purchase_digits(step_m: float) -> int:
    """Знаков после точки у кромки «к закупке»: столько, сколько знаков у шага округления, но не меньше
    одного (0.1 м — один, 0.05 м и 0.25 м — два)."""
    exponent = Decimal(str(step_m)).normalize().as_tuple().exponent
    return max(1, -exponent) if isinstance(exponent, int) else 1
