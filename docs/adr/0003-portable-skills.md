# 0003. Переносимые скиллы в формате Agent Skills

Статус: принято, 2026-11-13.

## Контекст

Скиллы должны работать в Claude Code, Codex, Qwen Code и Hermes Agent. Все они читают открытый формат Agent Skills (`SKILL.md` с `name` и `description`), но ищут скиллы в разных папках: `.claude/skills/`, `.agents/skills/`, `.qwen/skills/`, `~/.hermes/skills/`. Папка `.claude/skills/` в `.gitignore`, ею управляет `scripts/sync-skills`.

## Решение

- Основная копия скиллов — `skills/kitchen-*/` под git.
- Во frontmatter только `name` и `description`, без функций одного агента.
- `scripts/link-skills` ставит симлинки в `.claude/skills/`, `.agents/skills/`, `.qwen/skills/`. С `--hermes` — ещё и в `~/.hermes/skills/`; это запись в домашнюю папку, шаг ручной.
- Zip-сборка `kitchen-brief` для claude.ai убрана: скилл теперь вызывает движок.

## Последствия

- `scripts/check` проверяет frontmatter и то, что упомянутые в скиллах команды `kitchen` существуют.
- Выбор скилла по фразе на GPT в Hermes проверяется вручную.
