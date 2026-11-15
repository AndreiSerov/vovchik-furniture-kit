"""HTML 3D-вида (тикет 42): один файл, который открывается без сети.

three.js r147 (`vendor/three/`) и скрипт страницы (`web/viewer.js`) встроены обычными `<script>` без `src`, данные
сцены (`scene3d.Scene3D`) — JSON в `<script type="application/json">`. Шапка, плашка мока и плашка ошибок
проверок — обычная разметка: они не зависят от того, запустился ли 3D. Если 3D не запустилось, виден запасной
текст.
"""

import json
from datetime import date
from html import escape
from pathlib import Path

from kitchen.result import MOCK_BANNER
from kitchen.scene3d import Scene3D
from kitchen.views import checks_banner

__all__ = ["FALLBACK_TEXT", "THREE_VERSION", "render_html", "write_html"]

THREE_VERSION = "three.js r147 (0.147.0)"  # версия встроенного three.js; README в `vendor/three/`
FALLBACK_TEXT = "Откройте файл в браузере (Safari или Chrome)"

_HERE = Path(__file__).parent
_STYLE = """
html, body { margin: 0; height: 100%; font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }
body { display: flex; flex-direction: column; height: 100vh; height: 100dvh; background: #f4f4f4; color: #222; }
#head { padding: 6px 12px; font-size: 14px; background: #ffffff; border-bottom: 1px solid #ccc; }
#mock-banner { padding: 6px 12px; font-weight: bold; text-align: center; color: #ffffff; background: #cc0000; }
#errors-banner { padding: 6px 12px; font-weight: bold; text-align: center; color: #222222; background: #ffcc33; }
#view { position: relative; flex: 1; min-height: 0; overflow: hidden; }
#view canvas { display: block; width: 100%; height: 100%; }
#fallback { display: block; position: absolute; top: 40%; left: 0; right: 0; padding: 0 16px; text-align: center; font-size: 18px; }
#info { display: none; position: absolute; left: 8px; right: 8px; bottom: 8px; padding: 8px 12px; background: rgba(255,255,255,0.94); border: 1px solid #999; border-radius: 6px; font-size: 16px; }
#info-size { margin-top: 2px; font-size: 14px; color: #555; }
#hint { position: absolute; left: 8px; top: 6px; font-size: 12px; color: #666; pointer-events: none; }
""".strip()


def _read(*parts: str) -> str:
    text = _HERE.joinpath(*parts).read_text(encoding="utf-8")
    if "</script" in text.lower():  # такой текст закрыл бы тег раньше времени
        raise ValueError(f"В {'/'.join(parts)} есть закрывающий тег script.")
    return text


def _json(scene: Scene3D) -> str:
    """JSON сцены для `<script type="application/json">`: символы, которые закрыли бы тег или сломали строку,
    записаны escape-последовательностями (разбор JSON возвращает тот же текст)."""
    text = json.dumps(scene.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    for char, code in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        (" ", "\\u2028"),
        (" ", "\\u2029"),
    ):
        text = text.replace(char, code)
    return text


def render_html(scene: Scene3D, *, order: str, version: int | None, on: date, mock: bool, errors: int) -> str:
    version_text = f"версия проекта {version}" if version is not None else "версия проекта не записана"
    title = escape(f"Кухня {order} · {version_text}")
    head = escape(f"Заказ {order} · {version_text} · {on.strftime('%d.%m.%Y')}")
    banners = ""
    if mock:
        banners += f'<div id="mock-banner">{escape(MOCK_BANNER)}</div>\n'
    if errors:
        banners += f'<div id="errors-banner">{escape(checks_banner(errors))}</div>\n'
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<!-- {THREE_VERSION}, MIT; встроен целиком, сеть не нужна -->
<meta name="three-version" content="{THREE_VERSION}">
<style>
{_STYLE}
</style>
</head>
<body>
<div id="head">{head}</div>
{banners}<div id="view">
<div id="hint">Вращение — пальцем или мышью, масштаб — щипком или колесом, подпись — нажатием на модуль</div>
<div id="fallback">{escape(FALLBACK_TEXT)}</div>
<div id="info"><div id="info-caption"></div><div id="info-size"></div></div>
</div>
<script id="three">
{_read("vendor", "three", "three.min.js")}
</script>
<script id="orbit">
{_read("vendor", "three", "OrbitControls.js")}
</script>
<script id="scene-data" type="application/json">{_json(scene)}</script>
<script id="viewer">
{_read("web", "viewer.js")}
</script>
<script id="boot">
KitchenViewer.start(THREE, JSON.parse(document.getElementById("scene-data").textContent), document, window);
</script>
</body>
</html>
"""


def write_html(
    path: Path, scene: Scene3D, *, order: str, version: int | None, on: date, mock: bool, errors: int
) -> None:
    path.write_text(
        render_html(scene, order=order, version=version, on=on, mock=mock, errors=errors), encoding="utf-8"
    )
