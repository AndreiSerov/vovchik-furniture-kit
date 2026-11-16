#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=10.1", "pyyaml>=6"]
# ///
"""Картинка примерного дизайна кухни: фото комнаты + образец + материалы → сервис генерации.

Запуск (uv сам поставит Pillow и PyYAML):
    uv run design_image.py catalog [--catalog ПАПКА]
    uv run design_image.py generate --room ФОТО [--sample ФОТО]... [--sample-type ID]
        [--material [РОЛЬ=]ФОТО]... [--type ТЕКСТ] [--wishes ТЕКСТ] --out ПАПКА
        [--provider openai|openrouter] [--model ИМЯ] [--n 1] [--size auto] [--quality medium]
        [--watermark ТЕКСТ] [--catalog ПАПКА]

Настройки — переменные окружения или `.env` (ищется от текущей папки вверх, окружение важнее):
    OPENAI_API_KEY, OPENROUTER_API_KEY — ключи сервисов
    DESIGN_PROVIDER (openai), DESIGN_MODEL, DESIGN_QUALITY — сервис, модель, качество
    DESIGN_WATERMARK — текст водяного знака мастера (обязателен), DESIGN_WATERMARK_LOGO — PNG-логотип
    DESIGN_CATALOG — папка каталога образцов с catalog.yaml

stdout — JSON с описанием результата; ошибки — stderr по-русски, код возврата 2.
Скрипт не зависит от движка `kitchen` и работает в любой папке.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

FONT = Path(__file__).resolve().parents[1] / "assets" / "DejaVuSans.ttf"
MOCK_LINE = "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА"
DEFAULT_MODELS = {"openai": "gpt-image-1", "openrouter": "google/gemini-2.5-flash-image"}
KEYS = {"openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
OPENAI_URL = "https://api.openai.com/v1/images/edits"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT = 300

Send = Callable[[str, dict, bytes], bytes]


class UserError(Exception):
    """Ошибка, которую видит мастер: текст по-русски, код возврата 2."""


class ServiceError(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(f"{status}: {text}")
        self.status = status
        self.text = text


# --- настройки ---


def parse_dotenv(text: str) -> dict[str, str]:
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        line = line.removeprefix("export ")
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_env(start: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """`.env` ближайший к `start` вверх по дереву, поверх — окружение."""
    merged: dict[str, str] = {}
    for folder in [start, *start.parents]:
        candidate = folder / ".env"
        if candidate.is_file():
            merged = parse_dotenv(candidate.read_text(encoding="utf-8"))
            break
    merged.update(environ)
    return merged


# --- каталог ---


def find_catalog(env: Mapping[str, str], flag: str | None) -> Path | None:
    if flag:
        return Path(flag)
    if env.get("DESIGN_CATALOG"):
        return Path(env["DESIGN_CATALOG"])
    home = env.get("KITCHEN_HOME")
    starts = [Path(home)] if home else [Path.cwd(), *Path.cwd().parents]
    for folder in starts:
        candidate = folder / "workshop" / "design-catalog"
        if (candidate / "catalog.yaml").is_file():
            return candidate
    return None


def load_catalog(folder: Path) -> dict:
    path = folder / "catalog.yaml"
    if not path.is_file():
        raise UserError(f"Каталог образцов: нет файла {path}.")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    types, missing = [], []
    for item in data.get("types") or []:
        photos = []
        for name in item.get("фото") or []:
            photo = (folder / name).resolve()
            (photos if photo.is_file() else missing).append(str(photo))
        types.append(
            {
                "id": str(item["id"]),
                "название": item.get("название", item["id"]),
                "описание": item.get("описание", ""),
                "фото": photos,
            }
        )
    return {"mock": bool(data.get("mock", False)), "types": types, "missing": missing}


# --- фото ---


def mime_of(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def read_photo(path: Path) -> tuple[bytes, str]:
    if not path.is_file():
        raise UserError(f"Фото не найдено: {path}")
    data = path.read_bytes()
    mime = mime_of(data)
    if mime is None:
        raise UserError(f"{path} — не картинка PNG, JPEG или WebP (фото HEIC с iPhone сохраните как JPEG).")
    return data, mime


# --- промпт ---


def build_prompt(kitchen_type: str | None, wishes: str | None, roles: list[str]) -> str:
    lines = ["Photorealistic interior visualization of a custom-made kitchen.", "", "Input images:"]
    for n, role in enumerate(roles, 1):
        if role == "room":
            text = (
                "the client's empty room (base image). Keep its walls, windows, doors, floor, "
                "ceiling, lighting and camera perspective exactly."
            )
        elif role == "sample":
            text = "sample kitchen from the cabinet maker's work: follow its layout and style."
        else:
            what = role.split(":", 1)[1] if ":" in role else ""
            text = f"material sample{f' for: {what}' if what else ''}. Reproduce its colour and texture."
        lines.append(f"- Image {n}: {text}")
    lines.append("")
    lines.append("Place the new kitchen into the room from Image 1.")
    if kitchen_type:
        lines.append(f"Kitchen layout type (Russian): {kitchen_type}.")
    if wishes:
        lines.append(f"Cabinet maker's wishes (Russian): {wishes}")
    lines += [
        "Cabinets must fit the walls realistically; do not move windows or doors.",
        "No text, logos or people in the image.",
    ]
    return "\n".join(lines)


# --- сервисы ---


def multipart(fields: list[tuple[str, str]], files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    for name, value in fields:
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        out.write(value.encode() + b"\r\n")
    for name, filename, data, mime in files:
        out.write(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n".encode()
        )
        out.write(data + b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def openai_call(send: Send, key: str, model: str, prompt: str, photos, opts: dict) -> list[bytes]:
    fields = [("model", model), ("prompt", prompt), ("n", str(opts["n"])), ("size", opts["size"])]
    fields.append(("quality", opts["quality"]))
    files = [("image[]", f"image-{i}{ext(mime)}", data, mime) for i, (data, mime) in enumerate(photos, 1)]
    body, content_type = multipart(fields, files)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": content_type}
    answer = load_json(send(OPENAI_URL, headers, body))
    images = [base64.b64decode(d["b64_json"]) for d in answer.get("data") or [] if d.get("b64_json")]
    if not images:
        raise UserError(f"Сервис ответил без картинки: {json.dumps(answer, ensure_ascii=False)[:500]}")
    return images


def openrouter_call(send: Send, key: str, model: str, prompt: str, photos, opts: dict) -> list[bytes]:
    content: list[dict] = [{"type": "text", "text": prompt}]
    for data, mime in photos:
        uri = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        content.append({"type": "image_url", "image_url": {"url": uri}})
    images: list[bytes] = []
    for _ in range(opts["n"]):
        payload = {"model": model, "messages": [{"role": "user", "content": content}]}
        payload["modalities"] = ["image", "text"]
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        answer = load_json(send(OPENROUTER_URL, headers, json.dumps(payload).encode()))
        message = ((answer.get("choices") or [{}])[0]).get("message") or {}
        found = [
            decode_data_uri(img["image_url"]["url"])
            for img in message.get("images") or []
            if str(img.get("image_url", {}).get("url", "")).startswith("data:")
        ]
        if not found:
            said = message.get("content") or json.dumps(answer, ensure_ascii=False)
            raise UserError(f"Сервис ответил без картинки: {str(said)[:500]}")
        images += found
    return images


def decode_data_uri(uri: str) -> bytes:
    return base64.b64decode(uri.split(",", 1)[1])


def load_json(raw: bytes) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as err:
        raise UserError(f"Сервис ответил не JSON: {raw[:300]!r}") from err


def ext(mime: str) -> str:
    return {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime]


def http_send(url: str, headers: dict, body: bytes) -> bytes:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as err:
        raise ServiceError(err.code, err.read().decode("utf-8", "replace")) from err
    except urllib.error.URLError as err:
        raise UserError(f"Нет связи с сервисом {url}: {err.reason}") from err


PROVIDERS = {"openai": openai_call, "openrouter": openrouter_call}


# --- водяной знак ---


def watermark(image: bytes, text: str, mock: bool, logo: Path | None = None) -> bytes:
    base = Image.open(io.BytesIO(image)).convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    size = max(12, round(min(base.size) * 0.03))
    font = ImageFont.truetype(str(FONT), size)
    lines = [text, MOCK_LINE] if mock else [text]
    margin = size
    y = base.height - margin
    for line in reversed(lines):
        left, top, right, bottom = draw.textbbox((0, 0), line, font=font, stroke_width=2)
        y -= bottom - top
        x = base.width - margin - (right - left)
        fill = (255, 60, 60, 210) if line == MOCK_LINE else (255, 255, 255, 170)
        draw.text((x - left, y - top), line, font=font, fill=fill, stroke_width=2, stroke_fill=(0, 0, 0, 120))
        y -= size // 3
    if logo is not None:
        mark = Image.open(logo).convert("RGBA")
        mark.thumbnail((size * 4, size * 4))
        layer.alpha_composite(mark, (base.width - margin - mark.width, max(0, y - mark.height)))
    out = io.BytesIO()
    Image.alpha_composite(base, layer).convert("RGB").save(out, "PNG")
    return out.getvalue()


# --- команды ---


def parse_material(value: str) -> tuple[str, Path]:
    role, sep, path = value.partition("=")
    if sep and role and not Path(value).exists():
        return role.strip(), Path(path)
    return "", Path(value)


def generate(opts: argparse.Namespace, env: Mapping[str, str], send: Send) -> dict:
    provider = (opts.provider or env.get("DESIGN_PROVIDER") or "openai").lower()
    if provider not in PROVIDERS:
        raise UserError(f"Неизвестный сервис «{provider}»: openai или openrouter.")
    key = env.get(KEYS[provider])
    if not key:
        raise UserError(f"Нет ключа {KEYS[provider]}: добавьте его в .env или в окружение.")
    mark = opts.watermark or env.get("DESIGN_WATERMARK")
    if not mark:
        raise UserError("Нет водяного знака мастера: задайте DESIGN_WATERMARK в .env или --watermark.")
    model = opts.model or env.get("DESIGN_MODEL") or DEFAULT_MODELS[provider]
    logo = Path(env["DESIGN_WATERMARK_LOGO"]) if env.get("DESIGN_WATERMARK_LOGO") else None

    inputs: list[tuple[str, Path]] = [("room", Path(opts.room))]
    inputs += [("sample", Path(p)) for p in opts.sample]
    kitchen_type, mock = opts.type, False
    if opts.sample_type:
        folder = find_catalog(env, opts.catalog)
        if folder is None:
            raise UserError("Каталог образцов не найден: задайте --catalog или DESIGN_CATALOG.")
        catalog = load_catalog(folder)
        chosen = next((t for t in catalog["types"] if t["id"] == opts.sample_type), None)
        if chosen is None:
            known = ", ".join(t["id"] for t in catalog["types"]) or "пусто"
            raise UserError(f"В каталоге нет типа «{opts.sample_type}». Есть: {known}.")
        if not chosen["фото"]:
            raise UserError(f"У типа «{opts.sample_type}» в каталоге нет фото: пришлите фото образца.")
        inputs += [("sample", Path(p)) for p in chosen["фото"][:1]]
        kitchen_type = kitchen_type or chosen["название"]
        mock = catalog["mock"]
    for value in opts.material:
        role, path = parse_material(value)
        inputs.append((f"material:{role}" if role else "material", path))

    photos = [read_photo(path) for _, path in inputs]
    roles = [role for role, _ in inputs]
    prompt = build_prompt(kitchen_type, opts.wishes, roles)
    options = {"n": opts.n, "size": opts.size, "quality": opts.quality or env.get("DESIGN_QUALITY", "medium")}
    try:
        images = PROVIDERS[provider](send, key, model, prompt, photos, options)
    except ServiceError as err:
        raise UserError(f"Сервис {provider} вернул ошибку {err.status}: {err.text[:500]}") from err

    out = Path(opts.out)
    (out / "inputs").mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    copies = []
    for n, ((role, path), (_, mime)) in enumerate(zip(inputs, photos, strict=True), 1):
        copy = out / "inputs" / f"{stamp}-{n}-{role.replace(':', '-')}{ext(mime)}"
        shutil.copyfile(path, copy)
        copies.append({"role": role, "path": str(copy.resolve()), "source": str(path)})
    text = f"{mark}\n{MOCK_LINE}" if mock else mark
    saved = []
    for n, image in enumerate(images, 1):
        target = out / f"design-{stamp}-{n}.png"
        target.write_bytes(watermark(image, mark, mock, logo))
        saved.append(str(target.resolve()))
    result = {
        "provider": provider,
        "model": model,
        "prompt": prompt,
        "kitchen_type": kitchen_type,
        "wishes": opts.wishes,
        "inputs": copies,
        "images": saved,
        "watermark": text,
        "mock": mock,
        "created": datetime.now().isoformat(timespec="seconds"),
        "json": str((out / f"design-{stamp}.json").resolve()),
    }
    Path(result["json"]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Картинка примерного дизайна кухни.")
    sub = p.add_subparsers(dest="command", required=True)
    cat = sub.add_parser("catalog", help="типы кухонь из каталога образцов")
    cat.add_argument("--catalog")
    gen = sub.add_parser("generate", help="сделать картинку")
    gen.add_argument("--room", required=True, help="фото комнаты клиента")
    gen.add_argument("--sample", action="append", default=[], help="фото образца кухни")
    gen.add_argument("--sample-type", help="id типа из каталога (берётся его первое фото)")
    gen.add_argument("--material", action="append", default=[], help="[РОЛЬ=]ФОТО материала")
    gen.add_argument("--type", help="тип кухни словами")
    gen.add_argument("--wishes", help="пожелания мастера")
    gen.add_argument("--out", required=True, help="папка для результата")
    gen.add_argument("--provider", choices=sorted(PROVIDERS))
    gen.add_argument("--model")
    gen.add_argument("--n", type=int, default=1)
    gen.add_argument("--size", default="auto")
    gen.add_argument("--quality")
    gen.add_argument("--watermark")
    gen.add_argument("--catalog")
    return p


def main(
    argv: list[str] | None = None, env: Mapping[str, str] | None = None, send: Send | None = None
) -> int:
    opts = parser().parse_args(argv)
    settings = load_env(Path.cwd(), os.environ) if env is None else dict(env)
    try:
        if opts.command == "catalog":
            folder = find_catalog(settings, opts.catalog)
            if folder is None:
                raise UserError("Каталог образцов не найден: задайте --catalog или DESIGN_CATALOG.")
            result = load_catalog(folder)
        else:
            if opts.n < 1:
                raise UserError("--n должно быть не меньше 1.")
            result = generate(opts, settings, send or http_send)
    except UserError as err:
        print(f"Ошибка: {err}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
