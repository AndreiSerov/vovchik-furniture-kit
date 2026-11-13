"""Ошибки движка: сообщения по-русски, готовые к показу мастеру."""


class KitchenError(Exception):
    """Общая основа ошибок движка. `problems` — список русских сообщений."""

    def __init__(self, problems: list[str] | str):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("\n".join(self.problems))


class WorkshopError(KitchenError):
    """Мастерская не найдена или её данные неверны."""


class OrderError(KitchenError):
    """Заказ не найден, неоднозначен, или данные заказа неверны. Сообщения по-русски."""


class InputError(KitchenError):
    """Неверный аргумент команды (например, длина кухни не больше нуля)."""
