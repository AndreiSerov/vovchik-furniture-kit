#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=10.1", "pyyaml>=6"]
# ///
"""ИИ-визуализация кухни: фото комнаты + образец + фото материалов → сервис генерации картинок.

Запуск (uv сам поставит Pillow и PyYAML):
    uv run visualize.py catalog [--catalog ПАПКА]
    uv run visualize.py generate --room ФОТО [--shape ФОРМА] [--sample ФОТО]...
        [--material [РОЛЬ=]ФОТО]... [--wishes ТЕКСТ] --out ПАПКА
        [--provider openai|openrouter] [--model ИМЯ] [--n 1] [--size auto] [--quality medium]
        [--watermark ТЕКСТ] [--catalog ПАПКА]

ФОРМА — форма кухни из брифа: straight, corner, u_shape, island. Без --sample образец берётся
из каталога образцов: первое фото этой формы.

Настройки — переменные окружения или `.env` (ищется от текущей папки вверх, ближайший; окружение важнее):
    OPENAI_API_KEY, OPENROUTER_API_KEY — ключи сервисов
    VISUALIZATION_PROVIDER (openai), VISUALIZATION_MODEL, VISUALIZATION_QUALITY (medium)
    VISUALIZATION_WATERMARK — водяной знак мастера, имя и телефон (обязателен)
    VISUALIZATION_WATERMARK_LOGO — PNG-логотип рядом со знаком
    VISUALIZATION_CATALOG — папка каталога образцов (иначе workshop/sample-catalog/)

stdout — JSON с описанием результата; ошибки — stderr по-русски, код возврата 2.
Скрипт не зависит от движка `kitchen` и работает в любой папке.
"""

from __future__ import annotations

import argparse
import base64
import http.client
import io
import json
import os
import re
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
SHAPES = {"straight": "прямая", "corner": "угловая", "u_shape": "П-образная", "island": "только остров"}
DEFAULT_MODELS = {"openai": "gpt-image-1", "openrouter": "google/gemini-2.5-flash-image"}
KEYS = {"openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
OPENAI_URL = "https://api.openai.com/v1/images/edits"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT = 300
REMOVE_OLD = (
    "Remove any existing kitchen furniture and appliances from the room in Image 1 "
    "and place the new kitchen there."
)

Send = Callable[[str, dict, bytes], bytes]


class UserError(Exception):
    """Ошибка, которую видит мастер: текст по-русски, код возврата 2."""


class ServiceError(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(f"{status}: {text}")
        self.status = status
        self.text = text


class RussianParser(argparse.ArgumentParser):
    def error(self, message: str):
        raise UserError(f"неверные аргументы: {message}")


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
        end = value.find(value[0], 1) if value[:1] in ("'", '"') else -1
        value = value[1:end] if end > 0 else re.sub(r"\s+#.*$", "", value)
        values[key.strip()] = value
    return values


def load_env(start: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """Ближайший к `start` `.env` вверх по дереву, поверх — окружение."""
    merged: dict[str, str] = {}
    for folder in [start, *start.parents]:
        candidate = folder / ".env"
        if candidate.is_file():
            try:
                merged = parse_dotenv(candidate.read_text(encoding="utf-8"))
            except UnicodeDecodeError as err:
                raise UserError(f"{candidate}: файл не в UTF-8, пересохраните его в UTF-8.") from err
            except OSError as err:
                raise UserError(f"{candidate}: файл не читается: {err.strerror}") from err
            break
    merged.update(environ)
    return merged


# --- каталог образцов ---


def find_catalog(env: Mapping[str, str], flag: str | None) -> Path | None:
    if flag:
        return Path(flag)
    if env.get("VISUALIZATION_CATALOG"):
        return Path(env["VISUALIZATION_CATALOG"])
    home = env.get("KITCHEN_HOME")
    starts = [Path(home)] if home else [Path.cwd(), *Path.cwd().parents]
    for folder in starts:
        candidate = folder / "workshop" / "sample-catalog"
        if (candidate / "catalog.yaml").is_file():
            return candidate
    return None


def load_catalog(folder: Path) -> dict:
    path = folder / "catalog.yaml"
    if not path.is_file():
        raise UserError(f"Каталог образцов: нет файла {path}.")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except UnicodeDecodeError as err:
        raise UserError(f"{path}: файл не в UTF-8, пересохраните его в UTF-8.") from err
    except yaml.YAMLError as err:
        raise UserError(f"{path}: YAML не разбирается: {err}") from err
    except OSError as err:
        raise UserError(f"{path}: файл не читается: {err.strerror}") from err
    if not isinstance(data, dict) or not isinstance(data.get("shapes", []), list):
        raise UserError(f"{path}: нужен словарь с mock и списком shapes.")
    shapes, missing = [], []
    for n, item in enumerate(data.get("shapes") or [], 1):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] not in SHAPES:
            raise UserError(f"{path}: форма {n}: id должен быть одним из {', '.join(SHAPES)}.")
        names = item.get("фото") or []
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise UserError(f"{path}: форма {item['id']}: фото — список имён файлов в квадратных скобках.")
        photos = []
        for name in names:
            try:
                photo = (folder / name).resolve()
                found = photo.is_file()
            except (OSError, ValueError) as err:
                raise UserError(f"{path}: форма {item['id']}: неверное имя фото «{name}».") from err
            (photos if found else missing).append(str(photo))
        shapes.append(
            {
                "id": item["id"],
                "название": item.get("название", SHAPES[item["id"]]),
                "описание": item.get("описание", ""),
                "фото": photos,
            }
        )
    result = {"mock": bool(data.get("mock", False)), "shapes": shapes, "missing": missing}
    if result["mock"]:
        result["warning"] = MOCK_LINE
    return result


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
    try:
        data = path.read_bytes()
    except OSError as err:
        raise UserError(f"Фото не читается: {path}: {err.strerror}") from err
    mime = mime_of(data)
    if mime is None:
        raise UserError(f"{path} — не картинка PNG, JPEG или WebP (фото HEIC с iPhone сохраните как JPEG).")
    if decode_image(data) is None:
        raise UserError(f"{path} — картинка повреждена, пришлите фото ещё раз.")
    return data, mime


def decode_image(data: bytes) -> Image.Image | None:
    """Картинка целиком в RGBA или None, если данные не открываются.

    Pillow на испорченных данных бросает разные исключения (OSError, SyntaxError, ValueError,
    struct.error, EOFError…), поэтому ловится любое: любое из них значит «картинка повреждена».
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.convert("RGBA")
    except Exception:  # noqa: BLE001 — любое исключение Pillow на разборе значит «картинка повреждена»
        return None


def safe_name(text: str) -> str:
    """Роль в имени файла: буквы, цифры и дефис."""
    return re.sub(r"[^\w-]+", "-", text).strip("-") or "x"


# --- промпт ---


def build_prompt(shape: str | None, wishes: str | None, roles: list[str]) -> str:
    lines = ["Photorealistic interior visualization of a custom-made kitchen.", "", "Input images:"]
    for n, role in enumerate(roles, 1):
        if role == "room":
            text = (
                "the client's room (base image). Keep its walls, windows, doors, floor, ceiling, pipes, "
                "lighting and camera perspective exactly."
            )
        elif role == "sample":
            text = "sample kitchen from the cabinet maker's work: follow its layout, proportions and style."
        else:
            what = role.split(":", 1)[1] if ":" in role else ""
            text = f"material sample{f' for: {what}' if what else ''}. Reproduce its colour and texture."
        lines.append(f"- Image {n}: {text}")
    lines += ["", REMOVE_OLD]
    if shape:
        lines.append(f"Kitchen shape: {shape} (Russian: {SHAPES[shape]}).")
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
    data = answer.get("data") or []
    images = [b64(d["b64_json"]) for d in data if isinstance(d, dict) and d.get("b64_json")]
    if not images:
        raise UserError(f"Сервис ответил без картинки: {json.dumps(answer, ensure_ascii=False)[:500]}")
    return images


def openrouter_call(send: Send, key: str, model: str, prompt: str, photos, opts: dict) -> list[bytes]:
    """OpenRouter отдаёт одну картинку на запрос, поэтому `n` картинок — `n` запросов."""
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
            b64(img["image_url"]["url"].split(",", 1)[1])
            for img in message.get("images") or []
            if str(img.get("image_url", {}).get("url", "")).startswith("data:")
        ]
        if not found:
            said = message.get("content") or json.dumps(answer, ensure_ascii=False)
            raise UserError(f"Сервис ответил без картинки: {str(said)[:500]}")
        images += found
    return images


def b64(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as err:
        raise UserError("Сервис ответил без картинки: данные картинки повреждены.") from err


def load_json(raw: bytes) -> dict:
    try:
        answer = json.loads(raw)
    except json.JSONDecodeError as err:
        raise UserError(f"Сервис ответил не JSON: {raw[:300]!r}") from err
    if not isinstance(answer, dict):
        raise UserError(f"Сервис ответил без картинки: {str(answer)[:300]}")
    return answer


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
    except TimeoutError as err:
        raise UserError(f"Сервис {url} не ответил за {TIMEOUT} с, попробуйте ещё раз.") from err
    except (OSError, http.client.HTTPException) as err:
        raise UserError(f"Связь с сервисом {url} оборвалась: {err!r}") from err


PROVIDERS = {"openai": openai_call, "openrouter": openrouter_call}


# --- водяной знак ---


def watermark(image: bytes, text: str, mock: bool, logo: Image.Image | None = None) -> bytes:
    base = decode_image(image)
    if base is None:
        raise UserError("Сервис ответил без картинки: данные не открываются как картинка.")
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
        mark = logo.copy()
        mark.thumbnail((size * 4, size * 4))
        layer.alpha_composite(mark, (base.width - margin - mark.width, max(0, y - mark.height)))
    out = io.BytesIO()
    Image.alpha_composite(base, layer).convert("RGB").save(out, "PNG")
    return out.getvalue()


def load_logo(path: str) -> Image.Image:
    try:
        data = Path(path).read_bytes()
    except OSError as err:
        raise UserError(f"Логотип VISUALIZATION_WATERMARK_LOGO не открывается: {path}") from err
    logo = decode_image(data)
    if logo is None:
        raise UserError(f"Логотип VISUALIZATION_WATERMARK_LOGO не открывается: {path}")
    return logo


# --- команды ---


def parse_material(value: str) -> tuple[str, Path]:
    role, sep, path = value.partition("=")
    if sep and role and not Path(value).exists():
        return role.strip(), Path(path)
    return "", Path(value)


def unique_stamp(out: Path) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate, n = stamp, 2
    while (out / f"visualization-{candidate}.json").exists():
        candidate, n = f"{stamp}-{n}", n + 1
    return candidate


def generate(opts: argparse.Namespace, env: Mapping[str, str], send: Send) -> dict:
    if not opts.room:
        raise UserError("Нет фото комнаты: передайте --room <фото>.")
    if opts.n < 1:
        raise UserError("--n должно быть не меньше 1.")
    if opts.shape and opts.shape not in SHAPES:
        raise UserError(f"Неизвестная форма кухни «{opts.shape}»: {', '.join(SHAPES)}.")
    provider = (opts.provider or env.get("VISUALIZATION_PROVIDER") or "openai").lower()
    if provider not in PROVIDERS:
        raise UserError(f"Неизвестный сервис «{provider}»: openai или openrouter.")
    key = env.get(KEYS[provider])
    if not key:
        raise UserError(f"Нет ключа {KEYS[provider]}: добавьте его в .env или в окружение.")
    mark = (opts.watermark or env.get("VISUALIZATION_WATERMARK") or "").strip()
    if not mark:
        raise UserError(
            "Нет водяного знака мастера: задайте VISUALIZATION_WATERMARK (имя и телефон) в .env или --watermark."
        )
    model = opts.model or env.get("VISUALIZATION_MODEL") or DEFAULT_MODELS[provider]
    logo_path = env.get("VISUALIZATION_WATERMARK_LOGO")
    logo = load_logo(logo_path) if logo_path else None

    inputs: list[tuple[str, Path]] = [("room", Path(opts.room))]
    inputs += [("sample", Path(p)) for p in opts.sample]
    mock = False
    if not opts.sample and opts.shape:
        folder = find_catalog(env, opts.catalog)
        if folder is None:
            raise UserError("Каталог образцов не найден: задайте --catalog или VISUALIZATION_CATALOG.")
        catalog = load_catalog(folder)
        chosen = next((s for s in catalog["shapes"] if s["id"] == opts.shape), None)
        if chosen is None or not chosen["фото"]:
            raise UserError(
                f"У формы «{SHAPES[opts.shape]}» в каталоге нет фото: пришлите фото образца (--sample)."
            )
        inputs.append(("sample", Path(chosen["фото"][0])))
        mock = catalog["mock"]
    for value in opts.material:
        role, path = parse_material(value)
        inputs.append((f"material:{role}" if role else "material", path))

    photos = [read_photo(path) for _, path in inputs]
    prompt = build_prompt(opts.shape, opts.wishes, [role for role, _ in inputs])
    quality = opts.quality or env.get("VISUALIZATION_QUALITY") or "medium"
    options = {"n": opts.n, "size": opts.size, "quality": quality}
    out = Path(opts.out)
    try:
        (out / "inputs").mkdir(parents=True, exist_ok=True)
    except OSError as err:
        raise UserError(f"Не создать папку результата {out}: {err}") from err
    try:
        images = PROVIDERS[provider](send, key, model, prompt, photos, options)
    except ServiceError as err:
        raise UserError(f"Сервис {provider} вернул ошибку {err.status}: {err.text[:500]}") from err
    except (AttributeError, IndexError, KeyError, TypeError) as err:
        # Ответ не той формы, что у сервиса в документации: после платного запроса — понятная ошибка, а не трейсбек.
        raise UserError(
            f"Сервис {provider} ответил без картинки: неожиданный формат ответа ({err!r})."
        ) from err

    marked = [watermark(image, mark, mock, logo) for image in images]
    stamp = unique_stamp(out)
    copies = []
    for n, ((role, _), (data, mime)) in enumerate(zip(inputs, photos, strict=True), 1):
        copy = out / "inputs" / f"{stamp}-{n}-{safe_name(role)}{ext(mime)}"
        copy.write_bytes(data)
        copies.append({"role": role, "path": str(copy.resolve())})
    saved = []
    for n, image in enumerate(marked, 1):
        target = out / f"visualization-{stamp}-{n}.png"
        target.write_bytes(image)
        saved.append(str(target.resolve()))
    result = {
        "provider": provider,
        "model": model,
        "prompt": prompt,
        "shape": opts.shape,
        "wishes": opts.wishes,
        "inputs": copies,
        "images": saved,
        "watermark": f"{mark}\n{MOCK_LINE}" if mock else mark,
        "mock": mock,
        "created": datetime.now().isoformat(timespec="seconds"),
        "json": str((out / f"visualization-{stamp}.json").resolve()),
    }
    if mock:
        result["warning"] = MOCK_LINE
    Path(result["json"]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def parser() -> argparse.ArgumentParser:
    p = RussianParser(prog="visualize.py", description="ИИ-визуализация кухни.")
    sub = p.add_subparsers(dest="command", required=True, parser_class=RussianParser)
    cat = sub.add_parser("catalog", help="формы кухни и фото образцов из каталога")
    cat.add_argument("--catalog")
    gen = sub.add_parser("generate", help="сделать ИИ-визуализацию")
    gen.add_argument("--room", help="фото комнаты клиента (обязательно)")
    gen.add_argument("--shape", help=f"форма кухни: {', '.join(SHAPES)}")
    gen.add_argument("--sample", action="append", default=[], help="фото образца кухни")
    gen.add_argument("--material", action="append", default=[], help="[РОЛЬ=]ФОТО материала")
    gen.add_argument("--wishes", help="пожелания мастера о виде и материалах")
    gen.add_argument("--out", help="папка для результата (обязательно)")
    gen.add_argument("--provider")
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
    try:
        opts = parser().parse_args(argv)
        settings = load_env(Path.cwd(), os.environ) if env is None else dict(env)
        if opts.command == "catalog":
            folder = find_catalog(settings, opts.catalog)
            if folder is None:
                raise UserError("Каталог образцов не найден: задайте --catalog или VISUALIZATION_CATALOG.")
            result = load_catalog(folder)
        else:
            if not opts.out:
                raise UserError("Нет папки результата: передайте --out <папка>.")
            result = generate(opts, settings, send or http_send)
    except UserError as err:
        print(f"Ошибка: {err}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
