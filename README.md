# vovchik-furniture-kit

Моделирование мебели на заказ для мебельщика: генерация 3D-модели, план раскроя, расчёт стоимости материалов.

## Состав

- `engine/kitchen/` — движок и CLI `kitchen`; `workshop/` — данные мастерской (моки); `PROGRESS.md`, `DECISIONS.md`, `MOCKS.md`.
- `skills/kitchen-brief/` — скилл Claude: разбор переписки с клиентом кухни или интервью, бриф к замеру, противоречия и вопросы клиенту. Пороги и цены салона — в `references/settings.json`.

## Установка

```sh
uv tool install -e .    # команда kitchen в системе
export KITCHEN_HOME=/путь/к/папке/с/workshop   # не нужно внутри репозитория
kitchen check-workshop  # сводка по данным мастерской; --json для агентов
```

В режиме `--json` ошибки идут в stderr, а stdout остаётся пустым.

`KITCHEN_HOME` — папка, где лежит `workshop/`. Без неё `kitchen` ищет корень репозитория вверх от текущей папки. Пока в `workshop/` моки, любой вывод помечен «МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА»; что заменить — в `MOCKS.md`.

## Заказы

Заказы, версии проекта и журнал лежат в SQLite `$KITCHEN_HOME/orders/kitchen.db` (другой путь — `KITCHEN_DB`); папка `orders/` не в git. Заказ находится по номеру (`2026-047`), `номер-фамилия` или части фамилии, кириллицей или латиницей.

```sh
kitchen order new 2026-047-petrov --client "Петров П. П." --phone ... --address ...
kitchen order show petrov                # найти заказ, показать карточку
kitchen import petrov project.json       # новая версия, только если проект изменился
kitchen export petrov [--version N] [--out файл]   # project.json с номером версии
kitchen diff petrov v1 v2               # изменения модулей между версиями
kitchen log petrov --kind done|decision|next|note "текст"
kitchen status petrov                    # печатает и пишет orders/<заказ>/status.md (руками не править)
```

До этапа 1 импорт проверяет только обёртку: JSON-объект с полем `order`. Поле `version` в файле игнорируется.

## Бриф и быстрая вилка

```sh
kitchen brief save 2026-047-petrov brief.json   # проверить check_brief.py и сохранить; заказа нет — завести
kitchen brief save Петров brief.json            # одна фамилия: новый заказ со следующим номером года (ГГГГ-NNN)
kitchen brief show petrov                       # напечатать сохранённый бриф (JSON)
kitchen quick-estimate --meters 4.6             # вилка цены по pricing.yaml, с округлением и плашкой мока
```

Бриф с неизвестным полем или ошибкой схемы не сохраняется. Противоречия и пустые обязательные поля — обычное содержимое брифа: они сохраняются вместе с результатом проверки, команда печатает их число. Повторное сохранение заменяет текущий бриф (прежние остаются в БД). Одна фамилия заводит заказ, только если заказа с такой фамилией ещё нет; иначе ошибка со списком и подсказкой номера для нового. Чтобы обновить бриф существующего, указывайте номер или `номер-фамилия`. Округление берётся из `округление_цены` в `pricing.yaml` (`вверх до N` или `вниз до N`).

## Детали проекта

```sh
kitchen parts --project project.json    # детали по модулям, фурнитура, кромка, фасады; без БД
```

Расчёт поддерживает нижние шаблоны `base_door`, `base_sink`, `base_bottle`, `base_drawers` (три ящика) и `dishwasher_panel`. Формулы и правила конструкции — в `workshop/construction.yaml`. Ручные эталоны находятся в `tests/golden/`; добавленные правила помечены как мок и требуют проверки мастера.

## Разработка

```sh
uv sync                 # зависимости для разработки
scripts/sync-skills     # скиллы агента из .claude/skills.toml
scripts/check           # ruff + pytest
```

`scripts/link-skills` ставит симлинки `skills/kitchen-*` в `.claude/skills/`, `.agents/skills/`, `.qwen/skills/` (его же вызывает `sync-skills`); для Hermes один раз вручную: `scripts/link-skills --hermes` (пишет в `~/.hermes/skills/`). Запускать из основной копии репозитория, не из worktree: после удаления worktree ссылки повиснут.

Проверка брифа вручную:

```sh
python3 skills/kitchen-brief/scripts/check_brief.py brief.json
```

Схема файла проекта — `engine/kitchen/schemas/project.schema.json`. Импорт проверяет её;
номер `version` из файла игнорируется, при экспорте его ставит движок. Пенал задаётся
один раз в ряду своей стены. Для ряда и модуля можно указать `materials` с полями
`body`, `facades`, `handles`, `countertop`: значения модуля перекрывают ряд, ряд — проект.
Наполнение (`contents`), параметры угла и флаг `without_back` сохраняются при импорте;
расчёт `base_door` пока явно отказывает при таких параметрах. Для `base_sink` можно указать `without_back` и `contents.inserts`, для `base_bottle` — `contents.inserts`, для `base_drawers` — `contents.drawers: 3`. Несовместимые параметры отклоняются.
