# vovchik-furniture-kit

Моделирование мебели на заказ для мебельщика: генерация 3D-модели, план раскроя, расчёт стоимости материалов.

## Состав

- `skills/kitchen-brief/` — скилл Claude: разбор переписки с клиентом кухни или интервью, бриф к замеру, противоречия и вопросы клиенту. Пороги и цены салона — в `references/settings.json`.

## Запуск

```sh
uv sync                 # зависимости для разработки
scripts/sync-skills     # скиллы агента из .claude/skills.toml
scripts/check           # ruff + pytest
```

Проверка брифа вручную:

```sh
python3 skills/kitchen-brief/scripts/check_brief.py brief.json
```

## Сборка скилла для Claude

Архив для загрузки (Settings → Capabilities → Skills) — zip папки `skills/kitchen-brief/` без `evals/`:

```sh
mkdir -p dist && (cd skills && zip -r ../dist/kitchen-brief.zip kitchen-brief -x 'kitchen-brief/evals/*' '*/__pycache__/*')
```
