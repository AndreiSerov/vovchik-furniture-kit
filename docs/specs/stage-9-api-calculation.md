# Этап 9. Расчёт одним модулем, штамп PDF, слои под линтером

Статус: `needs-triage`. Спека и тикеты написаны 2026-11-18 по разбору скилла `codebase-design` и прогону import-linter. В тот же день прошли две проверки готовности; ответы приняты по рекомендациям ревьюера, замечания обеих проверок внесены. Ждёт третьей проверки.

## Problem Statement

`kitchen.api` вырос до 2,6 тыс. строк. Почти каждый вывод (детали, раскрой, кромка, присадка, сборочный лист, чек-лист выезда, закупка, смета, КП, виды, расстановка, проверки) представлен двумя-тремя функциями: `X_from_order`, `X_from_project`, `X_and_save`; у проверок это `check_order`/`check_project`. Вокруг них живут помощники `_X_writer`, `_build_X`, `_X_files`. Каждая функция сама проходит одну и ту же цепочку: найти проект (заказ или файл), загрузить мастерскую, взять бриф, посчитать детали, построить вывод, при сохранении записать файлы и зарегистрировать вывод.

Что из этого следует:
- Правило «какой бриф идёт в вывод» нигде не записано одной строкой. Каждая функция повторяет его по-своему, со своими исключениями (таблица «Выводы расчёта» ниже).
- Аргументы PDF раскроя и кромки записаны дважды: в `cut_and_save`/`edges_and_save` и в `production_and_save`. Если поправить одно место, другое отстанет.
- За одну команду мастерская бывает загружена повторно (`drilling_from_project`), а `compute_parts` выполняется дважды (`_build_parts` и `_build_cut`).
- CLI в каждой команде сам выбирает между функцией заказа и функцией файла.
- Каждый писатель PDF и HTML 3D принимает одни и те же `order`, `version`, `mock`, `on` и передаёт их дальше. Колонтитул и водяной знак мока рисует общий слой `kitchen.pdf`.

Разовый прогон import-linter показал, что слои ADR 0002 в целом соблюдаются: CLI напрямую ходит только в `api`, `errors`, `result`, а расчёты не трогают БД. Нарушений два: раскладка видов импортирует `kitchen.pdf` ради шрифтов и размера страницы, а писатели сборочного листа и чек-листа берут `TextLine` из писателя присадки. В `scripts/check` этих правил нет.

## Solution

Для мастера и скиллов ничего не меняется: те же команды, тексты, JSON, PDF и HTML, те же числа, коды выхода, файлы выводов и записи журнала.

Внутри движка будет три изменения:
1. **Расчёт.** Это модуль на одну версию проекта — заказа или файла. Проект и бриф он читает один раз, мастерскую загружает один раз при первом выводе, выводы считает лениво и кэширует. Все выводы записываются через `save` и одну таблицу сохранения, а `production` — это `save` документов цеха.
2. **Штамп.** Писатели PDF и HTML 3D получают один объект штампа вместо четырёх аргументов.
3. **Линтер слоёв.** import-linter входит в `scripts/check` с контрактами слоёв ADR 0002, оба найденных нарушения устранены.

## User Stories

1. Как мастер, я хочу, чтобы `kitchen parts`, `check`, `cut`, `edges`, `drilling`, `assembly`, `trip`, `3d`, `purchase`, `estimate`, `quote` и `production` после рефакторинга выдавали те же тексты, JSON, PDF и HTML и те же коды выхода, что и до него, чтобы ничего не перепроверять.
2. Как мастер, я хочу, чтобы `--project` по-прежнему не писал файлов и записей в БД, чтобы черновик не попадал в заказ.
3. Как мастер, я хочу, чтобы правило брифа у каждого вывода осталось прежним: чек-лист из `--project` печатает шапку из «—», а детали, смета, КП, виды и проверки из `--project` идут с брифом заказа, если он есть.
4. Как мастер, я хочу, чтобы `kitchen production` записывал все документы цеха одной транзакцией с откатом и одной записью «сделано», как сейчас.
5. Как мастер, я хочу, чтобы смета и КП по-прежнему оставляли в журнале запись «сделано» с прежним текстом, чтобы «Следующий шаг» в `status` не сбился.
6. Как мастер, я хочу, чтобы PDF и HTML 3D на моке по-прежнему несли плашку «МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА», чтобы черновик на моке не ушёл клиенту.
7. Как мастер, я хочу, чтобы содержимое записанных `purchase.md`, `purchase.json`, `estimate.md`, `estimate.json`, `quote.json` не изменилось, чтобы закупка и смета сходились с прошлыми.
8. Как разработчик, я хочу получать любой вывод из одного объекта расчёта, чтобы новому выводу хватало одного метода и одной строки в таблице сохранения.
9. Как разработчик, я хочу, чтобы правило «какой бриф идёт в вывод» было записано в одном месте, чтобы его можно было прочитать и проверить тестом.
10. Как разработчик, я хочу, чтобы аргументы писателя каждого вывода были записаны один раз, чтобы `production` и отдельная команда не расходились.
11. Как разработчик, я хочу, чтобы за одну команду, включая `production`, мастерская загружалась один раз, а детали проекта считались один раз, чтобы расчёт не повторял работу.
12. Как разработчик, я хочу, чтобы CLI получал расчёт по заказу или файлу одной строкой и не ветвился в каждой команде.
13. Как разработчик, я хочу, чтобы тесты логики шли через интерфейс расчёта, а старые функции были удалены, чтобы интерфейс `api` стал узким.
14. Как разработчик, я хочу, чтобы `scripts/check` падал, если CLI напрямую импортирует что-то кроме `api`, `errors`, `result`, если модуль расчётов импортирует `store` или `api`, если слой нарушен или если новый модуль не внесён в контракты, чтобы ADR 0002 держался кодом.
15. Как разработчик, я хочу, чтобы писатели PDF и HTML 3D не зависели друг от друга, а общие части (`TextLine`, метрики) лежали в общем слое.
16. Как будущий автор LangChain-инструментов (ADR 0002), я хочу маленький интерфейс: расчёт, его выводы, `save` и `production`, чтобы инструменты легли на него без обёрток.
17. Как ревьюер, я хочу снимок всех выводов, их особых случаев и записанных файлов до рефакторинга, чтобы равенство «до и после» проверялось тестом, а не чтением диффа.

## Implementation Decisions

### Модуль и интерфейс

- **Где живёт.** Новый модуль пакета `kitchen.calculation`. Правило переноса: всё, что нужно расчёту и сохранению, переезжает в `calculation`, а `api` импортирует это оттуда. `calculation` не импортирует `api`, поэтому цикла нет. Переезжают:
  - все типы результатов выводов (`PartsSource`, `PartsResult`, `CutResult`, `EdgingResult`, `DrillingResult`, `AssemblyResult`, `TripResult`, `PurchaseResult`, `EstimateResult`, `QuoteResult`, `ViewsResult`, `PlacementResult`, `CheckResult`, `ProductionResult`, `OutputInfo`), `OutputKind` и `PRODUCTION_KINDS`;
  - чтение проекта и брифа: `_open_order`, `OpenOrder`, `_current_version`, `_latest_brief`, `_order_project`, `_project_file`, `_brief_of_project`, `_read_json`, `parse_project_file`, `_project_hash`, `_canonical`, `_require_unique_labels`;
  - сборщики выводов: `_edging`, `_drilling`, `_assembly`, `_trip`, `_purchase`, `_estimate`, `_priced`, `_economy`, `_with_economy`, `_draft_warnings`, `_check_result`, `_placement`, `_views`, `NO_SINK_WARNING`;
  - тексты файлов: `_purchase_markdown`, `_estimate_markdown`, `_economy_markdown`;
  - сохранение: `_save_outputs`, `_output_info`, `_insert_log`, `_write_reason`.

  `status`, `import_project`, `diff_project`, бриф и журнал остаются в `api` и берут эти помощники из `calculation`. В `api` по-прежнему доступны имена, которые используют тесты и CLI: все типы результатов, `OutputKind`, `PRODUCTION_KINDS`, `parse_project_file`, `_canonical` (`tests/test_brief_api.py:299`) и `_insert_log`.
- **Форма** (набросок решает форму, а не код):

  ```python
  calc = api.calculation(order="Петров", home=None)   # или project=Path(...); ровно одно
  calc.source      # PartsSource без брифа (brief_id и brief_saved_at пусты), как у расстановки сейчас
  calc.brief       # строка брифа или None (правило ниже); calc.project — разобранный проект
  calc.parts()  calc.cut()  calc.edging()  calc.drilling()  calc.assembly()  calc.trip()
  calc.purchase()  calc.estimate(economy=False)  calc.quote(days, economy=False)
  calc.views()  calc.placement()  calc.check()  calc.economy_parts()
  api.save(calc, *results, on=None) -> tuple[Result, ...]   # всегда кортеж, в порядке аргументов
  api.production(calc, on=None) -> ProductionResult
  ```

- **Когда что читается и в каком порядке идут ошибки** (как сейчас):
  1. `calculation()` проверяет аргументы. Если не передан ни `order`, ни `project` или переданы оба — `OrderError` «Укажите либо заказ, либо файл проекта.». CLI оставляет свою проверку и тексты по командам.
  2. `calculation()` сразу читает проект и бриф. Для заказа: заказ, текущая версия, последний бриф, предупреждение «бриф новее» (сейчас `_order_project`). Для файла: JSON, разбор, бриф заказа (`_project_file`, `_brief_of_project`). Ошибки этих шагов (нет заказа, нет проекта, битый JSON, схема) выбрасывает `calculation()`. Битый JSON в `kitchen check --project` даёт код 2, отклонённый схемой файл — код 1 (DECISIONS «`kitchen check`»).
  3. Мастерская загружается при первом вызове метода-вывода, один раз на расчёт, и **раньше** проверки повтора меток. Сейчас `_build_parts`, `_placement` и `_views` получают уже загруженную мастерскую, поэтому при повторе меток и сломанной мастерской выдаётся ошибка мастерской. Так и остаётся.
  4. `quote(days, …)` проверяет срок (`check_days`, `InputError`) до загрузки мастерской.
  5. Повтор меток проверяют методы из таблицы ниже при своём вызове. `check()` повтор меток не отвергает, а возвращает находкой `duplicate_label`.
- **Кэш.** У каждого метода кэш по набору аргументов: второй вызов с теми же аргументами отдаёт тот же объект. `quote(days, economy)` берёт `estimate(economy)` из кэша. Детали проекта (`compute_parts` по `calc.project`) считаются в расчёте один раз и общие для `parts`, `cut`, `edging`, `drilling`, `assembly`, `trip` заказа, `purchase`, `estimate` и `views` без ошибок проверок. Своими расчётами деталей остаются, как сейчас: копии проекта «экономии» (`_priced`, `_economy`), `trip()` из файла (детали без брифа), `economy_parts()` (детали копии со всеми заменами). Результаты — неизменяемые pydantic-модели, как сейчас.

### Выводы расчёта: бриф и особые правила

Таблица собирает в одно место то, что сейчас разнесено по функциям `api.py`. Имена в ней — сегодняшние. Порядок предупреждений внутри ячейки соответствует порядку в списке `warnings`.

| Метод | Бриф в `source` и в проверках | Особые правила (сохранить как есть) |
|---|---|---|
| `parts()` | `calc.brief` | предупреждения: деталей, затем «бриф новее»; повтор меток — отказ |
| `cut()` | `calc.brief` | детали из кэша; предупреждения: деталей, «бриф новее», пропил |
| `edging()`, `drilling()`, `assembly()` | из `parts()` | — |
| `trip()` | заказ — из `parts()`; **файл — детали без брифа**, у `source` пусты `brief_id` и `brief_saved_at` | шапка из брифа заказа; у файла и у заказа без брифа шапка из «—» и `NO_BRIEF_WARNING` после предупреждений деталей |
| `purchase()` | из `parts()` | предупреждения: деталей, пропил; `mock_notice`. **Новое:** работает и для файла (сейчас закупка только по заказу), CLI `purchase` остаётся только для заказа |
| `estimate(economy)` | из `parts()` | предупреждения: деталей, `NO_SINK_WARNING`, пропил, у файла затем `_draft_warnings`; «экономия» считается после добавления черновика; копии «экономии» без брифа (`_priced`) |
| `quote(days, economy)` | из `estimate(economy)` | срок проверяется первым; `quote.json` пишется без `warnings` |
| `views()` | `calc.brief` | порядок: повтор меток (отказ), ошибки расстановки (отказ), проверки. Если есть находки `error`, берутся `buildable_module_parts`, а не `parts()` (на таком проекте `compute_parts` бросает `OrderError`). Предупреждения: деталей, затем «бриф новее», без пропила и черновика |
| `placement()` | `source` без брифа | повтор меток — отказ |
| `check()` | `calc.brief` | `brief_note` у заказа — первое предупреждение заказа, у файла `None`; `parts()` не зовёт |
| `economy_parts()` | детали копии со всеми заменами — с `calc.brief`; цены копий — без брифа | `warnings` — только предупреждения деталей копии, без «бриф новее» (как у файла сейчас). **Новое:** работает и для заказа (сейчас только для файла) |

### Сохранение

- **Таблица сохранения** сопоставляет типу результата список файлов: вид вывода `store.OutputKind`, имя файла, поле результата, куда идёт `OutputInfo`, и писатель по результату и штампу. Писатель получает результат с уже заполненными `output`/`output_html`/`outputs`, как сейчас, поэтому JSON-файлы содержат свои `outputs`. Необязательно у типа есть текст записи «сделано». Таблица — единственное место, где имена файлов выводов связаны с писателями и где вызываются `write_cutting_map` и `write_edging_map`.

  | Результат | Файлы: вид → имя (поле) | «Сделано» |
  |---|---|---|
  | раскрой | `cutting` → `cutting-map.pdf` (`output`) | — |
  | кромка | `edging` → `edging-map.pdf` (`output`) | — |
  | присадка | `drilling` → `drilling-map.pdf` (`output`) | — |
  | сборочный лист | `assembly` → `assembly.pdf` (`output`) | — |
  | чек-лист | `trip` → `trip-checklist.pdf` (`output`) | — |
  | закупка | `purchase_md` → `purchase.md`, `purchase_json` → `purchase.json` (`outputs`) | — |
  | смета | `estimate_md` → `estimate.md`, `estimate_json` → `estimate.json` (`outputs`) | «смета посчитана из версии N» |
  | КП | `quote_pdf` → `quote.pdf`, `quote_json` → `quote.json` (`outputs`) | «КП посчитано из версии N, срок <working_days(days)>» |
  | виды | `views` → `kitchen-views.pdf` (`output`), `views_html` → `kitchen-3d.html` (`output_html`) | — |

- **`save(calc, *results, on=None)`**:
  - Проверки идут по порядку, ошибка — `OrderError`, файлов и записей нет:
    1. расчёт из файла: «Расчёт из файла (--project) не сохраняется: файлы пишет только заказ.»;
    2. результат без строки в таблице (`parts`, `check`, `placement`, `economy_parts`);
    3. вид, повторённый в одном вызове.
  - Возвращает кортеж копий результатов с заполненными полями из таблицы, в порядке аргументов, даже если результат один.
  - Записи `outputs` идут в порядке аргументов, внутри результата — в порядке таблицы.
  - Все файлы и все записи «сделано» пишутся одним `_save_outputs` в одной транзакции. Логика `_save_outputs` не меняется: проверка версии, блокировка, откат, резервные копии.
  - `on` — дата штампа; если не передан, берётся `store.today()`.
- **`production(calc, on=None)`** — это `save(calc, cut(), edging(), purchase(), assembly(), trip())` с текстом «документы цеха посчитаны из версии N». Текст передаётся закрытым параметром `save`. `PRODUCTION_KINDS` выводится из таблицы по этим пяти результатам и совпадает с прежним кортежем.

### Штамп

- **`Stamp`**: поля `order: str`, `version: int | None`, `mock: bool`, `on: date` (дата уже вычислена). Объявлен в модуле метрик (см. ниже), реэкспортируется из `kitchen.pdf` и из `kitchen.api` начиная с тикета 68. `save`/`production` строят его из `result.source.order`, `result.source.version`, `result.mock` и `on or store.today()`.
- Все писатели принимают `stamp` вместо `order`, `version`, `mock`, `on`: `write_cutting_map`, `write_edging_map`, `write_drilling_map`, `write_assembly`, `write_trip`, `write_quote`, `write_views`, `write_html`, `render_html`. Конструктор общего документа `kitchen.pdf.PdfDocument` тоже принимает штамп целиком. Тексты колонтитула («Заказ … · версия проекта … · дд.мм.гггг · стр. N из M») и плашек не меняются.

### Слои

- `TextLine` переезжает в `kitchen.pdf`.
- Новый модуль основы — `kitchen.metrics`. В нём регистрация шрифтов, `FONT`, `FONT_BOLD`, `page_size`, `page_area`, `Area`, перенос строк по ширине `wrap` и `Stamp`, без отрисовки. `kitchen.pdf` импортирует его и реэкспортирует эти имена. Раскладка видов импортирует `kitchen.metrics`, а не `kitchen.pdf`.
- **Контракты import-linter.** Настройка в `pyproject.toml`, `uv run lint-imports` в `scripts/check` после `ruff`, `.import_linter_cache/` в `.gitignore`. Имена контрактов задаются ровно так:
  1. «Слои ADR 0002». Тип `layers`; модули одного слоя не обязаны быть независимы, разделитель `:`. Сверху вниз:
     - `kitchen.cli`;
     - `kitchen.api`, `kitchen.calculation`;
     - рендер: `cutting_pdf`, `edging_pdf`, `drilling_pdf`, `assembly_pdf`, `trip_pdf`, `quote_pdf`, `views_pdf`, `views_html`, `scene3d`, `pdf`;
     - домен: `assembly`, `brief`, `checks`, `common`, `cutting`, `drilling`, `economy`, `estimate`, `geometry`, `models`, `parts`, `placement`, `project`, `purchase`, `quote`, `store`, `tall`, `trip`, `views`, `workshop`;
     - основа: `errors`, `format`, `formulas`, `metrics`, `ral`, `result`.

     `kitchen.skill_lint` импортирует `kitchen.cli` и в контракт не входит.
  2. «CLI ходит только в api». Тип `forbidden`, `allow_indirect_imports = true`. Источник — `kitchen.cli`; запрещены все модули пакета, кроме `cli`, `api`, `errors`, `result`, `skill_lint`.
  3. «Писатели независимы». Тип `independence`: семь модулей `*_pdf` и `views_html`.
  4. «Расчёты не ходят в БД». Тип `forbidden`, `allow_indirect_imports = true`. Источники: `parts`, `cutting`, `drilling`, `assembly`, `trip`, `estimate`, `economy`, `quote`, `purchase`, `checks`, `placement`, `views`, `tall`, `common`. Запрещены: `store`, `api`, `calculation`.
- **Предусловие сети.** `import-linter>=2.1` (разделитель `:` в слоях появился в 2.1) ставится через `uv add --dev "import-linter>=2.1"`. Для этого нужна сеть; 2026-11-18 она была (`uv run --with import-linter` сработал). Если у сборщика сети нет, тикет 67 останавливается с отчётом, а не отмечается `manual`: без пакета `scripts/check` красный, и коммиты дальше не пройдут.

### Удаление старой формы (contract)

Тикет 74 удаляет из `kitchen.api` ровно эти имена:
- публичные: `parts_from_order`, `parts_from_project`, `cut_from_order`, `cut_from_project`, `cut_and_save`, `edges_from_order`, `edges_from_project`, `edges_and_save`, `drilling_from_order`, `drilling_from_project`, `drilling_and_save`, `assembly_from_order`, `assembly_from_project`, `assembly_and_save`, `trip_from_order`, `trip_from_project`, `trip_and_save`, `purchase_from_order`, `purchase_and_save`, `estimate_from_order`, `estimate_from_project`, `estimate_and_save`, `quote_from_order`, `quote_from_project`, `quote_and_save`, `views_from_order`, `views_from_project`, `views_and_save`, `placement_from_order`, `placement_from_project`, `check_order`, `check_project`, `economy_parts_from_project`, `production_and_save`;
- закрытые: `_build_parts`, `_build_cut`, `_drilling_writer`, `_assembly_writer`, `_trip_writer`, `_save_output`, `_purchase_files`, `_estimate_files`, `_estimate_run`, `_quote`;
- константы: `CUTTING_PDF`, `DRILLING_PDF`, `ASSEMBLY_PDF`, `TRIP_PDF`, `VIEWS_PDF`, `VIEWS_HTML`. Имена файлов живут в таблице сохранения; вид вывода `store.VIEWS_HTML` остаётся.

Остаются: `list_outputs`, функции раскладки (`drilling_map_layout`, `assembly_layout`, `trip_layout`, `views_layout`), `part_in_room`, `PRODUCTION_KINDS` и всё, что не относится к выводам (`status`, `import_project`, `diff_project`, бриф, журнал, `check_workshop`, быстрая вилка). Доменная `kitchen.checks.check_project` (в `api` она импортируется как `run_checks`) остаётся.

### Тесты, которые подменяют внутренности `api`

`monkeypatch.setattr(api, X)` меняет имя только в `api` и не действует на код в `calculation`. Ниже тесты, которые сломаются в 69/70. Их правят в том же тикете, где ломаются. Это исключение из правила «проверки не меняются»: меняется только точка подмены или способ устроить гонку, а проверяемое поведение остаётся прежним.

| Тест | Сейчас подменяет | После |
|---|---|---|
| `test_status_cleanup_57.py:158` | `api.quote_from_order` (версия меняется после расчёта) | построить `calc`, импортировать v2, вызвать `save(calc, calc.quote(…))` (как 70.9) |
| `test_status_cleanup_57.py:285` | `api.estimate_from_order`, `api._order_project` | то же: `calc`, затем импорт v2, затем `save` |
| `test_status_cleanup_57.py:300` | `api._insert_log` | `monkeypatch.setattr("kitchen.calculation._insert_log", …)` |
| `test_cut_pdf.py:200` | `api.write_cutting_map` | подмена писателя в строке раскроя таблицы сохранения (`kitchen.calculation`) |
| `test_views.py:485` | `api.views_page_layout` | `kitchen.calculation.views_page_layout` |
| `test_project_cleanup.py:128-130` | считает `duplicate_labels(` в `api.py` | считает в `calculation.py` |

`test_cut_pdf.py:158` подменяет `api.shutil.copy2`, то есть атрибут общего модуля `shutil`, поэтому продолжает работать. Подмена строкой `"kitchen.calculation.X"` не создаёт импорта, и страж `test_structure_guards.py` её не видит.

### Глоссарий и DECISIONS

- Новый термин «Расчёт» (calculation): одна версия проекта, заказа или файла, вместе с мастерской и брифом; из неё считаются выводы. Избегать: «контекст», «сессия», «пайплайн». В текстах для мастера «расчёт» остаётся обычным словом («расчёт и проверки идут с последним брифом»). Определение «Сметы» в `GLOSSARY.md` («внутренний расчёт мастера») переписывается на «внутренняя калькуляция мастера…», чтобы не сталкиваться с термином.
- Новая тема DECISIONS «Расчёт и сохранение выводов» с таблицами этой спеки. В тикете 74 переписываются на месте записи, которые называют удалённые или перенесённые функции: `api.check_project` и `api.check_order` (тема «`kitchen check`», `DECISIONS.md:110`, вместе с `api.parse_project_file`, `_project_file`, `_order_project`), `api._priced` и `api.economy_parts_from_project` (тема «Экономия», `DECISIONS.md:180`), `cut_from_order`/`cut_and_save` (тема «Выводы и закупка») и `drilling_and_save`/`drilling_from_order` (тема «Присадка»). Записи про доменную `kitchen.checks.check_project` (`DECISIONS.md:52`, `:368`) не трогаются.

## Testing Decisions

- Хороший тест проверяет только внешнее поведение: JSON результатов, тексты CLI, тексты записанных файлов и страниц PDF, записи БД. Внутренности расчёта не проверяются. Исключение — тесты-счётчики загрузки мастерской и `compute_parts` (история 11) через `monkeypatch` имён `load_workshop` и `compute_parts` в `kitchen.calculation`.
- **Снимок до рефакторинга** (тикет 66, первый тикет этапа, пишется и коммитится на старом коде). Снимок лежит в `tests/fixtures/stage9_before/`, а не в `tests/golden/`, где действуют другие правила (`test_golden_marks.py`). Помощник снимка — `tests/stage9_snapshot.py` (не `test_*`, pytest его не собирает), тест — `tests/test_stage9_snapshot.py`. Снимок живёт только на время этапа: тикет 74 удаляет его последним коммитом (решение мастера 2026-11-18).
  - Детерминизм: `store.today` и `store.now` подменяются на `date(2026, 11, 18)` и `"2026-11-18T12:00:00"`. У каждого случая свой временный `KITCHEN_HOME`. Пути (`source.path`, `OutputInfo.file`) в снимке записываются относительно home, а не вырезаются.
  - Входы — шесть случаев:
    1. заказ Петрова (`tests/fixtures/petrov_project.json`) с брифом Сергея, как в `test_petrov.py`;
    2. тот же файл через путь, когда заказ с брифом существует;
    3. заказ Петрова без брифа, в отдельном home;
    4. заказ Петрова, у которого после импорта сохранён второй бриф (бриф новее), `пропил: 4.2` в `equipment.yaml` tmp home, а мойку из проекта убрали;
    5. файл, где модуль не строится: проект `test_views.py:457-460` (Н3 шириной 100, Н4 шириной 1100, находка `module_build`). Детали, раскрой и смета на нём падают;
    6. файл с ошибкой проверки, где детали строятся: фикстура `broken_draft` (`tests/test_quote.py:862`, стена 1000 мм при рядах 1350 мм), чтобы поймать `_draft_warnings` у сметы и КП.
  - Для каждого случая снимаются `model_dump(mode="json")` выводов `parts`, `cut`, `edging`, `drilling`, `assembly`, `trip`, `purchase` (только заказ), `estimate` (с экономией и без), `quote` (20 дней, с экономией и без), `views`, `placement`, `check`, `economy_parts` (только файл) и всех функций раскладки. Вывод, который бросает ошибку, записывается как тип и текст ошибки; в случае 5 это `parts`, `cut`, `estimate` и всё, что строится из деталей.
  - Для случаев 1 и 4 дополнительно снимаются результаты после `*_and_save` каждого вывода и после `production_and_save`:
    - `model_dump(mode="json")` возвращённого результата с `output`/`output_html`/`outputs`;
    - тексты всех записанных `md`, `json`, `html`;
    - текст каждой страницы каждого PDF (`pypdf`);
    - строки таблицы `outputs`: вид, путь, версия;
    - записи журнала `done`: текст, версия, `auto`.
- Тесты логики идут через `api.calculation`, `api.save`, `api.production` (ADR 0002: интерфейс — это поверхность тестов). Старые тесты переводятся механически, заменой вызова, а проверки не меняются. Исключения перечислены в таблице «Тесты, которые подменяют внутренности `api`».
- **Контрактные тесты CLI** (`test_*_cli.py`): вызовы `runner.invoke` и все проверки остаются как есть. Механически заменяется только эталонный вызов API, с которым сравнивается JSON CLI (например, `tests/test_cut_cli.py:19`).
- Образцы в коде: откат с триггером БД — `tests/test_cut_pdf.py:103`, `tests/test_purchase.py:85-100`; файл `estimate.json` против результата — `tests/test_estimate.py:308`; страж по тексту исходников — `test_handles_one_place.py`; страж импортов тестов — `test_structure_guards.py`.
- Контракты import-linter проверяет `scripts/check`. Отдельные pytest закрепляют, что контракты покрывают все модули и что линтер ловит нарушение.

## Out of Scope

- Новые выводы, правила и тексты. Числа не меняются. Исключение — два новых сочетания в API без команд CLI: `purchase()` для файла и `economy_parts()` для заказа.
- `--project` у `kitchen purchase` и `kitchen production`.
- Рефакторинг расстановки и обхода модулей: он в бэклоге и делается вместе с тикетом 25.
- Деление `parts.py`. Это глубокий модуль за `build_module`/`compute_parts`, и его интерфейс уже хорош.
- LangChain-инструменты и MCP (ADR 0002).
- Изменения `status`, `import`, `diff`, брифа и журнала, кроме того, что их помощники переезжают в `calculation`.
- `ViewsResult._pages` и кэш раскладки видов (отдельный пункт бэклога).

## Further Notes

- Схема expand–contract (`to-tickets`). 66 снимает эталон, 67 и 68 готовят почву под защитой снимка, 69 и 70 добавляют новую форму рядом со старой, 71–73 переводят на неё вызывающий код, 74 удаляет старую форму. До 74 каждый тикет оставляет `scripts/check` зелёным.
- 69 — самый большой тикет: перенос около 1,2 тыс. строк и 13 методов. По `workflow.md` («Stronger model for hard tickets») его собирает модель главной сессии. Если сессии не хватит, перенос (типы, чтение, сборщики) и методы-выводы коммитятся двумя коммитами `69:` в одной ветке.
- Отчёты проверок готовности (2026-11-18) отдельными файлами не сохранены. Их замечания внесены в эту редакцию.

## Тикет 66 — Снимок выводов до рефакторинга

**What to build:** снимок из «Testing Decisions» на старом коде и тест, который сравнивает текущие выводы со снимком. Код движка не меняется. Этот тикет идёт первым в этапе.

**Blocked by:** —

**Status:** needs-triage

**Вывод:** —

**Проверки приёмки:**
- [ ] 1. `tests/fixtures/stage9_before/` содержит снимок шести случаев со всеми выводами, ошибками, раскладками, результатами `*_and_save`, файлами, страницами PDF, строками `outputs` и записями `done` из спеки. Снимок делает помощник `tests/stage9_snapshot.py` старыми функциями: `*_from_order`, `*_from_project`, `*_and_save`, `production_and_save`, `check_*`, `placement_from_*`, `economy_parts_from_project`.
- [ ] 2. `tests/test_stage9_snapshot.py` зелёный на старом коде. Два прогона помощника подряд дают байт-в-байт одинаковый снимок (тест на детерминизм).
- [ ] 3. Снимок не пустой там, где должен ловить особые правила. Отдельные проверки этих строк:
  - в случае 4 есть «бриф новее», предупреждение пропила и `NO_SINK_WARNING`;
  - в случае 6 у сметы и КП есть предупреждение черновика «Проект с ошибкой проверки (импорт его отклонит): …»;
  - в случае 5 у `views` `errors == [("module_build", "Н3")]`;
  - в случае 5 у `parts` ошибка «Модуль Н3: отверстие «конфирмат» …».
- [ ] 4. Коммит `66:` меняет только `tests/` и `PROGRESS.md`.
- [ ] 5. Тест укладывается в `timeout = 30` (`pyproject.toml`), иначе получает `@pytest.mark.timeout(…)` с замером в отчёте.

## Тикет 67 — Слои ADR 0002 под import-linter (ADR 0002)

**What to build:** `scripts/check` проверяет слои движка четырьмя контрактами из спеки. `TextLine` лежит в `kitchen.pdf`, метрики шрифтов и страницы — в `kitchen.metrics`, и раскладка видов не импортирует `kitchen.pdf`. Поведение не меняется.

**Blocked by:** 66

**Status:** needs-triage

**Вывод:** тексты, JSON, PDF и HTML не меняются.

**Проверки приёмки:**
- [ ] 1. `import-linter>=2.1` в dev-зависимостях (`pyproject.toml`, `uv.lock`); предусловие сети — в спеке. Контракты «Слои ADR 0002», «CLI ходит только в api», «Писатели независимы» и «Расчёты не ходят в БД» описаны в `pyproject.toml` с модулями из спеки, кроме `kitchen.calculation` (его вносит 69). `uv run lint-imports` — строка `scripts/check`, все контракты KEPT. `.import_linter_cache/` — в `.gitignore`.
- [ ] 2. Тест полноты контрактов: множество модулей в слоях равно множеству `engine/kitchen/*.py` без `__init__` и `skill_lint`. `forbidden_modules` контракта «CLI ходит только в api» равны всем модулям без `cli`, `api`, `errors`, `result`, `skill_lint`. В `scripts/check` есть `lint-imports`. Образец — набросок из второй проверки готовности:

  ```python
  cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"]["contracts"]
  mods = {f"kitchen.{p.stem}" for p in ENGINE.glob("*.py") if p.stem != "__init__"}
  placed = {m.strip() for line in layers_of(cfg, "Слои ADR 0002") for m in line.split(":")}
  assert placed == mods - {"kitchen.skill_lint"}
  ```

- [ ] 3. Тест: копия пакета `kitchen` во временной папке, в конец `cli.py` дописан `import kitchen.parts`. `lint-imports` с конфигом проекта и `PYTHONPATH` на копию завершается с кодом ≠ 0, в выводе есть «CLI ходит только в api». Тест укладывается в `timeout = 30`.
- [ ] 4. `TextLine` определён только в `kitchen.pdf`. `assembly_pdf` и `trip_pdf` не импортируют `drilling_pdf` (контракт 3).
- [ ] 5. `kitchen.views` не импортирует `kitchen.pdf` (контракт 1). `FONT`, `FONT_BOLD`, `register_fonts`, `page_size`, `page_area`, `Area`, `wrap` определены в `kitchen.metrics`, `kitchen.pdf` реэкспортирует их, `test_pdf.py` не меняется.
- [ ] 6. Тест снимка 66 и тесты PDF, видов и HTML зелёные без правки проверок: `test_pdf.py`, `test_cut_pdf.py`, `test_cut_pdf_dense.py`, `test_edges.py`, `test_drilling_pdf.py`, `test_assembly_pdf.py`, `test_trip.py`, `test_quote.py`, `test_views.py`, `test_views_html.py`, `test_views_js.py`.

## Тикет 68 — Штамп PDF и HTML 3D (ADR 0002)

**What to build:** все писатели PDF и HTML 3D и `PdfDocument` получают один `Stamp` с полями из спеки. `Stamp` реэкспортируется из `kitchen.pdf` и из `kitchen.api`. Числа, тексты и вид PDF и HTML не меняются.

**Blocked by:** 67

**Status:** needs-triage

**Вывод:** колонтитул PDF «Заказ <order> · версия проекта <N | не записана> · дд.мм.гггг · стр. N из M», шапка HTML «Заказ <order> · версия проекта … · дд.мм.гггг» и плашки мока остаются прежними.

**Проверки приёмки (каждая — тест):**
- [ ] 1. Тест-страж (AST): ни у одной функции из списка писателей в спеке и ни у `PdfDocument.__init__` нет параметров `order`, `version`, `mock`, `on`.
- [ ] 2. Новый `tests/test_stamp.py`, параметризованный по семи PDF-писателям, на деталях Петрова (`tests/fixtures/petrov_project.json`, через `api`) и штампе `api.Stamp(order="2026-047-petrov", version=None, mock=True, on=date(2026, 11, 18))`. Проверяет:
  - в тексте первой страницы (`pypdf`) есть «Заказ 2026-047-petrov · версия проекта не записана · 18.11.2026 · стр. 1 из»;
  - на каждой странице есть «МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА»;
  - при `mock=False` плашки нет;
  - при `version=3` в колонтитуле стоит «версия проекта 3».

  Файл ходит во внутренние модули писателей, поэтому входит в `LEGACY_INTERNAL_IMPORTS` (`test_structure_guards.py`), а причина записана в DECISIONS: «писатель со штампом нельзя вызвать через `api` без записи заказа».
- [ ] 3. В том же файле — `render_html`: шапка «Заказ 2026-047-petrov · версия проекта не записана · 18.11.2026», `id="mock-banner"` при `mock=True` и его отсутствие при `mock=False`.
- [ ] 4. Тест снимка 66 и тесты из 67.6 зелёные. Разрешена только механическая замена четырёх аргументов на `stamp=` в вызовах писателей и `PdfDocument` (`test_pdf.py:13, 57, 70, 94`); `test_pdf.py` берёт `Stamp` из `kitchen.pdf`.

## Тикет 69 — Расчёт и методы-выводы (expand)

**What to build:** `kitchen.calculation` с переносом по правилу спеки, `calculation()` и всеми методами-выводами по таблице, реэкспорт в `api`, `kitchen.calculation` в контрактах import-linter. Старые `*_from_order`, `*_from_project`, `check_*`, `placement_from_*` и `economy_parts_from_project` становятся однострочными обёртками над расчётом. `*_and_save` пока сохраняют по-старому, но результат берут из расчёта.

**Blocked by:** 68

**Status:** needs-triage

**Вывод:** тексты, JSON, файлы и записи БД не меняются.

**Проверки приёмки (каждая — тест):**
- [ ] 1. Тест снимка 66 зелёный без правки снимка.
- [ ] 2. `kitchen.calculation` внесён в слой `api` контракта «Слои ADR 0002» и в запреты «CLI ходит только в api» и «Расчёты не ходят в БД». Тест полноты 67.2 зелёный.
- [ ] 3. `api.calculation()` без аргументов и `api.calculation(order="petrov", project=path)` выдают `OrderError` «Укажите либо заказ, либо файл проекта.». `test_*_cli.py` зелёные.
- [ ] 4. Правило брифа (данные — случай 2 снимка):
  - у `calc.parts().source.brief_id` — последний бриф заказа;
  - у `calc.trip().source` пусты `brief_id` и `brief_saved_at`, шапка из «—», в `warnings` есть «брифа нет — шапка пустая»;
  - у `calc.placement().source.brief_id` пусто;
  - `calc.check().brief_note is None`.
- [ ] 5. Порядок ошибок:
  - битый JSON при сломанной мастерской (`workshop/construction.yaml` в tmp home с синтаксической ошибкой) — `OrderError` «<файл>: не JSON (строка 1, столбец 2).», `kitchen check --project` — код 2;
  - `calc.quote(0)` при сломанной мастерской — `InputError` срока;
  - проект `repeated` (`test_project_cleanup.py:113`) при сломанной мастерской: `parts()`, `cut()`, `views()`, `placement()` дают ошибку мастерской, а не повтора меток.
- [ ] 6. Счётчики (`monkeypatch` `kitchen.calculation.load_workshop` и `compute_parts`). На одном расчёте заказа Петрова последовательные `parts()`, `cut()`, `edging()`, `drilling()`, `assembly()`, `trip()`, `purchase()`, `estimate()`, `views()` дают по одному вызову каждого. После `calc.estimate()` вызов `calc.quote(20)` не увеличивает ни один счётчик.
- [ ] 7. Кэш: `calc.estimate() is calc.estimate()`, `calc.estimate(economy=True) is not calc.estimate()`.
- [ ] 8. Проект `test_views.py:457-460` из файла: `calc.views()` рисуется, `[(f.code, f.module) for f in errors] == [("module_build", "Н3")]`. `calc.parts()` бросает `OrderError` «Модуль Н3: отверстие «конфирмат» Ø5 на детали Н3-дно (x=16, y=510, z=8) пересекается с отверстием «конфирмат».».
- [ ] 9. Новое: `calc.purchase()` из файла Петрова совпадает с `purchase()` заказа Петрова по `shops` и `total`. `calc.economy_parts()` заказа совпадает с `economy_parts()` файла по `modules`, `hardware`, `warnings`.
- [ ] 10. Повтор меток (проект `repeated`): `placement()`, `parts()`, `views()` отказывают прежним текстом, `check()` возвращает находку `duplicate_label`.
- [ ] 11. Тесты из таблицы «Тесты, которые подменяют внутренности `api`», которые ломаются в этом тикете, переведены на новые точки подмены и зелёные.

## Тикет 70 — `save`, таблица сохранения, `production` (expand)

**What to build:** `api.save`, `api.production` и таблица сохранения из спеки. `*_and_save` и `production_and_save` становятся однострочными обёртками. Появляется страж старых вызовов в тестах со списком исключений.

**Blocked by:** 69

**Status:** needs-triage

**Вывод:** файлы, записи `outputs` и записи журнала «сделано» не меняются.

**Проверки приёмки (каждая — тест):**
- [ ] 1. Тест снимка 66 зелёный без правки снимка, включая результаты `*_and_save`, тексты файлов, страницы PDF, строки `outputs` и записи `done`.
- [ ] 2. `api.save(calc_файла, calc_файла.cut())` выдаёт `OrderError` «Расчёт из файла (--project) не сохраняется: файлы пишет только заказ.». Ни файлов, ни записей `outputs` не появляется.
- [ ] 3. `api.save(calc, calc.cut())` возвращает кортеж из одного результата с заполненным `output`. `api.save(calc, calc.parts())` и `api.save(calc, calc.cut(), calc.cut())` выдают `OrderError`, файлов нет. Если ошибок несколько, срабатывает первая по порядку из спеки.
- [ ] 4. Откат `save` (образец `tests/test_purchase.py:85-100`): сохранить раскрой и чек-лист на v1, импортировать v2, поставить триггер `AFTER INSERT ON outputs`, роняющий commit, затем `api.save(calc_v2, calc_v2.cut(), calc_v2.trip())` — ошибка. Оба файла v1 байт-в-байт на месте, число строк `outputs` и записей журнала не изменилось.
- [ ] 5. Откат `production` по той же схеме: `production` на v1, импорт v2, триггер, `api.production(calc_v2)` — ошибка. Все шесть файлов v1 на месте, записи «документы цеха посчитаны из версии 2» нет, число записей `done` прежнее. Без триггера: виды в `list_outputs` идут в порядке `PRODUCTION_KINDS`, запись «сделано» одна.
- [ ] 6. `save(calc, calc.estimate())` пишет «смета посчитана из версии 1». `save(calc, calc.quote(20))` пишет «КП посчитано из версии 1, срок 20 рабочих дней». `save(calc, calc.estimate(), calc.quote(20))` пишет обе записи в одной транзакции: с триггером из п. 4 — ни одной.
- [ ] 7. Записанный `purchase.json` совпадает с `PurchaseResult` с заполненными `outputs` (как `tests/test_estimate.py:308` для сметы). `quote.json` не содержит `warnings`. У видов `output` — PDF, `output_html` — HTML.
- [ ] 8. Тест-страж (AST по `engine/kitchen/*.py`): строковые литералы, равные именам файлов из таблицы сохранения, и вызовы `write_cutting_map`, `write_edging_map` встречаются вне модулей своих писателей ровно один раз — в таблице сохранения в `kitchen.calculation`. Справка CLI (`cli.py:659`, `:731`) и докстринги — это не литералы-имена, страж их не считает.
- [ ] 9. Расчёт заказа, после которого импортирована новая версия: `save` отказывает «Изменилась версия проекта во время расчёта. Повторите команду.».
- [ ] 10. Счётчики: `api.production(api.calculation(order="petrov"))` даёт по одному вызову `kitchen.calculation.load_workshop` и `compute_parts`.
- [ ] 11. `PRODUCTION_KINDS == ("cutting", "edging", "purchase_md", "purchase_json", "assembly", "trip")`.
- [ ] 12. Страж `tests/test_no_old_api.py`: в файлах `tests/` нет имён из списка удаляемых в спеке как `api.<имя>`. Исключения: все файлы, где они сейчас есть (список строится в этом тикете), а также `tests/stage9_snapshot.py`. Тикеты 72 и 73 только сокращают список.
- [ ] 13. Тесты из таблицы «Тесты, которые подменяют внутренности `api`», которые ломаются в этом тикете, переведены и зелёные.
- [ ] 14. Термин «Расчёт» и новое определение «Сметы» — в `GLOSSARY.md`. Тема «Расчёт и сохранение выводов» с таблицами спеки — в `DECISIONS.md`.

## Тикет 71 — CLI на расчёте

**What to build:** каждая команда CLI строит расчёт одной строкой: `calculation(order=…)` или `calculation(project=…)`. Команды, которые пишут файлы (`cut`, `edges`, `drilling`, `assembly`, `trip`, `3d`, `purchase`, `estimate`, `quote`), для заказа вызывают `save`, а `production` вызывает `api.production`. `parts` и `check` ничего не сохраняют ни для заказа, ни для файла.

**Blocked by:** 70

**Status:** needs-triage

**Вывод:** тексты и коды выхода CLI не меняются.

**Проверки приёмки:**
- [ ] 1. Все `tests/test_*_cli.py` зелёные. В них разрешена только механическая замена эталонного вызова API (см. «Testing Decisions»), `runner.invoke` и проверки не трогаются. Файлы, где замена сделана, уходят из исключений стража 70.12.
- [ ] 2. Тест-страж (AST): `cli.py` не обращается ни к одному атрибуту `api` из списка удаляемых в спеке.
- [ ] 3. Контракт «CLI ходит только в api» — KEPT.

## Тикет 72 — Тесты на расчёте, часть 1 (`tests/test_[a-k]*.py`)

**What to build:** тесты в файлах `tests/test_a*`–`test_k*` (кроме `test_*_cli.py`, они в 71) вызывают `api.calculation`, `api.save`, `api.production` вместо удаляемых имён. Проверки тестов не меняются, меняется только способ получить результат.

**Blocked by:** 70

**Status:** needs-triage

**Вывод:** —

**Проверки приёмки:**
- [ ] 1. Файлы `tests/test_[a-k]*.py` убраны из исключений стража 70.12, страж зелёный.
- [ ] 2. Скрипт `scripts/count-asserts <git-ref>` печатает по каждому файлу `tests/` число строк с `assert` и `pytest.raises` на ref и в рабочем дереве. В отчёте сборщика — его вывод против базы тикета: ни одно число не уменьшилось. Число собранных тестов (`pytest --collect-only -q`) не изменилось.
- [ ] 3. `scripts/check` зелёный.

## Тикет 73 — Тесты на расчёте, часть 2 (`tests/test_[l-z]*.py`, `skill_steps.py`, `conftest.py`)

**What to build:** то же для остальных файлов тестов и общих помощников.

**Blocked by:** 70, 72 (скрипт `scripts/count-asserts`)

**Status:** needs-triage

**Вывод:** —

**Проверки приёмки:**
- [ ] 1. Файлы части 2 убраны из исключений стража 70.12. В исключениях остаётся только `tests/stage9_snapshot.py`.
- [ ] 2. Как 72.2.
- [ ] 3. `scripts/check` зелёный.

## Тикет 74 — Удаление старой формы (contract) и уборка этапа

**What to build:** из `kitchen.api` удалены имена из списка спеки. Помощник снимка 66 переходит на новый интерфейс, и снимок доказывает равенство на новом коде. Последним коммитом тикета снимок, его помощник и тест удаляются (решение мастера 2026-11-18: иначе любой будущий текстовый тикет перегенерировал бы снимок). Страж 70.12 остаётся без исключений. Записи DECISIONS из спеки переписаны на месте.

**Blocked by:** 71, 72, 73

**Status:** needs-triage

**Вывод:** тексты, JSON, файлы и PDF не меняются.

**Проверки приёмки (каждая — тест):**
- [ ] 1. Параметризованный тест по списку спеки: `hasattr(api, name)` ложно для каждого удалённого имени.
- [ ] 2. Тест снимка 66 зелёный без правки снимка, при этом помощник зовёт `calculation`/`save`/`production`. Это отдельный коммит `74:`; хеш — в отчёте сборщика, ревьюер прогоняет тест на нём. `scripts/check` зелёный.
- [ ] 5. Последний коммит `74:` удаляет `tests/fixtures/stage9_before/`, `tests/stage9_snapshot.py` и `tests/test_stage9_snapshot.py`, а `tests/stage9_snapshot.py` уходит из исключений стража 70.12. `scripts/check` зелёный.
- [ ] 3. Тест-страж: в `DECISIONS.md`, `GLOSSARY.md`, `docs/agents/`, `skills/` нет `api.<имя>` для удалённых имён, а в `engine/kitchen/` нет определений и обращений к атрибутам `api` с этими именами (AST). Записи из раздела «Глоссарий и DECISIONS» спеки переписаны. `docs/specs/` и `docs/reviews/` — история, страж их не проверяет. Доменная `kitchen.checks.check_project` и вид `store.VIEWS_HTML` — не совпадения.
- [ ] 4. В `api.__all__` нет удалённых имён, а `calculation`, `save`, `production`, `Stamp` в нём есть.
