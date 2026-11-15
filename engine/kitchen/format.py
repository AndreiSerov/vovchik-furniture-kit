"""Слова и числа для текстов вывода: склонение по числу и целое число без «.0».

Одно место для того, что раньше было записано в `quote`, `cli`, `views`, `checks`, `placement`, `scene3d`
(уборка этапа 5, тикет 47).
"""

__all__ = ["num", "plural"]


def plural(count: int, one: str, few: str, many: str) -> str:
    """«1 день», «2 дня», «5 дней», «21 день», «12 дней»."""
    last, tens = count % 10, count % 100
    form = one if last == 1 and tens != 11 else few if 2 <= last <= 4 and not 12 <= tens <= 14 else many
    return f"{count} {form}"


def num(value: float, digits: int | None = None) -> int | float:
    """Целое число — целым (85000, не 85000.0); дробное — как есть или округлённое до `digits` знаков."""
    if float(value).is_integer():
        return int(value)
    return value if digits is None else round(value, digits)
