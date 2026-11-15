# three.js r147 (встроен в HTML 3D-вида, тикет 42)

- Версия: npm `three@0.147.0` (r147), лицензия MIT (`LICENSE`).
- Источник: npm-пакет `three@0.147.0`, tgz sha256 `03b50c0c820f7ae92010ce464200904c92214938ea7f751bd0e96e0499330988`.
- `three.min.js` — `build/three.min.js`, sha256 `f34446bf875b5fb0dcd93819ffe1d9e182d46634ee855f5d904c6c4ac7cdbc95`.
- `OrbitControls.js` — `examples/js/controls/OrbitControls.js` (не модуль, вешается на `THREE.OrbitControls`),
  sha256 `b4c6e53f98538535b11fd6655627d47fb5877b3ad972568bff0a7026c4b6d5c4`.
- r147 — последняя версия, в которой есть non-module сборка (`examples/js`); с r148 её убрали.

Файлы не правятся. `kitchen 3d` встраивает их в HTML обычными `<script>` без `src`: файл открывается без сети.
Обновление — отдельное решение: заменить файлы, версию в `engine/kitchen/views_html.py` (`THREE_VERSION`)
и этот README.
