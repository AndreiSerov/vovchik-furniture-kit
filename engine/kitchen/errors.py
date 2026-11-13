"""Ошибки движка: сообщения по-русски, готовые к показу мастеру."""


class WorkshopError(Exception):
    """Мастерская не найдена или её данные неверны. `problems` — список русских сообщений."""

    def __init__(self, problems: list[str] | str):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("\n".join(self.problems))


class OrderError(WorkshopError):
    """Заказ не найден, неоднозначен, или данные заказа неверны. Сообщения по-русски."""
