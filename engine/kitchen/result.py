"""Общая основа результатов API: пометка мока.

Любой результат, посчитанный из данных мастерской, наследует `Result` и получает
`mock`, `mock_files` и `warning`. При моке `warning` равен плашке, и она попадает в JSON
любой команды; CLI печатает плашку и в тексте (см. `kitchen.cli.emit`).
"""

from pydantic import BaseModel, computed_field

MOCK_BANNER = "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА"


class Result(BaseModel):
    mock: bool = False
    mock_files: list[str] = []

    @computed_field
    @property
    def warning(self) -> str | None:
        return MOCK_BANNER if self.mock else None
