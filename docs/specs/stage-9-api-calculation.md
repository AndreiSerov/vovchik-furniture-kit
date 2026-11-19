# Этап 9. Расчёт одним модулем, штамп PDF, слои под линтером

Статус: `ready-for-agent`. Спеку и тикеты написал 2026-11-18 по разбору скилла `codebase-design` и прогону import-linter. Проверки готовности прошли 2026-11-18 (две) и 2026-11-19 (третья и четвёртая). Ответы мастер дал по рекомендациям, замечания всех четырёх проверок внесены. По решению мастера 2026-11-19 пятой проверки нет: сборка начинается с тикета 66, остальное ловит ревью тикетов.

## Problem Statement

`kitchen.api` вырос до 2,6 тыс. строк. Почти каждый вывод (детали, раскрой, кромка, присадка, сборочный лист, чек-лист выезда, закупка, смета, КП, виды, расстановка, проверки) представлен двумя-тремя функциями: `X_from_order`, `X_from_project`, `X_and_save`, а у проверок `check_order`/`check_project`. Вокруг них живут помощники `_X_writer`, `_build_X`, `_X_files`. Каждая функция сама проходит одну и ту же цепочку: найти проект (заказ или файл), загрузить мастерскую, взять бриф, посчитать детали, построить вывод, при сохранении записать файлы и зарегистрировать вывод.

Из этого следует:
- Правило «какой бриф идёт в вывод» нигде не записано одной строкой. Каждая функция повторяет его по-своему и со своими исключениями (см. таблицу «Выводы расчёта» ниже).
- Аргументы PDF раскроя и кромки записаны дважды: в `cut_and_save`/`edges_and_save` и в `production_and_save`. Поправить одно место и забыть второе легко.
- За одну команду мастерская бывает загружена повторно (`drilling_from_project`), а `compute_parts` выполняется дважды (`_build_parts` и `_build_cut`).
- CLI в каждой команде выбирает между функцией заказа и функцией файла.
- Каждый писатель PDF и HTML 3D принимает одни и те же `order`, `version`, `mock`, `on` и передаёт их дальше. Колонтитул и водяной знак мока рисует общий слой `kitchen.pdf`.

Разовый прогон import-linter показал, что слои движка в целом соблюдаются. CLI напрямую ходит только в `api`, `errors` и `result`, расчёты не трогают БД. Нарушений два: раскладка видов импортирует `kitchen.pdf` ради шрифтов и размера страницы, а писатели сборочного листа и чек-листа берут `TextLine` из писателя присадки. Ни слои движка, ни эти правила не записаны ни в ADR 0002, ни в `scripts/check`.

## Solution

Для мастера и скиллов ничего не меняется: те же команды, тексты, JSON, PDF и HTML, те же числа, коды выхода, файлы выводов и записи журнала.

Внутри движка меняются три вещи:
1. **Расчёт.** Модуль на одну версию проекта, заказа или файла. Он читает проект один раз, бриф — один раз и только когда бриф нужен, мастерскую загружает один раз при первом выводе. Выводы считаются лениво и кэшируются. Все выводы записываются через `save` по одной таблице сохранения; `production` — это `save` для документов цеха.
2. **Штамп.** Писатели PDF и HTML 3D получают один объект штампа вместо четырёх аргументов.
3. **Линтер слоёв.** Слои движка описаны в ADR 0002 и проверяются import-linter в `scripts/check`. Оба найденных нарушения устранены.

## User Stories

1. Как мастер, я хочу, чтобы `kitchen parts`, `check`, `cut`, `edges`, `drilling`, `assembly`, `trip`, `3d`, `purchase`, `estimate`, `quote` и `production` после рефакторинга выдавали те же тексты, JSON, PDF, HTML и коды выхода, что и до него, чтобы ничего не перепроверять.
2. Как мастер, я хочу, чтобы `--project` по-прежнему не писал файлов и записей в БД, чтобы черновик не попадал в заказ.
3. Как мастер, я хочу, чтобы правило брифа у каждого вывода осталось прежним, чтобы проверки шли как раньше: чек-лист из `--project` печатает шапку из «—», а детали, смета, КП, виды и проверки из `--project` идут с брифом заказа, если он есть.
4. Как мастер, я хочу, чтобы `kitchen trip --project` и расстановка из файла по-прежнему не открывали БД, чтобы битая или старая БД им не мешала и не мигрировала молча.
5. Как мастер, я хочу, чтобы `kitchen production` записывал все документы цеха одной транзакцией с откатом и одной записью «сделано», как сейчас.
6. Как мастер, я хочу, чтобы смета и КП по-прежнему оставляли в журнале запись «сделано» с прежним текстом, чтобы «Следующий шаг» в `status` не сбился.
7. Как мастер, я хочу, чтобы PDF и HTML 3D на моке по-прежнему несли плашку «МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА», чтобы черновик на моке не ушёл клиенту.
8. Как мастер, я хочу, чтобы содержимое записанных `purchase.md`, `purchase.json`, `estimate.md`, `estimate.json`, `quote.json` не изменилось, чтобы закупка и смета сходились с прошлыми.
9. Как мастер, я хочу, чтобы `kitchen 3d` рисовал виды и там, где детали падают (ошибка столешницы, модуль не строится), как сейчас, чтобы видеть кухню до исправления проекта.
10. Как разработчик, я хочу получать любой вывод из одного объекта расчёта, чтобы новому выводу хватало одного метода и одной строки в таблице сохранения.
11. Как разработчик, я хочу, чтобы правило «какой бриф идёт в вывод» было записано в одном месте, чтобы его можно было прочитать и проверить тестом.
12. Как разработчик, я хочу, чтобы аргументы писателя каждого вывода были записаны один раз, чтобы `production` и отдельная команда не расходились.
13. Как разработчик, я хочу, чтобы за одну команду, включая `production`, мастерская загружалась один раз, а детали проекта считались один раз, чтобы не повторять работу.
14. Как разработчик, я хочу, чтобы CLI получал расчёт по заказу или файлу одной строкой и не ветвился в каждой команде.
15. Как разработчик, я хочу, чтобы тесты логики шли через интерфейс расчёта, а старые функции были удалены, чтобы интерфейс `api` стал узким.
16. Как разработчик, я хочу, чтобы `scripts/check` падал, если CLI напрямую импортирует что-то кроме `api`, `errors`, `result`, если модуль расчётов импортирует `store` или `api`, если нарушен слой или если новый модуль не внесён в контракты, чтобы слои держались кодом.
17. Как разработчик, я хочу, чтобы писатели PDF и HTML 3D не зависели друг от друга, а общие части (`TextLine`, метрики, штамп) лежали в общем слое.
18. Как будущий автор LangChain-инструментов (ADR 0002), я хочу маленький интерфейс — расчёт, его выводы, `save` и `production`, — чтобы инструменты легли на него без обёрток.
19. Как ревьюер, я хочу снимок всех выводов, их особых случаев и записанных файлов до рефакторинга, чтобы равенство «до и после» проверялось тестом, а не чтением диффа.

## Implementation Decisions

### Модуль и интерфейс

- **Где живёт.** Новый модуль пакета `kitchen.calculation`. Всё, что нужно расчёту и сохранению, переезжает в него, а `api` импортирует это оттуда. `calculation` не импортирует `api`, поэтому цикла нет. Переезжают:
  - все типы результатов выводов (`PartsSource`, `PartsResult`, `CutResult`, `EdgingResult`, `DrillingResult`, `AssemblyResult`, `TripResult`, `PurchaseResult`, `EstimateResult`, `QuoteResult`, `ViewsResult`, `PlacementResult`, `CheckResult`, `ProductionResult`, `OutputInfo`), `OutputKind`, `PRODUCTION_KINDS` и `LogEntry`;
  - чтение проекта и брифа: `_open_order`, `OpenOrder`, `_current_version`, `_latest_brief`, `_order_project`, `_project_file`, `_brief_of_project`, `_read_json`, `_reject_constant`, `parse_project_file`, `ValidationContext`, `_same_order`, `_project_hash`, `_canonical`, `_require_unique_labels`;
  - построение деталей и раскроя: `_build_parts`, `_build_cut` (их зовёт `_priced`); тикет 75 вливает их в методы расчёта, чтобы детали проекта и копий «экономии» считались одним путём (решение мастера 2026-11-19);
  - сборщики выводов: `_edging`, `_drilling`, `_assembly`, `_trip`, `_purchase`, `_estimate`, `_priced`, `_economy`, `_with_economy`, `_draft_warnings`, `_check_result`, `_placement`, `_views`, `NO_SINK_WARNING`;
  - тексты файлов: `_purchase_markdown`, `_estimate_markdown`, `_economy_markdown`;
  - сохранение: `_save_outputs`, `_output_info`, `_insert_log`, `_write_reason`, `_WRITE_REASONS`.

  Если исполнитель найдёт ещё имя, без которого `calculation` пришлось бы импортировать `api`, оно тоже переезжает: правило важнее списка. `status`, `import_project`, `diff_project`, бриф и журнал остаются в `api` и берут помощники из `calculation`. В `api` (в том числе в `__all__`) остаются имена, которыми пользуются тесты и CLI: все типы результатов, `OutputKind`, `PRODUCTION_KINDS`, `PartsGap`, `working_days`, `parse_project_file`, `_canonical` (`tests/test_brief_api.py:299`) и `_insert_log`.
- **Форма** (набросок фиксирует форму, а не код):

  ```python
  calc = api.calculation(order="Петров", home=None)   # или project=Path(...); ровно одно
  calc.source      # PartsSource без брифа (brief_id и brief_saved_at пусты), как у расстановки сейчас
  calc.brief       # sqlite3.Row брифа или None; у файла читается лениво (правило ниже). Внутреннее: тесты и CLI его не читают
  calc.project     # разобранный проект
  calc.parts()  calc.cut()  calc.edging()  calc.drilling()  calc.assembly()  calc.trip()
  calc.purchase()  calc.estimate(economy=False)  calc.quote(days, economy=False)
  calc.views()  calc.placement()  calc.check()  calc.economy_parts()
  api.save(calc, *results, on=None) -> tuple[Result, ...]   # всегда кортеж, в порядке аргументов
  api.production(calc, on=None) -> ProductionResult
  ```

- **Когда что читается и в каком порядке ошибки.** Как сейчас:
  1. `calculation()` проверяет аргументы. Если нет ни `order`, ни `project` или есть оба — `OrderError` «Укажите либо заказ, либо файл проекта.» CLI свою проверку и тексты по командам оставляет.
  2. **Заказ:** `calculation()` сразу читает заказ, текущую версию, последний бриф и предупреждение «бриф новее» (сейчас это `_order_project`, он открывает БД в любом случае).
  3. **Файл:** `calculation()` сразу читает JSON и разбирает проект (`_project_file`). Бриф заказа (`_brief_of_project`, открывает БД) читается **лениво**, при первом обращении к `calc.brief`. Его читают только методы, у которых в таблице «Выводы расчёта» бриф есть. `trip()` и `placement()` из файла бриф не читают, поэтому БД не открывается. Бриф читается раньше мастерской, как сейчас в `parts_from_project`.
  4. Ошибки шагов 2–3 (нет заказа, нет проекта, битый JSON, схема, битая БД) выбрасывает тот, кто читает: `calculation()` или метод. Битый JSON в `kitchen check --project` даёт код 2, файл, отклонённый схемой, — код 1 (DECISIONS «`kitchen check`»).
  5. Мастерская загружается при первом вызове метода-вывода, один раз на расчёт, и **раньше** проверки повтора меток. Сейчас `_build_parts`, `_placement` и `_views` получают уже загруженную мастерскую, поэтому при повторе меток и сломанной мастерской выдаётся ошибка мастерской. Так и остаётся.
  6. `quote(days, …)` проверяет срок (`check_days`, `InputError`) первым: раньше брифа и мастерской. **Это изменение** (решение мастера 2026-11-19). Сейчас `quote_from_project` читает бриф до срока, и при битой БД и неверном сроке одновременно мастер видит ошибку БД; станет видеть ошибку срока. Запись — в теме DECISIONS «Расчёт и сохранение выводов».
  7. Повтор меток проверяют методы из таблицы ниже, каждый при своём вызове. `check()` повтор меток не отвергает и возвращает находку `duplicate_label`.
  8. **Допустимое изменение порядка:** `economy_parts()` из файла сейчас читает бриф после мастерской и «экономии» (`api.py:2016-2019`). По общему правилу бриф будет читаться раньше мастерской. Видно это только при одновременно битой БД и сломанной мастерской.
- **Кэш.**
  - У каждого метода кэш по набору аргументов: второй вызов с теми же аргументами отдаёт тот же объект. `quote(days, economy)` берёт `estimate(economy)` из кэша.
  - Общий кэш деталей — это результат `compute_parts(calc.project, workshop)`, а **не** `parts()`. `parts()` поверх него считает ещё общие позиции (`compute_common`, где есть отказ «Столешница … не найдена») и проверки. `cut()` и `views()` берут только `compute_parts` и `parts()` не зовут: сейчас `_build_cut` и `_views` общих позиций не считают, и `kitchen 3d` на проекте с ошибкой столешницы рисует виды.
  - `compute_parts` по `calc.project` в расчёте выполняется один раз. Свои расчёты деталей, как сейчас, остаются у копий проекта «экономии» (`_priced`, `_economy`) и у `economy_parts()` (детали копии со всеми заменами). `trip()` из файла строит `PartsResult` без брифа, но берёт `compute_parts` из кэша: от брифа зависит только `source` и проверки, а не детали.
  - Результаты — pydantic-модели **без** `frozen` (как сейчас). Правило: результат из расчёта не правят на месте, только через `model_copy`, потому что кэш отдаёт тот же объект.

### Выводы расчёта: бриф и особые правила

Таблица переносит в одно место то, что сейчас разнесено по функциям `api.py`. Имена — сегодняшние. В ячейке предупреждения перечислены в порядке их следования в `warnings`.

| Метод | Бриф в `source` и в проверках | Особые правила (сохранить как есть) |
|---|---|---|
| `parts()` | `calc.brief` | общие позиции и проверки поверх кэша `compute_parts`; предупреждения: деталей, затем «бриф новее»; повтор меток — отказ |
| `cut()` | `calc.brief` | только кэш `compute_parts`, без общих позиций; предупреждения: деталей, «бриф новее», пропил |
| `edging()`, `drilling()`, `assembly()` | из `parts()` | — |
| `trip()` | заказ — из `parts()`; **файл — детали без брифа**, у `source` пусты `brief_id` и `brief_saved_at`, БД не открывается | шапка из брифа заказа; у файла и у заказа без брифа — шапка из «—» и `NO_BRIEF_WARNING` после предупреждений деталей |
| `purchase()` | из `parts()` | предупреждения: `parts()` (деталей, «бриф новее»), затем пропил; `mock_notice`. **Новое:** работает и для файла (сейчас закупка только по заказу); CLI `purchase` остаётся только для заказа |
| `estimate(economy)` | из `parts()` | предупреждения: `parts()` (деталей, «бриф новее»), `NO_SINK_WARNING`, пропил, у файла затем `_draft_warnings`; «экономия» считается после добавления черновика; копии «экономии» без брифа (`_priced`) |
| `quote(days, economy)` | из `estimate(economy)` | срок проверяется первым, раньше брифа и мастерской (решение мастера 2026-11-19, см. «Порядок ошибок»); `quote.json` пишется без `warnings` |
| `views()` | `calc.brief` | порядок: повтор меток (отказ), ошибки расстановки (отказ), проверки. Если есть находки `error`, берутся `buildable_module_parts`, иначе кэш `compute_parts`; `parts()` не зовёт. Предупреждения: деталей (при находках `error` их нет), затем «бриф новее», без пропила и черновика. Раскладку страниц кладёт в `result._pages` (как сейчас), писатель таблицы сохранения берёт её оттуда и `api.views_layout` не зовёт |
| `placement()` | `source` без брифа, БД у файла не открывается | повтор меток — отказ |
| `check()` | `calc.brief` | `brief_note` у заказа — первое предупреждение заказа, у файла `None`; `parts()` не зовёт |
| `economy_parts()` | детали копии со всеми заменами — с `calc.brief`; цены копий — без брифа | `warnings` — только предупреждения деталей копии, без «бриф новее» (как у файла сейчас). **Новое:** работает и для заказа (сейчас только для файла) |

### Сохранение

- **Таблица сохранения** ставит каждому типу результата в соответствие список файлов. Для каждого файла указаны вид вывода `store.OutputKind`, имя файла, поле результата для `OutputInfo` и писатель по результату и штампу. Строка таблицы зовёт писателя по глобальному имени модуля `kitchen.calculation` (например, `write_cutting_map`), поэтому тест подменяет его строкой `"kitchen.calculation.write_cutting_map"`. Писатель получает результат с уже заполненными `output`/`output_html`/`outputs`, как сейчас: JSON-файлы содержат свои `outputs`. Необязательный текст записи «сделано» тоже задаётся у типа. Таблица — единственное место, где имена файлов выводов связаны с писателями и где вызываются `write_cutting_map` и `write_edging_map`.

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
  - Проверки идут по порядку. При ошибке — `OrderError`, файлов и записей нет:
    1. расчёт из файла — «Расчёт из файла (--project) не сохраняется: файлы пишет только заказ.»;
    2. результатов нет;
    3. результат из другого расчёта (`result.source` по `order`, `version`, `content_hash` не совпадает с `calc.source`);
    4. результат без строки в таблице (`parts`, `check`, `placement`, `economy_parts`);
    5. вид повторяется в одном вызове.
  - Возвращает кортеж копий результатов с заполненными полями из таблицы, в порядке аргументов, даже если результат один.
  - Записи `outputs` идут в порядке аргументов, внутри результата — в порядке таблицы.
  - Все файлы и все записи «сделано» пишутся одним `_save_outputs` в одной транзакции. Единственное изменение `_save_outputs`: параметр `done: str | None` становится `done: list[str]`, записи пишутся в порядке аргументов `save` (решение мастера 2026-11-19). Остальная логика не меняется: проверка версии, блокировка, откат, резервные копии.
  - `on` — дата штампа, по умолчанию `store.today()`.
- **`production(calc, on=None)`** — это `save(calc, cut(), edging(), purchase(), assembly(), trip())` с одной записью «документы цеха посчитаны из версии N». Текст передаётся закрытым параметром `save`; своих записей «сделано» у документов цеха нет. `PRODUCTION_KINDS` выводится из таблицы по этим пяти результатам и совпадает с прежним кортежем.

### Штамп

- **`Stamp`** — поля `order: str`, `version: int | None`, `mock: bool`, `on: date` (дата уже вычислена). Объявлен в модуле метрик (см. ниже), реэкспортируется из `kitchen.pdf` и из `kitchen.api` начиная с тикета 68. `save`/`production` строят его из `result.source.order`, `result.source.version`, `result.mock` и `on or store.today()`.
- Все писатели принимают `stamp` вместо `order`, `version`, `mock`, `on`: `write_cutting_map`, `write_edging_map`, `write_drilling_map`, `write_assembly`, `write_trip`, `write_quote`, `write_views`, `write_html`, `render_html`. Конструктор общего документа `kitchen.pdf.PdfDocument` тоже принимает штамп целиком. Тексты колонтитула («Заказ … · версия проекта … · дд.мм.гггг · стр. N из M») и плашек не меняются.

### Слои

- **ADR 0002 дополняется** (тикет 67) разделом «Слои движка» с таблицей слоёв ниже, пометкой «дополнено 2026-11-19» и ссылкой на контракты в `pyproject.toml`. Таблица «Где что лежит» в `AGENTS.md` называет `kitchen.api` и `kitchen.calculation` (с тикета 69).
- `TextLine` переезжает в `kitchen.pdf`.
- Новый модуль основы — `kitchen.metrics`: регистрация шрифтов, `FONT`, `FONT_BOLD`, `page_size`, `page_area`, `Area`, перенос строк по ширине `wrap` и `Stamp`, без отрисовки. `kitchen.pdf` импортирует его и реэкспортирует эти имена. Раскладка видов импортирует `kitchen.metrics`, а не `kitchen.pdf`.
- **Контракты import-linter.** Настройка в `pyproject.toml` (`[tool.importlinter]` с `root_package = "kitchen"`), модули записаны полными именами `kitchen.X`, без `containers`. В `scripts/check` после `ruff` идёт `uv run lint-imports`, `.import_linter_cache/` добавлен в `.gitignore`. Имена контрактов — ровно такие:
  1. «Слои ADR 0002». Тип `layers`; модули одного слоя не обязаны быть независимы, разделитель `:`. Сверху вниз:
     - `kitchen.cli`;
     - `kitchen.api`, `kitchen.calculation`;
     - рендер: `cutting_pdf`, `edging_pdf`, `drilling_pdf`, `assembly_pdf`, `trip_pdf`, `quote_pdf`, `views_pdf`, `views_html`, `scene3d`, `pdf`;
     - домен: `assembly`, `brief`, `checks`, `common`, `cutting`, `drilling`, `economy`, `estimate`, `geometry`, `models`, `parts`, `placement`, `project`, `purchase`, `quote`, `store`, `tall`, `trip`, `views`, `workshop`;
     - основа: `errors`, `format`, `formulas`, `metrics`, `ral`, `result`.

     `kitchen.skill_lint` импортирует `kitchen.cli` и в контракт не входит.
  2. «CLI ходит только в api». Тип `forbidden`, `allow_indirect_imports = true`. Источник — `kitchen.cli`, запрещены все модули пакета, кроме `cli`, `api`, `errors`, `result`, `skill_lint`.
  3. «Писатели независимы». Тип `independence`: семь модулей `*_pdf` и `views_html`.
  4. «Расчёты не ходят в БД». Тип `forbidden`, `allow_indirect_imports = true`. Источники: `parts`, `cutting`, `drilling`, `assembly`, `trip`, `estimate`, `economy`, `quote`, `purchase`, `checks`, `placement`, `views`, `tall`, `common`. Запрещены: `store`, `api`, `calculation`.
- **Предусловие сети.** `import-linter>=2.1` (разделитель `:` в слоях появился в 2.1) ставится командой `uv add --dev "import-linter>=2.1"`, а для неё нужна сеть. 2026-11-19 сеть была: прогон `uv run --with` сработал. Если у сборщика сети нет, тикет 67 останавливается с отчётом, а не помечается `manual`: без пакета `scripts/check` красный, и дальше ни один коммит не пройдёт.

### Порядок переноса (expand)

- **Тикет 69** только переносит имена в `kitchen.calculation`, поведение и интерфейс `api` не меняются. Все функции `api` остаются и зовут перенесённые помощники.
- **Тикет 70** добавляет `calculation()` и методы-выводы. Старые `*_from_order`, `*_from_project`, `check_*`, `placement_from_*` и `economy_parts_from_project` становятся однострочными обёртками. **Тела `*_and_save` и `production_and_save` в 70 не меняются**: они по-прежнему зовут глобальные имена `api` (`quote_from_order`, `estimate_from_order`, `_order_project` и другие). Поэтому подмены этих имён в тестах продолжают работать до тикета 71 (решение мастера 2026-11-19).
- **Тикет 71** добавляет `save`, таблицу и `production`, а `*_and_save` и `production_and_save` становятся обёртками.

### Удаление старой формы (contract)

Тикет 75 удаляет из `kitchen.api` ровно эти имена:
- публичные: `parts_from_order`, `parts_from_project`, `cut_from_order`, `cut_from_project`, `cut_and_save`, `edges_from_order`, `edges_from_project`, `edges_and_save`, `drilling_from_order`, `drilling_from_project`, `drilling_and_save`, `assembly_from_order`, `assembly_from_project`, `assembly_and_save`, `trip_from_order`, `trip_from_project`, `trip_and_save`, `purchase_from_order`, `purchase_and_save`, `estimate_from_order`, `estimate_from_project`, `estimate_and_save`, `quote_from_order`, `quote_from_project`, `quote_and_save`, `views_from_order`, `views_from_project`, `views_and_save`, `placement_from_order`, `placement_from_project`, `check_order`, `check_project`, `economy_parts_from_project`, `production_and_save`;
- закрытые: `_drilling_writer`, `_assembly_writer`, `_trip_writer`, `_save_output`, `_purchase_files`, `_estimate_files`, `_estimate_run`, `_quote`;
- `_build_parts` и `_build_cut` к этому моменту лежат в `kitchen.calculation` (69). Тикет 75 вливает их в методы расчёта, и функций с этими именами в движке не остаётся.

Константы `CUTTING_PDF`, `DRILLING_PDF`, `ASSEMBLY_PDF`, `TRIP_PDF`, `VIEWS_PDF`, `VIEWS_HTML` удаляет уже **тикет 71**: имена файлов переезжают в таблицу сохранения, а тесты, скиллы и документы этих констант не используют (grep 2026-11-19). Вид вывода `store.VIEWS_HTML` остаётся.

Остаются: `list_outputs`, функции раскладки (`drilling_map_layout`, `assembly_layout`, `trip_layout`, `views_layout`), `part_in_room`, `PRODUCTION_KINDS` и всё, что не относится к выводам (`status`, `import_project`, `diff_project`, бриф, журнал, `check_workshop`, быстрая вилка). Доменная `kitchen.checks.check_project` (в `api` её импортируют как `run_checks`) тоже остаётся.

### Тесты, которые ходят во внутренности `api`

`monkeypatch.setattr(api, X)` и `api.<модуль>` действуют только на имя в `api`. В таблице — тесты, которые сломаются, и тикет, где их правят. Это исключение из правила «проверки не меняются»: меняется только точка подмены или способ устроить гонку, проверяемое поведение остаётся прежним.

| Тест | Сейчас | Тикет | После |
|---|---|---|---|
| `test_cut_pdf.py:138`, `:158` | `api.shutil.copy2`; когда `_save_outputs` уедет, `ruff check` (F401) уронит неиспользуемый `import shutil` в `api`, и исполнитель его удалит | 69 | `import shutil` в тесте, `monkeypatch.setattr(shutil, "copy2", …)` |
| `test_status_cleanup_57.py:300` | `api._insert_log` | 69 | `monkeypatch.setattr("kitchen.calculation._insert_log", …)` |
| `test_views.py:485` | `api.views_page_layout` | 69 | `"kitchen.calculation.views_page_layout"` |
| `test_project_cleanup.py:128-130` | считает `duplicate_labels(` в `api.py` | 69 | считает в `calculation.py` |
| `test_status_cleanup_57.py:158` | `api.quote_from_order` (версия меняется после расчёта) | 71 | построить `calc`, импортировать v2, вызвать `save(calc, calc.quote(20))` (как 71.9) |
| `test_status_cleanup_57.py:285` | `api.estimate_from_order`, `api._order_project` (параметр production) | 71 | `calc`, импорт v2, затем `save(calc, calc.estimate())` или `api.production(calc)` |
| `test_cut_pdf.py:192`, `:200` | `write = api.write_cutting_map` (оригинал) и подмена `api.write_cutting_map` | 71 | оригинал — `from kitchen.cutting_pdf import write_cutting_map` (модуль уже в `LEGACY_INTERNAL_IMPORTS` файла), подмена — `"kitchen.calculation.write_cutting_map"` |
| `test_cut_pdf.py:333` | прямой вызов `write_cutting_map(..., order=…, version=…, mock=…, on=…)` | 68 | механическая замена на `stamp=Stamp(…)`, `Stamp` из `kitchen.pdf` |

Подмена строкой `"kitchen.calculation.X"` не создаёт импорта, поэтому страж `test_structure_guards.py` её не видит.

### Глоссарий и DECISIONS

- **«Расчёт»** (calculation): одна версия проекта, заказа или файла, вместе с мастерской и брифом; из неё считаются выводы. Избегать: «контекст», «сессия», «пайплайн». В текстах для мастера «расчёт» остаётся обычным словом («расчёт и проверки идут с последним брифом»).
- **«Штамп»** (stamp): заказ, версия, мок и дата, которые писатель печатает в колонтитуле и плашке вывода. Избегать: «шапка» (так называется шапка чек-листа), «футер».
- Определение «Сметы» (`GLOSSARY.md:46`) меняется на «Внутренние цифры мастера: себестоимость, труд, наценка, цена»; столбец «избегать» («калькуляция») не трогается (решение мастера 2026-11-19).
- Новая тема DECISIONS «Расчёт и сохранение выводов» с таблицами спеки.
- В тикете 75 на месте переписываются записи, которые называют удалённые или перенесённые функции:
  - `DECISIONS.md:87`, `:98`, `:110` — `api.check_project`/`check_order`; в `:110` ещё `api.parse_project_file`, `_project_file`, `_order_project`;
  - `:108` — `api._require_unique_labels`, `_build_parts`, `_build_cut`;
  - `:126`, тема «Раскрой» — `api.cut_from_order`/`cut_from_project`;
  - `:156`, тема «Выводы и закупка» — `cut_from_order`/`cut_and_save`;
  - `:180` — `api._priced`, `api.economy_parts_from_project`;
  - `:189` — `api._estimate_run`;
  - `:219`, `:220` — `drilling_from_order`/`drilling_from_project`/`drilling_and_save`;
  - `:287` — `api.placement_from_project`/`placement_from_order`;
  - `:334` — `api.parts_from_project`.

  Записи о доменной `kitchen.checks.check_project` (`DECISIONS.md:52`, `:368`) не трогаются. Список строк приблизительный (строки сдвигаются). Окончательно его проверяет страж 75.3.

## Testing Decisions

- **Швы** (подтверждены мастером 2026-11-19, `/to-spec`): (1) снимок всех выводов, базовый коммит против текущего; (2) интерфейс `kitchen.api` — `calculation`, его методы, `save`, `production`; (3) контрактные тесты CLI; (4) контракты import-linter в `scripts/check`. Мимо интерфейса идут только счётчики загрузки мастерской и `compute_parts`, перенесённые точки подмены в тестах отката и гонки и `tests/test_stamp.py` (писатели напрямую, исключение стража с причиной в DECISIONS); мастер их принял.
- Хороший тест проверяет только внешнее поведение: JSON результатов, тексты CLI, тексты записанных файлов и страниц PDF, записи БД. Внутренности расчёта не проверяются, кроме тестов-счётчиков загрузки мастерской и `compute_parts` (история 13): они подменяют `load_workshop` и `compute_parts` в `kitchen.calculation` через `monkeypatch`.
- **Общие входы проверок.** Каждая проверка ниже называет один из этих входов:
  - **«заказ Петрова»** — tmp home (фикстура `home` из `tests/conftest.py`), затем `api.brief_save("2026-047-petrov", tests/fixtures/petrov_brief.json)` и `api.import_project("2026-047-petrov", tests/fixtures/petrov_project.json)`, как в фикстуре `petrov` (`tests/test_assembly.py:19`);
  - **«v2»** — тот же проект с `ceiling_height: 2650`, импортированный в тот же заказ (`tests/test_purchase.py:85-100`);
  - **«файл Петрова»** — `tests/fixtures/petrov_project.json`, скопированный в tmp home.
- **Снимок до рефакторинга** (тикет 66, первый тикет этапа). **Снимок нигде не хранится**: ни в рабочей папке, ни в git (решение мастера 2026-11-19; к хранению больших файлов мастер вернётся, когда проект переедет на нормальный хостинг). Каждый прогон теста строит его заново дважды и сравнивает:
  - «до» — движок и скиллы из базового коммита `086cf59` (последний до правок движка этапа 9), извлечённые `git archive` во временную папку и подключённые через `PYTHONPATH`;
  - «после» — текущий движок.

  Помощник снимка — `tests/stage9_snapshot.py` (имя не `test_*`, pytest его не собирает; `--out DIR` пишет снимок в папку для ручного сравнения), тест — `tests/test_stage9_snapshot.py`. Данные мастерской (`workshop/`) и входные фикстуры в обоих прогонах берутся из текущего репо, различается только движок. Если базового коммита нет (неполный клон), тест падает с подсказкой `git fetch --unshallow`; CI делает полный клон (`fetch-depth: 0` в `.github/workflows/check.yml`). Тест живёт только на время этапа: тикет 75 удаляет его последним коммитом (решение мастера 2026-11-18).
  - **Детерминизм.** `store.today` и `store.now` подменяются строкой: `monkeypatch.setattr("kitchen.store.today", …)` даёт `date(2026, 11, 18)`, а `now` — `"2026-11-18T12:00:00+00:00"` (с поясом, как в остальных тестах). Импорта `kitchen.store` в тестах не появляется. У каждого случая свой tmp home. Абсолютный префикс home в обоих написаниях (`str(home)` и `str(home.resolve())`; на macOS это `/var/…` и `/private/var/…`) заменяется на `<HOME>` везде: в `source.path`, в `OutputInfo.file` и в текстах записанных файлов (`outputs[].file` в `purchase.json`, `estimate.json`, `quote.json`). Входные файлы копируются в home. В `kitchen-3d.html` содержимое встроенных `<script id="three">` и `<script id="orbit">` заменяется на их sha256, а остальной HTML хранится целиком.
  - **Шесть случаев:**
    1. заказ Петрова;
    2. файл Петрова при существующем заказе с брифом (заказ Петрова в том же home);
    3. заказ без брифа: `api.order_new("2026-047-petrov", …)`, затем импорт `petrov_project.json`, в отдельном home;
    4. бриф новее, пропил, без мойки. Шаги:
       - `order_new`;
       - бриф 1 — копия `petrov_brief.json`, из `appliances.items.value` которой убран элемент `sink`;
       - импорт копии `petrov_project.json`, из `appliances` которой убран прибор типа `sink`; модуль Н5 (`base_sink`) **остаётся**, иначе импорт отклоняет ряд по сумме ширин;
       - бриф 2 — та же копия;
       - в `workshop/equipment.yaml` tmp home (flow-словарь на строке 2) `пропил: 4}` заменить на `пропил: 4.2}`.

       Четвёртая проверка готовности подтвердила пробой: у сметы ровно четыре предупреждения в таком порядке — «Н5: без задней стенки», «Бриф №2 новее брифа, с которым проверена версия 1 (№1)…», `NO_SINK_WARNING`, «пропил 4.2 мм учтён как 5 мм…» (решение мастера 2026-11-19);
    5. файл, где модуль не строится: проект `tests/test_views.py:457-460` (Н3 шириной 100, Н4 шириной 1100, находка `module_build` у субъекта Н3). Детали, раскрой и смета на нём падают;
    6. файл с ошибкой проверки, где детали строятся: фикстура `broken_draft` (`tests/test_quote.py:862`, стена 1000 мм при рядах 1350 мм). Ловит `_draft_warnings` у сметы и КП.
  - **Что снимается для каждого случая:** `model_dump(mode="json")` выводов `parts`, `cut`, `edging`, `drilling`, `assembly`, `trip`, `purchase` (только заказ), `estimate` (с экономией и без), `quote` (20 дней, с экономией и без), `views`, `placement`, `check`, `economy_parts` (только файл), а также всех функций раскладки. Если вывод бросает ошибку, записываются тип и текст ошибки; в случае 5 так будет у `parts`, `cut`, `estimate` и всего, что строится из деталей.
  - **Для случаев 1 и 4 дополнительно** — после `*_and_save` каждого вывода и после `production_and_save`:
    - `model_dump(mode="json")` возвращённого результата с `output`/`output_html`/`outputs`;
    - тексты всех записанных `md`, `json` и `html`;
    - текст каждой страницы каждого PDF (`pypdf`);
    - строки таблицы `outputs` (вид, путь, версия);
    - записи журнала `done` (текст, версия, `auto`).
  - **Время.** Оба прогона идут параллельно в подпроцессах, весь модуль — около 22 с; `pytestmark = timeout(120)`. Отдельного теста на детерминизм нет: прогоны «до» и «после» идут в разных tmp home и уже его проверяют.
- Тесты логики идут через `api.calculation`, `api.save`, `api.production` (ADR 0002: интерфейс — поверхность тестов). Старые тесты переводятся механически, заменой вызова; проверки не меняются. Исключения — в таблице «Тесты, которые ходят во внутренности `api`».
- **Контрактные тесты CLI** (`test_*_cli.py`). Вызовы `runner.invoke` и все проверки остаются как есть. Механически заменяется только эталонный вызов API, с которым сравнивается JSON CLI (например, `tests/test_cut_cli.py:19`).
- **Образцы в коде.**
  - Откат с триггером БД: `tests/test_cut_pdf.py:103`, `tests/test_purchase.py:85-100`.
  - Файл `estimate.json` против результата: `tests/test_estimate.py:308`.
  - Страж по тексту исходников: `test_handles_one_place.py`.
  - Страж импортов тестов и храповик его исключений: `test_structure_guards.py`.
- Контракты import-linter проверяет `scripts/check`. Два отдельных pytest закрепляют, что контракты покрывают все модули и что линтер ловит нарушение.

## Out of Scope

- Новые выводы, правила и тексты. Числа не меняются. Исключение — два новых сочетания в API без команд CLI: `purchase()` для файла и `economy_parts()` для заказа.
- `--project` у `kitchen purchase` и `kitchen production`.
- `frozen` у моделей результатов.
- Рефакторинг расстановки и обхода модулей: он в бэклоге и делается вместе с тикетом 25.
- Деление `parts.py`. Это глубокий модуль за `build_module`/`compute_parts`, его интерфейс и так хорош.
- LangChain-инструменты и MCP (ADR 0002).
- Изменения `status`, `import`, `diff`, брифа и журнала. Их помощники переезжают в `calculation`, сами они не меняются.
- `ViewsResult._pages` и кэш раскладки видов (отдельный пункт бэклога).

## Further Notes

- Схема expand–contract (`to-tickets`):
  - 66 снимает эталон;
  - 67 и 68 готовят почву под защитой снимка;
  - 69–71 добавляют новую форму рядом со старой;
  - 72–74 переводят на неё вызывающий код;
  - 75 удаляет старую форму и снимок.

  До 75 каждый тикет оставляет `scripts/check` зелёным.
- Тикеты 69 и 70 — самые большие. По `workflow.md` («Stronger model for hard tickets») их собирает модель главной сессии.
- Ветка этапа — `stage-9` от `main`. Отчёты проверок готовности отдельными файлами не сохранены, их замечания внесены в эту редакцию.

## Тикет 66 — Снимок выводов до рефакторинга (19)

**What to build:** помощник снимка и тест из «Testing Decisions»: тест строит снимок на базовом коммите и на текущем коде и сравнивает их. Код движка не меняется. Тикет идёт первым в этапе.

**Blocked by:** —

**Status:** ready-for-agent

**Вывод:** —

**Проверки приёмки:**
- [ ] 1. Помощник `tests/stage9_snapshot.py` строит снимок шести случаев: все выводы, ошибки, раскладки, результаты `*_and_save`, файлы, страницы PDF, строки `outputs` и записи `done`, как описано в спеке. Он использует старые функции: `*_from_order`, `*_from_project`, `*_and_save`, `production_and_save`, `check_*`, `placement_from_*`, `economy_parts_from_project`. Помощник ходит только в `kitchen.api`, а `kitchen.store` подменяет строкой. Ничего не пишет в `tests/fixtures`.
- [ ] 2. `tests/test_stage9_snapshot.py` сравнивает снимок базового коммита `086cf59` с текущим и зелёный. Тест проверяет, что прогон «до» действительно импортирует `kitchen` из базового коммита. Проверка мутацией (2026-11-19, координатор): правка текста `NO_SINK_WARNING` в движке роняет тест с diff.
- [ ] 3. Снимок не пустой там, где должен ловить особые правила. Каждую строку проверяет отдельный тест:
  - в случае 4 у сметы есть «Бриф №2 новее», предупреждение пропила и `NO_SINK_WARNING`;
  - в случае 6 у сметы и КП есть «Проект с ошибкой проверки (импорт его отклонит): …»;
  - в случае 5 у `views` `[(f.code, f.subject) for f in errors] == [("module_build", "Н3")]`;
  - в случае 5 у `parts` ошибка «Модуль Н3: отверстие «конфирмат» …»;
  - в случае 3 у `trip` есть «брифа нет — шапка пустая».
- [ ] 4. Коммит `66:` меняет только `tests/`, `PROGRESS.md` и `fetch-depth: 0` в `.github/workflows/check.yml`; файлов больше 100 КБ в нём нет. Страж `test_structure_guards.py` зелёный без новых исключений.

## Тикет 67 — Слои движка под import-linter (16, 17)

**What to build:** ADR 0002 дополнен разделом «Слои движка». `scripts/check` проверяет слои четырьмя контрактами из спеки. `TextLine` лежит в `kitchen.pdf`, метрики шрифтов и страницы — в `kitchen.metrics`, раскладка видов не импортирует `kitchen.pdf`. Поведение не меняется.

**Blocked by:** 66

**Status:** ready-for-agent

**Вывод:** тексты, JSON, PDF и HTML не меняются.

**Проверки приёмки:**
- [ ] 1. `import-linter>=2.1` в dev-зависимостях (`pyproject.toml`, `uv.lock`); предусловие сети описано в спеке. Четыре контракта с именами и модулями из спеки лежат в `pyproject.toml` (кроме `kitchen.calculation`, его вносит 69). `uv run lint-imports` — строка `scripts/check`, все контракты KEPT. `.import_linter_cache/` — в `.gitignore`.
- [ ] 2. Тест полноты контрактов: множество модулей в слоях равно множеству `engine/kitchen/*.py` без `__init__` и `skill_lint`; `forbidden_modules` контракта «CLI ходит только в api» равны всем модулям, кроме `cli`, `api`, `errors`, `result`, `skill_lint`; в `scripts/check` есть `lint-imports`. Образец:

  ```python
  contracts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"]["contracts"]
  by_name = {c["name"]: c for c in contracts}
  mods = {f"kitchen.{p.stem}" for p in ENGINE.glob("*.py") if p.stem != "__init__"}
  placed = {m.strip() for line in by_name["Слои ADR 0002"]["layers"] for m in line.split(":")}
  assert placed == mods - {"kitchen.skill_lint"}
  ```

- [ ] 3. Тест: копия пакета `kitchen` во временной папке, в конец `cli.py` дописан `import kitchen.parts`. `lint-imports --no-cache` с конфигом проекта и `PYTHONPATH` на копию завершается с кодом ≠ 0, в выводе есть «CLI ходит только в api». Тест укладывается в `timeout = 30`.
- [ ] 4. `TextLine` определён только в `kitchen.pdf`. `assembly_pdf` и `trip_pdf` не импортируют `drilling_pdf` (контракт 3).
- [ ] 5. `kitchen.views` не импортирует `kitchen.pdf` (контракт 1). `FONT`, `FONT_BOLD`, `register_fonts`, `page_size`, `page_area`, `Area`, `wrap` определены в `kitchen.metrics`, `kitchen.pdf` их реэкспортирует, `test_pdf.py` не меняется.
- [ ] 6. В ADR 0002 есть раздел «Слои движка» с таблицей слоёв и пометкой «дополнено 2026-11-19».
- [ ] 7. Без правки проверок зелёные тест снимка 66 и тесты PDF, видов и HTML: `test_pdf.py`, `test_cut_pdf.py`, `test_cut_pdf_dense.py`, `test_edges.py`, `test_drilling_pdf.py`, `test_assembly_pdf.py`, `test_trip.py`, `test_quote.py`, `test_views.py`, `test_views_html.py`, `test_views_js.py`.

## Тикет 68 — Штамп PDF и HTML 3D (7, 17)

**What to build:** все писатели PDF и HTML 3D и `PdfDocument` получают один `Stamp` с полями из спеки. `Stamp` реэкспортируется из `kitchen.pdf` и `kitchen.api`. Термин «Штамп» есть в `GLOSSARY.md`. Числа, тексты и вид PDF и HTML не меняются.

**Blocked by:** 67

**Status:** ready-for-agent

**Вывод:** колонтитул PDF «Заказ <order> · версия проекта <N | не записана> · дд.мм.гггг · стр. N из M», шапка HTML «Заказ <order> · версия проекта … · дд.мм.гггг» и плашки мока — прежние.

**Проверки приёмки (каждая — тест):**
- [ ] 1. Тест-страж (AST): ни у одной функции из списка писателей в спеке и ни у `PdfDocument.__init__` нет параметров `order`, `version`, `mock`, `on`.
- [ ] 2. Новый `tests/test_stamp.py`, параметризованный по семи PDF-писателям. Вход: детали файла Петрова (через `api`) и `api.Stamp(order="2026-047-petrov", version=None, mock=True, on=date(2026, 11, 18))`. Проверки:
  - в тексте первой страницы (`pypdf`) есть «Заказ 2026-047-petrov · версия проекта не записана · 18.11.2026 · стр. 1 из»;
  - на каждой странице есть «МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА»;
  - при `mock=False` плашки нет;
  - при `version=3` в колонтитуле «версия проекта 3».

  Файл ходит во внутренние модули писателей, поэтому добавляется в `LEGACY_INTERNAL_IMPORTS` (`test_structure_guards.py`), а в DECISIONS записывается причина: «писатель со штампом нельзя вызвать через `api` без записи заказа».
- [ ] 3. В том же файле `render_html`: шапка «Заказ 2026-047-petrov · версия проекта не записана · 18.11.2026»; `id="mock-banner"` есть при `mock=True` и отсутствует при `mock=False`.
- [ ] 4. Тест снимка 66 и тесты из 67.7 зелёные. Разрешена только механическая замена четырёх аргументов на `stamp=` в прямых вызовах писателей и `PdfDocument` в тестах: `test_pdf.py:13, 57, 70, 94` и `test_cut_pdf.py:333` (других прямых вызовов нет, grep 2026-11-19). `Stamp` оба файла берут из `kitchen.pdf`: он уже в их `LEGACY_INTERNAL_IMPORTS`.
- [ ] 5. Тест-страж по тексту: в `GLOSSARY.md` есть строка термина «Штамп».

## Тикет 69 — Перенос в `kitchen.calculation` (10, 16)

**What to build:** перенос по правилу спеки: типы результатов, чтение проекта и брифа, сборщики, тексты файлов и сохранение уходят в `kitchen.calculation`. `api` импортирует их оттуда. Новых функций нет, поведение и имена `api` (кроме перенесённых закрытых, которые `api` реэкспортирует по списку спеки) не меняются. `kitchen.calculation` вносится в контракты import-linter.

**Blocked by:** 68

**Status:** ready-for-agent

**Вывод:** тексты, JSON, файлы и записи БД не меняются.

**Проверки приёмки (каждая — тест):**
- [ ] 1. Тест снимка 66 зелёный без правки снимка.
- [ ] 2. `kitchen.calculation` есть в слое `api` контракта «Слои ADR 0002» и в запретах контрактов «CLI ходит только в api» и «Расчёты не ходят в БД». Тест полноты 67.2 и `lint-imports` зелёные.
- [ ] 3. `kitchen.calculation` не импортирует `kitchen.api` (контракт «Слои ADR 0002»: модули одного слоя, плюс тест-страж AST на прямой импорт).
- [ ] 4. Тесты из таблицы «Тесты, которые ходят во внутренности `api`» с тикетом 69 переведены и зелёные.
- [ ] 5. `api.__all__` содержит прежние имена плюс `OutputKind`, `PartsGap` и `working_days` (тесты и CLI берут их из `api`: `test_status_stale.py:142`, `cli.py:247`, `cli.py:682`). `test_*_cli.py` зелёные без правки.
- [ ] 6. Тест-страж по тексту: в таблице «Где что лежит» в `AGENTS.md` есть `kitchen.calculation`.

## Тикет 70 — Расчёт и методы-выводы (expand) (3, 4, 9, 10, 11, 13)

**What to build:** `api.calculation()` и все методы-выводы по таблице спеки. Бриф у файла читается лениво. Старые `*_from_order`, `*_from_project`, `check_*`, `placement_from_*` и `economy_parts_from_project` — однострочные обёртки над расчётом. Тела `*_and_save` и `production_and_save` не меняются.

**Blocked by:** 69

**Status:** ready-for-agent

**Вывод:** тексты, JSON, файлы и записи БД не меняются.

**Проверки приёмки (каждая — тест):**
- [ ] 1. Тест снимка 66 зелёный без правки снимка.
- [ ] 2. `api.calculation()` без аргументов и `api.calculation(order="2026-047-petrov", project=<файл Петрова>)` дают `OrderError` «Укажите либо заказ, либо файл проекта.».
- [ ] 3. Правило брифа. Вход: файл Петрова в home заказа Петрова. Проверки:
  - `calc.parts().source.brief_id` — последний бриф заказа;
  - у `calc.trip().source` пусты `brief_id` и `brief_saved_at`, шапка из «—», в `warnings` есть «брифа нет — шапка пустая»;
  - у `calc.placement().source.brief_id` пусто;
  - `calc.check().brief_note is None`.
- [ ] 4. Ленивый бриф: в tmp home лежит `orders/kitchen.db` с содержимым `garbage`, вход — файл Петрова. `calc = api.calculation(project=…)`, затем `calc.trip()` и `calc.placement()` проходят без ошибки, а `calc.parts()` даёт прежнюю ошибку «… файл не является базой данных kitchen.».
- [ ] 5. Порядок ошибок (в tmp home `workshop/construction.yaml` с синтаксической ошибкой):
  - файл `bad.json` с содержимым `{`: `api.calculation(project=…)` бросает `OrderError` с `problems == ["bad.json: не JSON (строка 1, столбец 2)."]`; `kitchen check --project bad.json` возвращает код 2;
  - `calc.quote(0)` на файле Петрова — `InputError` срока;
  - `calc.quote(0)` на файле Петрова при `orders/kitchen.db` с содержимым `garbage` — тоже `InputError` срока (срок раньше брифа, решение мастера 2026-11-19);
  - проект `repeated` (`tests/test_project_cleanup.py:113`): `parts()`, `cut()`, `views()`, `placement()` дают ошибку мастерской, а не повтора меток.
- [ ] 6. Счётчики (`monkeypatch` `kitchen.calculation.load_workshop` и `compute_parts`), вход — заказ Петрова:
  - последовательные `parts()`, `cut()`, `edging()`, `drilling()`, `assembly()`, `trip()`, `purchase()`, `estimate()`, `views()`, `check()`, `placement()` дают по одному вызову каждого;
  - после `calc.estimate()` вызов `calc.quote(20)` не увеличивает ни один счётчик.
- [ ] 7. Кэш по аргументам на заказе Петрова: `calc.estimate() is calc.estimate()`, `calc.quote(20) is calc.quote(20)`, `calc.quote(20) is not calc.quote(21)`, причём `calc.quote(21).days == 21`.
- [ ] 8. Проект `tests/test_views.py:457-460` из файла:
  - `calc.views()` рисуется, `[(f.code, f.subject) for f in calc.views().errors] == [("module_build", "Н3")]`;
  - `calc.parts()` бросает `OrderError` «Модуль Н3: отверстие «конфирмат» Ø5 на детали Н3-дно (x=16, y=510, z=8) пересекается с отверстием «конфирмат».».
- [ ] 9. Виды без общих позиций. Вход: файл Петрова с `materials.countertop = "nope"`. `calc.views().errors == []`, а `calc.parts()` бросает `OrderError`, совпадающий с «Столешница «nope» …».
- [ ] 10. Новое:
  - `calc.purchase()` из файла Петрова совпадает с `purchase()` заказа Петрова по `shops` и `total`;
  - `calc.economy_parts()` заказа Петрова совпадает с `economy_parts()` файла Петрова (в том же home) по `modules`, `hardware`, `warnings`.
- [ ] 11. Повтор меток (проект `repeated`): `placement()`, `parts()`, `views()` отказывают прежним текстом, а `check()` возвращает находку `duplicate_label`.
- [ ] 12. `--project` ничего не пишет. В tmp home без заказа все методы расчёта файла Петрова не создают `orders/`. В home заказа Петрова те же методы на файле не меняют `api.list_outputs` и число строк журнала.
- [ ] 13. Тест-страж по тексту: в `GLOSSARY.md` есть термин «Расчёт» и новое определение «Сметы».

## Тикет 71 — `save`, таблица сохранения, `production` (expand) (5, 6, 8, 12)

**What to build:** `api.save`, `api.production` и таблица сохранения из спеки; `done` в `_save_outputs` становится списком. `*_and_save` и `production_and_save` — однострочные обёртки. Константы имён файлов удаляются из `api`. Появляется страж старых вызовов в тестах со списком исключений и храповиком.

**Blocked by:** 70

**Status:** ready-for-agent

**Вывод:** файлы, записи `outputs` и записи журнала «сделано» не меняются.

**Проверки приёмки (каждая — тест; вход — заказ Петрова, если не сказано иное):**
- [ ] 1. Тест снимка 66 зелёный без правки снимка, включая результаты `*_and_save`, тексты файлов, страницы PDF, строки `outputs` и записи `done`.
- [ ] 2. `api.save(calc, calc.cut())` для расчёта из файла Петрова — `OrderError` «Расчёт из файла (--project) не сохраняется: файлы пишет только заказ.». Ни файлов, ни записей `outputs` не появляется.
- [ ] 3. `api.save(calc, calc.cut())` возвращает кортеж из одного результата с заполненным `output`. Каждый из вызовов `api.save(calc)`, `api.save(calc, other.cut())` (здесь `other` — второй расчёт того же заказа после импорта v2), `api.save(calc, calc.parts())` и `api.save(calc, calc.cut(), calc.cut())` даёт `OrderError`, файлов нет. Если ошибок несколько, срабатывает первая по порядку из спеки: `api.save(calc_файла)` (файл и нет результатов) даёт текст про `--project`, а `api.save(calc, calc.parts(), calc.parts())` (нет строки и повтор) — ошибку об отсутствии строки в таблице.
- [ ] 4. Откат `save` (образец `tests/test_purchase.py:85-100`). Шаги: сохранить раскрой и чек-лист на v1, импортировать v2, поставить триггер `AFTER INSERT ON outputs`, роняющий commit, вызвать `api.save(calc_v2, calc_v2.cut(), calc_v2.trip())`. Ожидается ошибка; оба файла v1 байт-в-байт на месте, число строк `outputs` и записей журнала не изменилось.
- [ ] 5. Откат `production` по той же схеме: `production` на v1, импорт v2, триггер, `api.production(calc_v2)`. Ожидается ошибка; все шесть файлов v1 на месте, записи «документы цеха посчитаны из версии 2» нет, число записей `done` прежнее. Без триггера виды в `list_outputs` идут в порядке `PRODUCTION_KINDS`, запись «сделано» одна.
- [ ] 6. Записи «сделано»:
  - `save(calc, calc.estimate())` пишет «смета посчитана из версии 1»;
  - `save(calc, calc.quote(20))` пишет «КП посчитано из версии 1, срок 20 рабочих дней»;
  - `save(calc, calc.estimate(), calc.quote(20))` пишет обе записи в порядке аргументов, последние две записи `done` — `["смета посчитана из версии 1", "КП посчитано из версии 1, срок 20 рабочих дней"]`; с триггером из п. 4 нет ни одной.
- [ ] 7. Записанный `purchase.json` совпадает с `PurchaseResult` с заполненными `outputs` (как `tests/test_estimate.py:308` для сметы). `quote.json` не содержит `warnings`. У видов `output` — PDF, `output_html` — HTML.
- [ ] 8. Тест-страж (AST по `engine/kitchen/*.py`): строковые литералы, равные именам файлов из таблицы сохранения, и вызовы `write_cutting_map`, `write_edging_map` вне модулей своих писателей встречаются ровно по одному разу, в таблице сохранения в `kitchen.calculation`. Справка CLI (`cli.py:659`, `:731`) и докстринги — не литералы-имена, страж их не считает.
- [ ] 9. Расчёт заказа Петрова, после которого импортирована v2: `save(calc, calc.cut())` отказывает «Изменилась версия проекта во время расчёта. Повторите команду.».
- [ ] 10. Счётчики: `api.production(api.calculation(order="2026-047-petrov"))` даёт по одному вызову `kitchen.calculation.load_workshop` и `compute_parts`.
- [ ] 11. `PRODUCTION_KINDS == ("cutting", "edging", "purchase_md", "purchase_json", "assembly", "trip")`.
- [ ] 12. Страж `tests/test_no_old_api.py`: в файлах `tests/` нет обращений `api.<имя>` для имён из списка удаляемых в спеке. Исключения — `OLD_API_EXCEPTIONS`: все файлы, где такие обращения есть сейчас (список строится в этом тикете), плюс `tests/stage9_snapshot.py`. Храповик в том же файле: исключение, у которого в файле больше нет таких обращений, роняет тест (как `test_legacy_list_only_shrinks`).
- [ ] 13. Тесты из таблицы «Тесты, которые ходят во внутренности `api`» с тикетом 71 переведены и зелёные.
- [ ] 14. Тест-страж по тексту: в `DECISIONS.md` есть тема «Расчёт и сохранение выводов» с таблицами спеки и записью о сроке КП раньше брифа.
- [ ] 15. Константы `CUTTING_PDF`, `DRILLING_PDF`, `ASSEMBLY_PDF`, `TRIP_PDF`, `VIEWS_PDF`, `VIEWS_HTML` удалены из `api`: `hasattr(api, name)` ложно для каждой. Без этого страж п. 8 не пройдёт.

## Тикет 72 — CLI на расчёте (14)

**What to build:** каждая команда CLI строит расчёт одной строкой: `calculation(order=…)` или `calculation(project=…)`. Команды, которые пишут файлы (`cut`, `edges`, `drilling`, `assembly`, `trip`, `3d`, `purchase`, `estimate`, `quote`), для заказа вызывают `save`, `production` — `api.production`. `parts` и `check` ничего не сохраняют ни для заказа, ни для файла.

**Blocked by:** 71

**Status:** ready-for-agent

**Вывод:** тексты и коды выхода CLI не меняются.

**Проверки приёмки:**
- [ ] 1. Все `tests/test_*_cli.py` зелёные. В них разрешена только механическая замена эталонного вызова API (см. «Testing Decisions»), а `runner.invoke` и проверки не трогаются. Файлы, где замена сделана, уходят из `OLD_API_EXCEPTIONS`.
- [ ] 2. Тест-страж (AST): `cli.py` не обращается ни к одному атрибуту `api` из списка удаляемых в спеке.
- [ ] 3. Контракт «CLI ходит только в api» — KEPT.

## Тикет 73 — Тесты на расчёте, часть 1 (`tests/test_[a-k]*.py`) (15)

**What to build:** тесты в файлах `tests/test_a*`–`test_k*` (кроме `test_*_cli.py`, их переводит тикет 72) вызывают `api.calculation`, `api.save`, `api.production` вместо удаляемых имён. Проверки тестов не меняются, меняется только способ получить результат. Около 404 вызовов. Тикет можно сдавать несколькими коммитами `73:` по группам файлов, каждый с зелёным `scripts/check`.

**Blocked by:** 71

**Status:** ready-for-agent

**Вывод:** —

**Проверки приёмки:**
- [ ] 1. Файлы `tests/test_[a-k]*.py` убраны из `OLD_API_EXCEPTIONS`, страж и храповик зелёные.
- [ ] 2. Скрипт `scripts/count-asserts <git-ref>` печатает по каждому файлу `tests/` число строк с `assert` и `pytest.raises` на ref и в рабочем дереве. В отчёте сборщика — его вывод против базы тикета: ни одно число не уменьшилось. Число собранных тестов (`pytest --collect-only -q`) не изменилось.
- [ ] 3. `scripts/check` зелёный.

## Тикет 74 — Тесты на расчёте, часть 2 (`tests/test_[l-z]*.py`) (15)

**What to build:** то же для файлов `tests/test_l*`–`test_z*`. В `conftest.py` и `skill_steps.py` вызовов `api.` нет, их тикет не трогает. Около 350 вызовов.

**Blocked by:** 71, 73 (скрипт `scripts/count-asserts`)

**Status:** ready-for-agent

**Вывод:** —

**Проверки приёмки:**
- [ ] 1. Файлы части 2 убраны из `OLD_API_EXCEPTIONS`; в исключениях остаётся только `tests/stage9_snapshot.py`.
- [ ] 2. Как 73.2.
- [ ] 3. `scripts/check` зелёный.

## Тикет 75 — Удаление старой формы и снимка (contract), уборка этапа (15, 18)

**What to build:** из `kitchen.api` удалены имена из списка спеки. `_build_parts` и `_build_cut` влиты в методы расчёта: детали проекта и копий «экономии» считаются одним путём (решение мастера 2026-11-19). Помощник снимка 66 переходит на новый интерфейс, и снимок доказывает равенство на новом коде. Последним коммитом тикета снимок, его помощник и тест удаляются (решение мастера 2026-11-18: иначе каждый будущий тикет с правкой текста перегенерировал бы снимок). Записи DECISIONS из спеки переписаны на месте.

**Blocked by:** 72, 73, 74

**Status:** ready-for-agent

**Вывод:** тексты, JSON, файлы и PDF не меняются.

**Проверки приёмки (каждая — тест):**
- [ ] 1. Параметризованный тест по списку спеки: `hasattr(api, name)` ложно для каждого удалённого имени.
- [ ] 2. **Один коммит `75:`** удаляет имена (п. 1) и добавляет в помощник снимка ветку нового интерфейса (`hasattr(api, "calculation")` → `calculation`/`save`/`production`). Старая ветка остаётся: на ней идёт прогон «до» по базовому коммиту, поэтому помощник остаётся в `OLD_API_EXCEPTIONS`. На этом коммите тест снимка 66 (база против нового интерфейса) и `scripts/check` зелёные. Хеш коммита — в отчёте сборщика, ревьюер прогоняет тест на нём.
- [ ] 3. Тест-страж: в `DECISIONS.md`, `GLOSSARY.md`, `docs/agents/`, `docs/adr/`, `AGENTS.md`, `skills/` нет `api.<имя>` для удалённых имён, а в `engine/kitchen/` нет определений и обращений к атрибутам `api` с этими именами (AST). Записи из раздела «Глоссарий и DECISIONS» спеки переписаны. `docs/specs/` и `docs/reviews/` — история, страж их не проверяет. Доменная `kitchen.checks.check_project` и вид `store.VIEWS_HTML` совпадениями не считаются.
- [ ] 4. В `api.__all__` нет удалённых имён, а `calculation`, `save`, `production`, `Stamp` в нём есть.
- [ ] 5. Тест-страж (AST): в `engine/kitchen/` нет функций `_build_parts` и `_build_cut`; детали копий «экономии» строятся тем же методом расчёта, что детали проекта. Тест снимка (п. 2) подтверждает, что цены «экономии» не изменились.
- [ ] 6. Последний коммит `75:` удаляет `tests/stage9_snapshot.py` и `tests/test_stage9_snapshot.py`, `OLD_API_EXCEPTIONS` становится пустым. `fetch-depth: 0` в CI остаётся или убирается по решению координатора (без теста снимка полный клон не нужен). `scripts/check` зелёный.
