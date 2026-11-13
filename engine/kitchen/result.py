"""Общая основа результатов API: пометка мока.

Любой результат, посчитанный из данных мастерской, наследует `Result` и получает
`mock` и `mock_files`. CLI по ним печатает плашку (см. `kitchen.cli.emit`).
"""

from pydantic import BaseModel

MOCK_BANNER = "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА"


class Result(BaseModel):
    mock: bool = False
    mock_files: list[str] = []
