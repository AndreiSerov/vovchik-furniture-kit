# vovchik-furniture-kit

Это проект для моделирования мебели на заказ для мебельщика: генерация 3D-модели, план раскроя, расчёт стоимости материалов.

Before feature work, read [`docs/agents/workflow.md`](docs/agents/workflow.md). Before any change, follow [`docs/agents/agreements.md`](docs/agents/agreements.md). Before naming anything, read [`GLOSSARY.md`](GLOSSARY.md).

## Agent skills

Install them with `scripts/sync-skills`. They come from the sources pinned in `.claude/skills.toml`.

### Issue tracker

Issues live in GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

The default five role names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Начало и конец сессии

- В начале прочитай `PROGRESS.md` и `DECISIONS.md` и продолжай с первого незакрытого тикета.
- Один тикет — один коммит (или несколько мелких) с префиксом номера: `05: base_door считает детали`. Отметка в `PROGRESS.md` идёт в том же коммите. Коммит проходит только с зелёным `scripts/check`.
- На вехах 1 и 2 (отмечены в `PROGRESS.md`) остановись: покажи пути к PDF и ключевые цифры, дождись подтверждения мастера.

## Принципы

1. Числа считает код. Размеры, количества и цены не считаются «в голове»: вызывай `kitchen`.
2. Формулы и правила конструкции живут только в движке и в `workshop/construction.yaml`. Скиллы формул не содержат и числа переносят как есть.
3. Источник правды — текущая версия проекта в БД (ADR 0001). Всё, что посчитано из проекта, хранит версию-источник.
4. Файл мастерской с `mock: true` — мок. Любой вывод, посчитанный с ним, несёт «МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА» (текст, JSON, PDF, HTML). Что заменить — в `MOCKS.md`.
5. Задача — это код, тесты, зелёные тесты, коммит с номером, отметка в `PROGRESS.md`.
6. Правило неоднозначно: выбери разумный вариант, запиши в `DECISIONS.md` с пометкой «ПРОВЕРИТЬ МАСТЕРУ» и продолжай.

## Где что лежит

| Что | Где |
|---|---|
| Данные мастерской (YAML, пока моки) | `workshop/`, замены — `MOCKS.md` |
| Движок: логика в `kitchen.api`, CLI `kitchen` — тонкая обёртка (ADR 0002) | `engine/kitchen/` |
| Тесты: логика через API, CLI — контрактные | `tests/` |
| Скиллы под git (ADR 0003) | `skills/kitchen-*/` |
| Заказы, БД, выводы; не в git | `orders/` |
| Статус задач, решения | `PROGRESS.md`, `DECISIONS.md` |
| Спеки и тикеты (ADR 0004), решения | `docs/specs/`, `docs/adr/` |

Мастерская ищется по `KITCHEN_HOME`, иначе вверх от текущей папки до корня с `workshop/`.

## Какой скилл когда

| Скилл | Когда | Статус |
|---|---|---|
| `kitchen-brief` | переписка или интервью с клиентом, бриф к замеру | есть |
| `kitchen-project` | проект кухни: стены, модули, материалы | планируется |
| `kitchen-production` | раскрой, кромка, закупка, присадка, сборка | планируется |
| `kitchen-estimate` | смета, КП, вилка цены | планируется |

## Done means

`scripts/check` passes. Work needing real hardware, permissions or accounts is marked `manual`, and the user runs it. Report it as not run.

## Conventions

<!-- What's specific to this project and can't be seen in the code. -->

## Gotchas

<!-- Add one when it has actually caught someone out. -->
