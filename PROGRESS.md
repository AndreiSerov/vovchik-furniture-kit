# PROGRESS

Источник правды о статусе задач (ADR 0004). В начале сессии читать вместе с `DECISIONS.md` и продолжать с первого незакрытого тикета. Тикет закрывается в том же коммите, что и работа над ним.

Открытые тикеты MVP прошли проверку готовности (`docs/agents/workflow.md`, «Review rounds») и готовы к сборке по порядку этого файла; отчёты ревьюеров — `docs/reviews/2026-11-15/readiness/`, решения агента по поручению мастера — `docs/reviews/2026-11-15/agent-decisions.md`. Отложенные находки ревью (`docs/reviews/2026-11-15/deferred.md`) разбирает тикет уборки своего этапа.

## Этап 0. Основа

- [x] 01 — Каркас и `check-workshop` (0.1, 0.2, 0.3) ([спека](docs/specs/stage-0-foundation.md))
- [x] 02 — `scripts/link-skills` (0.1; `--hermes` — manual, не запускался) ([спека](docs/specs/stage-0-foundation.md))
- [x] 03 — Хранилище заказов (0.4) ([спека](docs/specs/stage-0-foundation.md))
- [x] 04 — Бриф в БД и быстрая вилка (0.5) ([спека](docs/specs/stage-0-foundation.md))

## Этап 1. Модуль → детали

- [x] 05 — Пробный ход через `base_door` (1.1, 1.2, 1.3) ([спека](docs/specs/stage-1-modules-to-parts.md))
- [x] 06 — Полная схема в импорте и `diff` (1.1, 0.4) ([спека](docs/specs/stage-1-modules-to-parts.md))
- [x] 07 — Нижние шаблоны (1.4) ([спека](docs/specs/stage-1-modules-to-parts.md))
- [x] 08 — Верхние шаблоны и `appliances.yaml` (1.4) ([спека](docs/specs/stage-1-modules-to-parts.md))
- [x] 09 — Угловые и пенал (1.5) ([спека](docs/specs/stage-1-modules-to-parts.md))
- [x] 10 — Проверки проекта (1.6) ([спека](docs/specs/stage-1-modules-to-parts.md))
- [x] 11 — Заказ Петрова целиком (1.7) ([спека](docs/specs/stage-1-modules-to-parts.md))

## Этап 2. Раскрой, кромка, закупка

- [x] 12 — Раскрой (2.1) ([спека](docs/specs/stage-2-cutting-edging-purchase.md))
- [x] 13 — Слой PDF, карта раскроя, учёт выводов (2.2) ([спека](docs/specs/stage-2-cutting-edging-purchase.md))
- [x] 14 — Карта кромления (2.3) ([спека](docs/specs/stage-2-cutting-edging-purchase.md))
- [x] 15 — Закупка и `kitchen production` (2.4) ([спека](docs/specs/stage-2-cutting-edging-purchase.md))

**ВЕХА 1** — остановиться, показать результаты (пути к PDF, ключевые цифры), дождаться подтверждения.


## Этап 3. Смета и КП

- [x] 16 — Смета (3.1, 3.2, 3.3) ([спека](docs/specs/stage-3-estimate.md))
- [x] 33 — Хотфикс: мастерская, заказы и раскрой отклоняют неверный ввод ([спека](docs/specs/hotfix-input-guards.md))
- [x] 34 — Хотфикс: проект и шаблоны отклоняют неверный ввод ([спека](docs/specs/hotfix-input-guards.md))
- [x] 35 — Столешница по общей длине ([спека](docs/specs/hotfix-input-guards.md))
- [x] 17 — Вариант «экономия» (3.3) ([спека](docs/specs/stage-3-estimate.md))
- [x] 18 — КП (3.4) ([спека](docs/specs/stage-3-estimate.md))
- [ ] 45 — Уборка этапа 3 и хотфикса ([спека](docs/specs/stage-3-estimate.md))

**ВЕХА 2** — остановиться, показать результаты, дождаться подтверждения. Показана и подтверждена 2026-11-15 (выводы Петрова в `orders/_veha-2/`).


## Этап 4. Присадка и сборка

- [x] 19 — Присадка: конфирматы и петли (4.1, 4.2) ([спека](docs/specs/stage-4-drilling-assembly.md))
- [x] 20 — Присадка: направляющие и полкодержатели (4.1, 4.2) ([спека](docs/specs/stage-4-drilling-assembly.md))
- [ ] 22 — Сборочный лист (4.4) ([спека](docs/specs/stage-4-drilling-assembly.md))

Чек-лист выезда (40) и уборка этапа 4 (46) ждут схему брифа (30) и стоят в этапе 6 после неё.

## Этап 5. Проект и 3D

- [x] 24 — Устаревшие выводы в `status` (5.3) ([спека](docs/specs/stage-5-project-3d.md))
- [ ] 32 — Пустое место в ряду (5.4) ([спека](docs/specs/stage-5-project-3d.md))
- [ ] 50 — Верхний угловой по глубине верхних (1.5, 5.1) ([спека](docs/specs/stage-5-project-3d.md))
- [ ] 23 — Расстановка модулей в комнате (5.1) ([спека](docs/specs/stage-5-project-3d.md))
- [ ] 41 — Виды кухни в PDF и команда `kitchen 3d` (5.1) ([спека](docs/specs/stage-5-project-3d.md))
- [ ] 42 — 3D в HTML (5.1) ([спека](docs/specs/stage-5-project-3d.md))
- [ ] 47 — Уборка этапа 5 ([спека](docs/specs/stage-5-project-3d.md))

## Этап 6. Скиллы

- [x] 26 — Статические проверки скиллов (6.6) ([спека](docs/specs/stage-6-skills.md))
- [ ] 36 — Команда `kitchen check` (6.1) ([спека](docs/specs/stage-6-skills.md))
- [ ] 30 — Схема брифа и правила `check_brief.py` (6.5) ([спека](docs/specs/stage-6-skills.md))
- [ ] 40 — Чек-лист выезда (4.4, этап 4) ([спека](docs/specs/stage-4-drilling-assembly.md))
- [ ] 49 — Следующий шаг в `status` (6.4) ([спека](docs/specs/stage-6-skills.md))
- [ ] 46 — Уборка этапа 4 ([спека](docs/specs/stage-4-drilling-assembly.md))
- [ ] 27 — Скилл `kitchen-project` (6.1, 6.4) ([спека](docs/specs/stage-6-skills.md))
- [ ] 28 — Скилл `kitchen-production` (6.2, 6.4) ([спека](docs/specs/stage-6-skills.md))
- [ ] 29 — Скилл `kitchen-estimate` (6.3, 6.4) ([спека](docs/specs/stage-6-skills.md))
- [ ] 43 — `kitchen-brief`: голос мастера и одна вилка (6.5, 6.4) ([спека](docs/specs/stage-6-skills.md))
- [ ] 44 — Быстрый опрос и эвалы `kitchen-brief` (6.5) ([спека](docs/specs/stage-6-skills.md))
- [ ] 31 — Триггеры и «продолжи заказ Петрова» (6.6) ([спека](docs/specs/stage-6-skills.md))
- [ ] 48 — Уборка этапа 6 ([спека](docs/specs/stage-6-skills.md))

## Бэклог (после MVP)

Решение 2026-11-15: MVP — от брифа до документов цеха и КП, с упрощённым 3D. Остальное ждёт здесь.

- [ ] 21 — Наклейки 70×37 (4.3; решение мастера 2026-11-15: не нужны в MVP) ([спека](docs/specs/stage-4-drilling-assembly.md))
- [ ] 25 — Черновик раскладки (5.2; нужен грилинг) ([спека](docs/specs/stage-5-project-3d.md))
- [ ] 37 — Целевое 3D: детали, фасады, столешница (5.1) ([спека](docs/specs/stage-5-project-3d.md))
- [ ] 38 — Антресоль над холодильником (5.4) ([спека](docs/specs/stage-5-project-3d.md))
- [ ] 39 — Раскладка и целевое 3D в `kitchen-project` (6.1) ([спека](docs/specs/stage-6-skills.md))
