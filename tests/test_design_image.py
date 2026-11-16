"""Скрипт скилла kitchen-design (спека этапа 7, тикет 56). Сеть подменена функцией `send`."""

import base64
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "kitchen-design" / "scripts" / "design_image.py"

spec = importlib.util.spec_from_file_location("design_image", SCRIPT)
di = importlib.util.module_from_spec(spec)
sys.modules["design_image"] = di
spec.loader.exec_module(di)


def png(path: Path, color=(120, 120, 120), size=(64, 48)) -> Path:
    Image.new("RGB", size, color).save(path, "PNG")
    return path


def jpeg(path: Path, color=(200, 100, 50)) -> Path:
    Image.new("RGB", (40, 40), color).save(path, "JPEG")
    return path


def result_png(size=(400, 300)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (90, 90, 90)).save(buf, "PNG")
    return buf.getvalue()


class FakeSend:
    """Записывает запросы и отвечает заданным телом."""

    def __init__(self, body: bytes):
        self.body = body
        self.calls: list[tuple[str, dict, bytes]] = []

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, body))
        return self.body


def openai_answer(n=1) -> bytes:
    b64 = base64.b64encode(result_png()).decode()
    return json.dumps({"data": [{"b64_json": b64} for _ in range(n)]}).encode()


def openrouter_answer() -> bytes:
    url = "data:image/png;base64," + base64.b64encode(result_png()).decode()
    return json.dumps(
        {
            "choices": [
                {"message": {"content": "", "images": [{"type": "image_url", "image_url": {"url": url}}]}}
            ]
        }
    ).encode()


@pytest.fixture
def photos(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    return {
        "room": png(d / "room.png", (10, 10, 10)),
        "sample": jpeg(d / "sample.jpg"),
        "front": png(d / "front.png", (250, 250, 250)),
        "top": png(d / "top.png", (150, 100, 60)),
    }


ENV = {"OPENAI_API_KEY": "sk-test", "OPENROUTER_API_KEY": "or-test", "DESIGN_WATERMARK": "Мастер Вовчик"}


def args(photos, out, *extra):
    return [
        "generate",
        "--room",
        str(photos["room"]),
        "--sample",
        str(photos["sample"]),
        "--material",
        f"фасады={photos['front']}",
        "--material",
        f"столешница={photos['top']}",
        "--type",
        "угловая",
        "--wishes",
        "фасады без ручек",
        "--out",
        str(out),
        *extra,
    ]


# 1. Запрос OpenAI


def test_openai_request_is_multipart_with_all_images_room_first(photos, tmp_path, capsys):
    send = FakeSend(openai_answer())
    assert di.main(args(photos, tmp_path / "out"), env=ENV, send=send) == 0
    (url, headers, body) = send.calls[0]
    assert url == "https://api.openai.com/v1/images/edits"
    assert headers["Authorization"] == "Bearer sk-test"
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert body.count(b'name="image[]"') == 4
    room_at = body.index(photos["room"].read_bytes())
    assert room_at < body.index(photos["sample"].read_bytes())
    assert b'name="model"\r\n\r\ngpt-image-1\r\n' in body
    assert b'name="prompt"' in body
    assert "фасады без ручек".encode() in body


def test_model_from_flag_beats_env(photos, tmp_path):
    send = FakeSend(openai_answer())
    env = {**ENV, "DESIGN_MODEL": "gpt-image-1-mini"}
    di.main(args(photos, tmp_path / "o", "--model", "gpt-image-1.5"), env=env, send=send)
    assert b"gpt-image-1.5\r\n" in send.calls[0][2]
    send2 = FakeSend(openai_answer())
    di.main(args(photos, tmp_path / "o2"), env=env, send=send2)
    assert b"gpt-image-1-mini\r\n" in send2.calls[0][2]


# 2. Запрос OpenRouter


def test_openrouter_request_json_with_data_uris_in_order(photos, tmp_path):
    send = FakeSend(openrouter_answer())
    env = {**ENV, "DESIGN_PROVIDER": "openrouter"}
    assert di.main(args(photos, tmp_path / "out"), env=env, send=send) == 0
    url, headers, body = send.calls[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["Authorization"] == "Bearer or-test"
    payload = json.loads(body)
    assert payload["model"] == "google/gemini-2.5-flash-image"
    assert payload["modalities"] == ["image", "text"]
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "text"
    uris = [c["image_url"]["url"] for c in content[1:]]
    assert len(uris) == 4
    assert uris[0] == "data:image/png;base64," + base64.b64encode(photos["room"].read_bytes()).decode()
    assert uris[1].startswith("data:image/jpeg;base64,")


# 3. Промпт


def test_prompt_names_every_role_type_and_wishes():
    prompt = di.build_prompt(
        "угловая",
        "фасады без ручек",
        ["room", "sample", "material:фасады", "material:столешница"],
    )
    assert "Image 1" in prompt and "room" in prompt.lower()
    assert "Image 2" in prompt and "sample" in prompt.lower()
    assert "Image 3" in prompt and "фасады" in prompt
    assert "Image 4" in prompt and "столешница" in prompt
    assert "угловая" in prompt
    assert "фасады без ручек" in prompt


def test_prompt_without_sample_and_materials():
    prompt = di.build_prompt(None, None, ["room"])
    assert "Image 1" in prompt
    assert "Image 2" not in prompt


# 4. Ответы


def test_openai_returns_n_images(photos, tmp_path, capsys):
    send = FakeSend(openai_answer(n=2))
    di.main(args(photos, tmp_path / "out", "--n", "2"), env=ENV, send=send)
    out = json.loads(capsys.readouterr().out)
    assert len(out["images"]) == 2
    assert all(Path(p).name.endswith(f"-{i}.png") for i, p in enumerate(out["images"], 1))
    assert b'name="n"\r\n\r\n2\r\n' in send.calls[0][2]


def test_answer_without_image_is_error(photos, tmp_path, capsys):
    send = FakeSend(json.dumps({"choices": [{"message": {"content": "не могу"}}]}).encode())
    env = {**ENV, "DESIGN_PROVIDER": "openrouter"}
    assert di.main(args(photos, tmp_path / "out"), env=env, send=send) == 2
    err = capsys.readouterr().err
    assert "без картинки" in err and "не могу" in err


def test_http_error_shows_code_and_service_text(photos, tmp_path, capsys):
    def send(url, headers, body):
        raise di.ServiceError(401, '{"error": {"message": "Incorrect API key"}}')

    assert di.main(args(photos, tmp_path / "out"), env=ENV, send=send) == 2
    err = capsys.readouterr().err
    assert "401" in err and "Incorrect API key" in err


# 5. Водяной знак


def test_watermark_changes_bottom_right_only():
    raw = result_png((400, 300))
    marked = Image.open(io.BytesIO(di.watermark(raw, "Мастер Вовчик", mock=False))).convert("RGB")
    plain = Image.open(io.BytesIO(raw)).convert("RGB")
    assert marked.size == plain.size
    corner = (300, 220, 400, 300)
    assert marked.crop(corner).tobytes() != plain.crop(corner).tobytes()
    assert marked.crop((0, 0, 100, 100)).tobytes() == plain.crop((0, 0, 100, 100)).tobytes()


def test_mock_watermark_covers_more_than_plain():
    raw = result_png((400, 300))
    base = Image.open(io.BytesIO(raw)).convert("RGB")

    def changed(img_bytes):
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        same = ImageChops.difference(img, base).convert("L").histogram()[0]
        return img.width * img.height - same

    assert changed(di.watermark(raw, "М", mock=True)) > changed(di.watermark(raw, "М", mock=False))


# 6. Ошибки до запроса


@pytest.mark.parametrize(
    ("drop", "provider", "needle"),
    [
        ("OPENAI_API_KEY", "openai", "OPENAI_API_KEY"),
        ("OPENROUTER_API_KEY", "openrouter", "OPENROUTER_API_KEY"),
        ("DESIGN_WATERMARK", "openai", "водян"),
    ],
)
def test_missing_setting_fails_before_request(photos, tmp_path, capsys, drop, provider, needle):
    env = {k: v for k, v in ENV.items() if k != drop} | {"DESIGN_PROVIDER": provider}
    send = FakeSend(openai_answer())
    assert di.main(args(photos, tmp_path / "out"), env=env, send=send) == 2
    assert send.calls == []
    assert needle in capsys.readouterr().err


def test_not_an_image_is_error(photos, tmp_path, capsys):
    bad = tmp_path / "room.heic"
    bad.write_bytes(b"not an image at all")
    photos = {**photos, "room": bad}
    send = FakeSend(openai_answer())
    assert di.main(args(photos, tmp_path / "out"), env=ENV, send=send) == 2
    assert send.calls == []
    assert "не картинка" in capsys.readouterr().err


def test_missing_file_is_error(photos, tmp_path, capsys):
    photos = {**photos, "room": tmp_path / "nope.png"}
    assert di.main(args(photos, tmp_path / "out"), env=ENV, send=FakeSend(b"")) == 2
    assert "не найден" in capsys.readouterr().err


# 7. .env


def test_dotenv_found_upward_and_environment_wins(tmp_path):
    (tmp_path / ".env").write_text(
        '# комментарий\nOPENAI_API_KEY="from-file"\nexport DESIGN_WATERMARK=Файл\n\n', encoding="utf-8"
    )
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    merged = di.load_env(deep, {"DESIGN_WATERMARK": "Окружение"})
    assert merged["OPENAI_API_KEY"] == "from-file"
    assert merged["DESIGN_WATERMARK"] == "Окружение"


def test_no_dotenv_keeps_environment(tmp_path):
    assert di.load_env(tmp_path, {"X": "1"})["X"] == "1"


# 8. Каталог


def make_catalog(tmp_path, mock=True):
    cat = tmp_path / "catalog"
    cat.mkdir()
    png(cat / "corner-1.png")
    (cat / "catalog.yaml").write_text(
        f"mock: {'true' if mock else 'false'}\n"
        "types:\n"
        "  - id: corner\n"
        "    название: Угловая\n"
        "    описание: Г-образная кухня\n"
        "    фото: [corner-1.png, corner-2.png]\n",
        encoding="utf-8",
    )
    return cat


def test_catalog_lists_types_with_absolute_photos_and_missing(tmp_path, capsys):
    cat = make_catalog(tmp_path)
    assert di.main(["catalog", "--catalog", str(cat)], env={}) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mock"] is True
    t = out["types"][0]
    assert t["id"] == "corner" and t["название"] == "Угловая"
    assert t["фото"] == [str(cat / "corner-1.png")]
    assert out["missing"] == [str(cat / "corner-2.png")]


def test_sample_type_from_mock_catalog_marks_mock(photos, tmp_path, capsys):
    cat = make_catalog(tmp_path)
    send = FakeSend(openai_answer())
    argv = ["generate", "--room", str(photos["room"]), "--sample-type", "corner"]
    argv += ["--catalog", str(cat), "--out", str(tmp_path / "out")]
    assert di.main(argv, env=ENV, send=send) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mock"] is True
    assert out["kitchen_type"] == "Угловая"
    assert "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА" in out["watermark"]
    assert [i["role"] for i in out["inputs"]] == ["room", "sample"]


def test_sample_type_from_real_catalog_is_not_mock(photos, tmp_path, capsys):
    cat = make_catalog(tmp_path, mock=False)
    argv = ["generate", "--room", str(photos["room"]), "--sample-type", "corner"]
    argv += ["--catalog", str(cat), "--out", str(tmp_path / "out")]
    assert di.main(argv, env=ENV, send=FakeSend(openai_answer())) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mock"] is False
    assert out["watermark"] == "Мастер Вовчик"


def test_unknown_sample_type_is_error(photos, tmp_path, capsys):
    cat = make_catalog(tmp_path)
    argv = ["generate", "--room", str(photos["room"]), "--sample-type", "island"]
    argv += ["--catalog", str(cat), "--out", str(tmp_path / "out")]
    assert di.main(argv, env=ENV, send=FakeSend(b"")) == 2
    assert "corner" in capsys.readouterr().err


def test_workshop_catalog_is_valid():
    cat = Path(__file__).resolve().parents[1] / "workshop" / "design-catalog"
    data = di.load_catalog(cat)
    assert data["mock"] is True
    assert {t["id"] for t in data["types"]} >= {"straight", "corner", "u_shaped", "island"}


# 9. Сохранение


def test_saves_png_json_and_input_copies(photos, tmp_path, capsys):
    out_dir = tmp_path / "orders" / "2026-050-ivanov" / "design"
    assert di.main(args(photos, out_dir), env=ENV, send=FakeSend(openai_answer())) == 0
    out = json.loads(capsys.readouterr().out)
    saved = json.loads(Path(out["json"]).read_text(encoding="utf-8"))
    assert saved == out
    assert out["provider"] == "openai" and out["model"] == "gpt-image-1"
    assert out["mock"] is False and out["watermark"] == "Мастер Вовчик"
    assert out["kitchen_type"] == "угловая" and out["wishes"] == "фасады без ручек"
    assert [i["role"] for i in out["inputs"]] == ["room", "sample", "material:фасады", "material:столешница"]
    for item in out["inputs"]:
        copy = Path(item["path"])
        assert copy.parent == out_dir / "inputs" and copy.is_file()
    image = Path(out["images"][0])
    assert image.parent == out_dir and image.suffix == ".png"
    Image.open(image).verify()
    assert "sk-test" not in json.dumps(out)
