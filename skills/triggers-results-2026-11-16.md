# Выбор скилла по фразам — 2026-11-16 (тикет 31)

Прогон: координатор. Свежий субагент на Claude (Opus 5.5) видел только `name` и `description` пяти скиллов (коммит 5d365b4) и 18 фраз набора в перемешанном порядке, без ожидаемых скиллов. Итог: 18 из 18 совпали.

| Фраза | Ожидаемый | Выбранный | Модель | Дата |
|---|---|---|---|---|
| Клиент написал: хотим угловую кухню, вот переписка. Составь бриф | kitchen-brief | kitchen-brief | Claude Opus 5.5 | 2026-11-16 |
| Подготовь вопросы клиенту к замеру кухни | kitchen-brief | kitchen-brief | Claude Opus 5.5 | 2026-11-16 |
| Проведи интервью с заказчиком кухни | kitchen-brief | kitchen-brief | Claude Opus 5.5 | 2026-11-16 |
| Клиент с сайта спрашивает, сколько стоит кухня | kitchen-brief | kitchen-brief | Claude Opus 5.5 | 2026-11-16 |
| Клиент до замера пишет: «Сколько стоит такая кухня?» | kitchen-brief | kitchen-brief | Claude Opus 5.5 | 2026-11-16 |
| Поменяй ширину Н2 у Петрова на 450 | kitchen-project | kitchen-project | Claude Opus 5.5 | 2026-11-16 |
| Покажи 3D кухни Петрова | kitchen-project | kitchen-project | Claude Opus 5.5 | 2026-11-16 |
| Сделай раскрой Петрову | kitchen-production | kitchen-production | Claude Opus 5.5 | 2026-11-16 |
| Что везти на монтаж к Петрову | kitchen-production | kitchen-production | Claude Opus 5.5 | 2026-11-16 |
| Пересчитай раскрой Петрову | kitchen-production | kitchen-production | Claude Opus 5.5 | 2026-11-16 |
| Посчитай закупку Петрову | kitchen-estimate | kitchen-estimate | Claude Opus 5.5 | 2026-11-16 |
| Сколько выйдет кухня Петрова | kitchen-estimate | kitchen-estimate | Claude Opus 5.5 | 2026-11-16 |
| Сколько выйдет Петров? | kitchen-estimate | kitchen-estimate | Claude Opus 5.5 | 2026-11-16 |
| Сделай КП Петрову | kitchen-estimate | kitchen-estimate | Claude Opus 5.5 | 2026-11-16 |
| А если подешевле? | kitchen-estimate | kitchen-estimate | Claude Opus 5.5 | 2026-11-16 |
| Продолжи заказ Петрова | kitchen-order | kitchen-order | Claude Opus 5.5 | 2026-11-16 |
| Где мы остановились с Петровым? | kitchen-order | kitchen-order | Claude Opus 5.5 | 2026-11-16 |
| Что дальше по заказу Сидорова | kitchen-order | kitchen-order | Claude Opus 5.5 | 2026-11-16 |

Близкие пары, по словам субагента:
- «Сколько выйдет Петров?» / «Сколько выйдет кухня Петрова» (`kitchen-estimate`) против «Сколько стоит такая кухня?» и «Клиент с сайта…» (`kitchen-brief`). Разводит только «заказа или проекта ещё нет» в описании брифа. Что заказ Петрова есть, субагент вывел из фамилии.
- «А если подешевле?» без контекста — смета, потому что описание сметы цитирует фразу. Посреди разговора с клиентом подошёл бы и бриф.
- «Пересчитай раскрой Петрову»: правило `kitchen-order` «названная работа — в свой скилл» и прямой пример решают в пользу производства.

Оговорка: часть фраз дословно стоит в описаниях скиллов, поэтому набор проверяет описания на собственных примерах. Фразы, которых в описаниях нет, стоит добавить в уборке (48).

Выбор на GPT в Hermes — `manual`, не запускался.
