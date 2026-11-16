"""Скрипт скилла kitchen-visualization (спека этапа 7, тикет 56). Сеть подменена функцией `send`."""

import base64
import importlib.util
import io
import json
import socket
import sys
import threading
import urllib.error
from pathlib import Path

import pytest
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "kitchen-visualization" / "scripts" / "visualize.py"

spec = importlib.util.spec_from_file_location("visualize", SCRIPT)
vz = importlib.util.module_from_spec(spec)
sys.modules["visualize"] = vz
spec.loader.exec_module(vz)


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


WATERMARK = "Вовчик · +7 900 000-00-00"
ENV = {"OPENAI_API_KEY": "sk-test", "OPENROUTER_API_KEY": "or-test", "VISUALIZATION_WATERMARK": WATERMARK}


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
        "--shape",
        "corner",
        "--wishes",
        "фасады без ручек",
        "--out",
        str(out),
        *extra,
    ]


def make_catalog(tmp_path, mock=True, photo="corner-1.png"):
    cat = tmp_path / "catalog"
    cat.mkdir()
    png(cat / "corner-1.png")
    (cat / "catalog.yaml").write_text(
        f"mock: {'true' if mock else 'false'}\n"
        "shapes:\n"
        "  - id: corner\n"
        "    название: Угловая\n"
        "    описание: Г-образная кухня\n"
        f"    фото: [{photo}, corner-2.png]\n"
        "  - id: straight\n"
        "    фото: []\n",
        encoding="utf-8",
    )
    return cat


def catalog_argv(photos, cat, out, shape="corner"):
    return [
        "generate",
        "--room",
        str(photos["room"]),
        "--shape",
        shape,
        "--catalog",
        str(cat),
        "--out",
        str(out),
    ]


# 1. Запрос OpenAI


def test_openai_request_is_multipart_with_all_images_room_first(photos, tmp_path):
    send = FakeSend(openai_answer())
    assert vz.main(args(photos, tmp_path / "out"), env=ENV, send=send) == 0
    (url, headers, body) = send.calls[0]
    assert url == "https://api.openai.com/v1/images/edits"
    assert headers["Authorization"] == "Bearer sk-test"
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert body.count(b'name="image[]"') == 4
    assert body.index(photos["room"].read_bytes()) < body.index(photos["sample"].read_bytes())
    assert b'name="model"\r\n\r\ngpt-image-1\r\n' in body
    assert b'name="prompt"' in body
    assert "фасады без ручек".encode() in body


def test_openai_sends_size_and_quality_from_env(photos, tmp_path):
    send = FakeSend(openai_answer())
    vz.main(args(photos, tmp_path / "o"), env={**ENV, "VISUALIZATION_QUALITY": "high"}, send=send)
    body = send.calls[0][2]
    assert b'name="quality"\r\n\r\nhigh\r\n' in body
    assert b'name="size"\r\n\r\nauto\r\n' in body


def test_default_quality_is_medium(photos, tmp_path):
    send = FakeSend(openai_answer())
    vz.main(args(photos, tmp_path / "o"), env=ENV, send=send)
    assert b'name="quality"\r\n\r\nmedium\r\n' in send.calls[0][2]
    assert b'name="n"\r\n\r\n1\r\n' in send.calls[0][2]


def test_model_from_flag_beats_env(photos, tmp_path):
    send = FakeSend(openai_answer())
    env = {**ENV, "VISUALIZATION_MODEL": "gpt-image-1-mini"}
    vz.main(args(photos, tmp_path / "o", "--model", "gpt-image-1.5"), env=env, send=send)
    assert b"gpt-image-1.5\r\n" in send.calls[0][2]
    send2 = FakeSend(openai_answer())
    vz.main(args(photos, tmp_path / "o2"), env=env, send=send2)
    assert b"gpt-image-1-mini\r\n" in send2.calls[0][2]


# 2. Запрос OpenRouter


def test_openrouter_request_json_with_data_uris_in_order(photos, tmp_path):
    send = FakeSend(openrouter_answer())
    env = {**ENV, "VISUALIZATION_PROVIDER": "openrouter"}
    assert vz.main(args(photos, tmp_path / "out"), env=env, send=send) == 0
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


def test_openrouter_n_makes_n_requests(photos, tmp_path, capsys):
    send = FakeSend(openrouter_answer())
    env = {**ENV, "VISUALIZATION_PROVIDER": "openrouter"}
    assert vz.main(args(photos, tmp_path / "out", "--n", "2"), env=env, send=send) == 0
    assert len(send.calls) == 2
    assert len(json.loads(capsys.readouterr().out)["images"]) == 2


# 3. Промпт


def test_prompt_names_every_role_shape_and_wishes():
    prompt = vz.build_prompt(
        "corner", "фасады без ручек", ["room", "sample", "material:фасады", "material:столешница"]
    )
    assert "Image 1" in prompt and "room" in prompt.lower()
    assert "Image 2" in prompt and "sample" in prompt.lower()
    assert "Image 3" in prompt and "фасады" in prompt
    assert "Image 4" in prompt and "столешница" in prompt
    assert "угловая" in prompt and "corner" in prompt
    assert "фасады без ручек" in prompt


def test_prompt_removes_old_furniture_and_keeps_room():
    prompt = vz.build_prompt(None, None, ["room"])
    assert "Remove any existing kitchen furniture and appliances" in prompt
    for kept in ("walls", "windows", "doors", "floor", "pipes"):
        assert kept in prompt
    assert "Image 2" not in prompt


def test_unknown_shape_is_error(photos, tmp_path, capsys):
    send = FakeSend(openai_answer())
    argv = ["generate", "--room", str(photos["room"]), "--shape", "l_shape", "--out", str(tmp_path / "o")]
    assert vz.main(argv, env=ENV, send=send) == 2
    assert send.calls == []
    assert "u_shape" in capsys.readouterr().err


# 4. Ответы и сеть


def test_openai_returns_n_images(photos, tmp_path, capsys):
    send = FakeSend(openai_answer(n=2))
    vz.main(args(photos, tmp_path / "out", "--n", "2"), env=ENV, send=send)
    out = json.loads(capsys.readouterr().out)
    assert len(out["images"]) == 2
    assert all(Path(p).name.endswith(f"-{i}.png") for i, p in enumerate(out["images"], 1))
    assert b'name="n"\r\n\r\n2\r\n' in send.calls[0][2]


def test_openrouter_answer_without_image_is_error(photos, tmp_path, capsys):
    send = FakeSend(json.dumps({"choices": [{"message": {"content": "не могу"}}]}).encode())
    env = {**ENV, "VISUALIZATION_PROVIDER": "openrouter"}
    assert vz.main(args(photos, tmp_path / "out"), env=env, send=send) == 2
    err = capsys.readouterr().err
    assert "без картинки" in err and "не могу" in err


def test_openai_answer_without_image_is_error(photos, tmp_path, capsys):
    assert vz.main(args(photos, tmp_path / "out"), env=ENV, send=FakeSend(b'{"data": []}')) == 2
    assert "без картинки" in capsys.readouterr().err


def test_answer_that_is_not_an_image_is_error(photos, tmp_path, capsys):
    bad = json.dumps({"data": [{"b64_json": base64.b64encode(b"hello").decode()}]}).encode()
    assert vz.main(args(photos, tmp_path / "out"), env=ENV, send=FakeSend(bad)) == 2
    assert "без картинки" in capsys.readouterr().err


def test_service_error_shows_code_and_service_text(photos, tmp_path, capsys):
    def send(url, headers, body):
        raise vz.ServiceError(401, '{"error": {"message": "Incorrect API key"}}')

    assert vz.main(args(photos, tmp_path / "out"), env=ENV, send=send) == 2
    err = capsys.readouterr().err
    assert "401" in err and "Incorrect API key" in err


def test_http_send_turns_http_error_into_service_error(monkeypatch):
    def boom(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many", {}, io.BytesIO(b'{"error":"rate"}'))

    monkeypatch.setattr(vz.urllib.request, "urlopen", boom)
    with pytest.raises(vz.ServiceError) as err:
        vz.http_send("https://example.invalid", {}, b"")
    assert err.value.status == 429 and "rate" in err.value.text


@pytest.fixture
def silent_server():
    """Сокет, который читает запрос и молчит (`hang`) или закрывает связь без ответа (`drop`)."""
    servers = []

    def start(mode):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        servers.append(srv)

        def run():
            conn, _ = srv.accept()
            conn.recv(65536)
            if mode == "drop":
                conn.close()
            else:
                threading.Event().wait(3)
                conn.close()

        threading.Thread(target=run, daemon=True).start()
        return f"http://127.0.0.1:{srv.getsockname()[1]}/"

    yield start
    for srv in servers:
        srv.close()


@pytest.mark.parametrize("mode", ["hang", "drop"])
def test_http_send_network_failure_is_user_error(silent_server, monkeypatch, mode):
    monkeypatch.setattr(vz, "TIMEOUT", 1)
    with pytest.raises(vz.UserError):
        vz.http_send(silent_server(mode), {}, b"{}")


# 5. Водяной знак


def test_watermark_changes_bottom_right_only():
    raw = result_png((400, 300))
    marked = Image.open(io.BytesIO(vz.watermark(raw, WATERMARK, mock=False))).convert("RGB")
    plain = Image.open(io.BytesIO(raw)).convert("RGB")
    assert marked.size == plain.size
    corner = (200, 220, 400, 300)
    assert marked.crop(corner).tobytes() != plain.crop(corner).tobytes()
    assert marked.crop((0, 0, 100, 100)).tobytes() == plain.crop((0, 0, 100, 100)).tobytes()


def changed_pixels(img_bytes, raw):
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    base = Image.open(io.BytesIO(raw)).convert("RGB")
    same = ImageChops.difference(img, base).convert("L").histogram()[0]
    return img.width * img.height - same


def test_mock_watermark_covers_more_than_plain():
    raw = result_png((400, 300))
    assert changed_pixels(vz.watermark(raw, "М", mock=True), raw) > changed_pixels(
        vz.watermark(raw, "М", mock=False), raw
    )


def test_logo_is_drawn(tmp_path):
    raw = result_png((400, 300))
    logo = Image.new("RGBA", (50, 50), (255, 0, 0, 255))
    assert changed_pixels(vz.watermark(raw, "М", False, logo), raw) > changed_pixels(
        vz.watermark(raw, "М", False), raw
    )


# 6. Ошибки до запроса


@pytest.mark.parametrize(
    ("drop", "provider", "needle"),
    [
        ("OPENAI_API_KEY", "openai", "OPENAI_API_KEY"),
        ("OPENROUTER_API_KEY", "openrouter", "OPENROUTER_API_KEY"),
        ("VISUALIZATION_WATERMARK", "openai", "VISUALIZATION_WATERMARK"),
    ],
)
def test_missing_setting_fails_before_request(photos, tmp_path, capsys, drop, provider, needle):
    env = {k: v for k, v in ENV.items() if k != drop} | {"VISUALIZATION_PROVIDER": provider}
    send = FakeSend(openai_answer())
    assert vz.main(args(photos, tmp_path / "out"), env=env, send=send) == 2
    assert send.calls == []
    assert needle in capsys.readouterr().err


def test_bad_logo_fails_before_request(photos, tmp_path, capsys):
    send = FakeSend(openai_answer())
    env = {**ENV, "VISUALIZATION_WATERMARK_LOGO": str(tmp_path / "nope.png")}
    assert vz.main(args(photos, tmp_path / "out"), env=env, send=send) == 2
    assert send.calls == []
    assert "Логотип" in capsys.readouterr().err


@pytest.mark.parametrize("missing", ["--room", "--out"])
def test_missing_room_or_out_is_russian_error(photos, tmp_path, capsys, missing):
    argv = args(photos, tmp_path / "out")
    i = argv.index(missing)
    del argv[i : i + 2]
    send = FakeSend(openai_answer())
    assert vz.main(argv, env=ENV, send=send) == 2
    assert send.calls == []
    err = capsys.readouterr().err
    assert err.startswith("Ошибка: Нет ")


def test_bad_argument_is_russian_error(capsys):
    assert vz.main(["generate", "--n", "два"], env=ENV) == 2
    assert capsys.readouterr().err.startswith("Ошибка: неверные аргументы")


def test_not_an_image_is_error(photos, tmp_path, capsys):
    bad = tmp_path / "room.heic"
    bad.write_bytes(b"not an image at all")
    send = FakeSend(openai_answer())
    assert vz.main(args({**photos, "room": bad}, tmp_path / "out"), env=ENV, send=send) == 2
    assert send.calls == []
    assert "не картинка" in capsys.readouterr().err


def test_missing_file_is_error(photos, tmp_path, capsys):
    photos = {**photos, "room": tmp_path / "nope.png"}
    assert vz.main(args(photos, tmp_path / "out"), env=ENV, send=FakeSend(b"")) == 2
    assert "не найден" in capsys.readouterr().err


# 7. .env


def test_dotenv_found_upward_and_environment_wins(tmp_path):
    (tmp_path / ".env").write_text(
        '# комментарий\nOPENAI_API_KEY="from-file"\nexport VISUALIZATION_WATERMARK=Файл\n\n', encoding="utf-8"
    )
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    merged = vz.load_env(deep, {"VISUALIZATION_WATERMARK": "Окружение"})
    assert merged["OPENAI_API_KEY"] == "from-file"
    assert merged["VISUALIZATION_WATERMARK"] == "Окружение"


def test_nearest_dotenv_wins(tmp_path):
    (tmp_path / ".env").write_text("VISUALIZATION_WATERMARK=far\n", encoding="utf-8")
    near = tmp_path / "a"
    near.mkdir()
    (near / ".env").write_text("VISUALIZATION_WATERMARK=near\n", encoding="utf-8")
    assert vz.load_env(near, {})["VISUALIZATION_WATERMARK"] == "near"


def test_dotenv_inline_comments_and_quotes():
    values = vz.parse_dotenv("A=\"x # y\" # note\nB=plain # note\nC='q'\nD=a#b\n")
    assert values == {"A": "x # y", "B": "plain", "C": "q", "D": "a#b"}


def test_no_dotenv_keeps_environment(tmp_path):
    assert vz.load_env(tmp_path, {"X": "1"})["X"] == "1"


# 8. Каталог образцов


def test_catalog_lists_shapes_with_absolute_photos_missing_and_warning(tmp_path, capsys):
    cat = make_catalog(tmp_path)
    assert vz.main(["catalog", "--catalog", str(cat)], env={}) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mock"] is True and out["warning"] == "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА"
    corner, straight = out["shapes"]
    assert corner["id"] == "corner" and corner["название"] == "Угловая"
    assert corner["фото"] == [str((cat / "corner-1.png").resolve())]
    assert straight["название"] == "прямая" and straight["фото"] == []
    assert out["missing"] == [str((cat / "corner-2.png").resolve())]


def test_catalog_found_by_env_and_kitchen_home(tmp_path):
    cat = make_catalog(tmp_path)
    assert vz.main(["catalog"], env={"VISUALIZATION_CATALOG": str(cat)}) == 0
    home = tmp_path / "home"
    (home / "workshop").mkdir(parents=True)
    cat.rename(home / "workshop" / "sample-catalog")
    assert vz.main(["catalog"], env={"KITCHEN_HOME": str(home)}) == 0


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("shapes:\n  - id: l_shape\n", "id должен быть"),
        ("shapes:\n  - название: Без id\n", "id должен быть"),
        ("shapes:\n  - id: corner\n    фото: a.png\n", "список"),
        ("shapes: [\n", "YAML"),
    ],
)
def test_malformed_catalog_is_user_error(tmp_path, capsys, text, needle):
    (tmp_path / "catalog.yaml").write_text(text, encoding="utf-8")
    assert vz.main(["catalog", "--catalog", str(tmp_path)], env={}) == 2
    assert needle in capsys.readouterr().err


def test_shape_from_mock_catalog_marks_png_and_json(photos, tmp_path, capsys):
    cat = make_catalog(tmp_path)
    send = FakeSend(openai_answer())
    assert vz.main(catalog_argv(photos, cat, tmp_path / "out"), env=ENV, send=send) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mock"] is True and out["warning"] == "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА"
    assert out["shape"] == "corner"
    assert "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА" in out["watermark"]
    assert [i["role"] for i in out["inputs"]] == ["room", "sample"]
    got = Path(out["images"][0]).read_bytes()
    assert got == vz.watermark(result_png(), WATERMARK, mock=True)


def test_shape_from_real_catalog_is_not_mock(photos, tmp_path, capsys):
    cat = make_catalog(tmp_path, mock=False)
    assert vz.main(catalog_argv(photos, cat, tmp_path / "out"), env=ENV, send=FakeSend(openai_answer())) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mock"] is False and "warning" not in out
    assert out["watermark"] == WATERMARK


def test_own_sample_skips_catalog(photos, tmp_path, capsys):
    env = {**ENV, "VISUALIZATION_CATALOG": str(tmp_path / "nowhere")}
    assert vz.main(args(photos, tmp_path / "out"), env=env, send=FakeSend(openai_answer())) == 0
    assert json.loads(capsys.readouterr().out)["mock"] is False


def test_shape_without_photos_is_error(photos, tmp_path, capsys):
    cat = ROOT / "workshop" / "sample-catalog"
    send = FakeSend(openai_answer())
    assert vz.main(catalog_argv(photos, cat, tmp_path / "o"), env=ENV, send=send) == 2
    assert send.calls == []
    assert "нет фото" in capsys.readouterr().err


def test_workshop_catalog_has_the_four_brief_shapes():
    data = vz.load_catalog(ROOT / "workshop" / "sample-catalog")
    assert data["mock"] is True
    assert [s["id"] for s in data["shapes"]] == ["straight", "corner", "u_shape", "island"]
    schema = json.loads((ROOT / "skills" / "kitchen-brief" / "references" / "brief_schema.json").read_text())
    assert set(vz.SHAPES) == set(schema["sections"]["room"]["fields"]["shape"]["values"])


# 9. Сохранение


def test_saves_png_json_and_input_copies(photos, tmp_path, capsys):
    out_dir = tmp_path / "orders" / "2026-050-ivanov" / "ai-visualization"
    assert vz.main(args(photos, out_dir), env=ENV, send=FakeSend(openai_answer())) == 0
    out = json.loads(capsys.readouterr().out)
    saved = json.loads(Path(out["json"]).read_text(encoding="utf-8"))
    assert saved == out
    assert out["provider"] == "openai" and out["model"] == "gpt-image-1"
    assert out["mock"] is False and out["watermark"] == WATERMARK
    assert out["shape"] == "corner" and out["wishes"] == "фасады без ручек"
    assert [i["role"] for i in out["inputs"]] == ["room", "sample", "material:фасады", "material:столешница"]
    for item in out["inputs"]:
        copy = Path(item["path"])
        assert copy.parent == out_dir / "inputs" and copy.is_file()
    image = Path(out["images"][0])
    assert image.parent == out_dir and image.name.startswith("visualization-") and image.suffix == ".png"
    Image.open(image).verify()
    assert "sk-test" not in json.dumps(out)


def test_role_with_slash_is_safe_in_copy_name(photos, tmp_path, capsys):
    argv = ["generate", "--room", str(photos["room"]), "--material", f"низ/верх={photos['front']}"]
    argv += ["--out", str(tmp_path / "out")]
    assert vz.main(argv, env=ENV, send=FakeSend(openai_answer())) == 0
    copy = Path(json.loads(capsys.readouterr().out)["inputs"][1]["path"])
    assert copy.parent == tmp_path / "out" / "inputs" and copy.is_file()


def test_two_runs_in_one_second_do_not_overwrite(photos, tmp_path, capsys):
    out_dir = tmp_path / "out"
    vz.main(args(photos, out_dir), env=ENV, send=FakeSend(openai_answer()))
    first = json.loads(capsys.readouterr().out)
    vz.main(args(photos, out_dir), env=ENV, send=FakeSend(openai_answer()))
    second = json.loads(capsys.readouterr().out)
    assert first["json"] != second["json"] and first["images"] != second["images"]
    assert Path(first["json"]).is_file() and Path(second["json"]).is_file()


def test_ai_visualization_folders_are_ignored_by_git():
    import subprocess

    for path in ("orders/2026-050-ivanov/ai-visualization/x.png", "orders/_ai-visualization/x/inputs/r.jpg"):
        assert subprocess.run(["git", "check-ignore", "-q", path], check=False, cwd=ROOT).returncode == 0, (
            path
        )


# Ревью 2: кривые ответы и файлы, пробелы в тестах


@pytest.mark.parametrize(
    ("provider", "body"),
    [
        ("openai", {"data": [{"b64_json": 5}]}),
        ("openrouter", {"choices": [{"message": "x"}]}),
        ("openrouter", {"choices": [{"message": {"images": [{"image_url": "data:x"}]}}]}),
        ("openrouter", {"choices": [{"message": {"images": [{"image_url": {"url": "data:image/png"}}]}}]}),
    ],
)
def test_malformed_answer_is_russian_error(photos, tmp_path, capsys, provider, body):
    env = {**ENV, "VISUALIZATION_PROVIDER": provider}
    assert vz.main(args(photos, tmp_path / "out"), env=env, send=FakeSend(json.dumps(body).encode())) == 2
    assert capsys.readouterr().err.startswith("Ошибка: ")


def test_catalog_not_utf8_is_russian_error(tmp_path, capsys):
    (tmp_path / "catalog.yaml").write_bytes("mock: true\nshapes: []\n# Угловая\n".encode("cp1251"))
    assert vz.main(["catalog", "--catalog", str(tmp_path)], env={}) == 2
    assert "UTF-8" in capsys.readouterr().err


def test_dotenv_not_utf8_is_user_error(tmp_path):
    (tmp_path / ".env").write_bytes(b"A=\xff\n")
    with pytest.raises(vz.UserError, match="UTF-8"):
        vz.load_env(tmp_path, {})


def test_blank_watermark_fails_before_request(photos, tmp_path, capsys):
    send = FakeSend(openai_answer())
    assert (
        vz.main(
            args(photos, tmp_path / "out", "--watermark", "   "),
            env={**ENV, "VISUALIZATION_WATERMARK": ""},
            send=send,
        )
        == 2
    )
    assert send.calls == []


def test_logo_from_env_is_on_saved_png(photos, tmp_path, capsys):
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (50, 50), (255, 0, 0, 255)).save(logo)
    env = {**ENV, "VISUALIZATION_WATERMARK_LOGO": str(logo)}
    assert vz.main(args(photos, tmp_path / "out"), env=env, send=FakeSend(openai_answer())) == 0
    got = Path(json.loads(capsys.readouterr().out)["images"][0]).read_bytes()
    assert got == vz.watermark(result_png(), WATERMARK, False, vz.load_logo(str(logo)))


def test_invalid_base64_is_russian_error(photos, tmp_path, capsys):
    bad = json.dumps({"data": [{"b64_json": "!!!not base64"}]}).encode()
    assert vz.main(args(photos, tmp_path / "out"), env=ENV, send=FakeSend(bad)) == 2
    assert "повреждены" in capsys.readouterr().err


def test_n_zero_is_error_before_request(photos, tmp_path, capsys):
    send = FakeSend(openrouter_answer())
    env = {**ENV, "VISUALIZATION_PROVIDER": "openrouter"}
    assert vz.main(args(photos, tmp_path / "out", "--n", "0"), env=env, send=send) == 2
    assert send.calls == [] and "--n" in capsys.readouterr().err


def test_unknown_provider_is_error_before_request(photos, tmp_path, capsys):
    send = FakeSend(openai_answer())
    assert vz.main(args(photos, tmp_path / "out", "--provider", "midjourney"), env=ENV, send=send) == 2
    assert send.calls == [] and "openrouter" in capsys.readouterr().err


@pytest.mark.parametrize(("mode", "needle"), [("hang", "не ответил"), ("drop", "оборвалась")])
def test_network_failure_text_is_russian(silent_server, monkeypatch, mode, needle):
    monkeypatch.setattr(vz, "TIMEOUT", 1)
    with pytest.raises(vz.UserError, match=needle):
        vz.http_send(silent_server(mode), {}, b"{}")


def test_no_connection_text_is_russian():
    with pytest.raises(vz.UserError, match="Нет связи"):
        vz.http_send("http://127.0.0.1:1/", {}, b"{}")


def test_watermark_text_height_is_3_percent():
    raw = result_png((1000, 600))
    img = Image.open(io.BytesIO(vz.watermark(raw, "ММММ", mock=False))).convert("RGB")
    box = ImageChops.difference(img, Image.open(io.BytesIO(raw)).convert("RGB")).getbbox()
    assert box is not None and 0.02 * 600 <= box[3] - box[1] <= 0.045 * 600


def test_out_is_a_file_is_error_before_request(photos, tmp_path, capsys):
    out = tmp_path / "out"
    out.write_text("x")
    send = FakeSend(openai_answer())
    assert vz.main(args(photos, out), env=ENV, send=send) == 2
    assert send.calls == [] and "Не создать папку" in capsys.readouterr().err


def test_catalog_found_upward_from_cwd(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "workshop").mkdir(parents=True)
    make_catalog(tmp_path).rename(home / "workshop" / "sample-catalog")
    deep = home / "orders" / "x"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    assert vz.main(["catalog"], env={}) == 0


def test_catalog_not_a_mapping_is_error(tmp_path, capsys):
    (tmp_path / "catalog.yaml").write_text("- straight\n", encoding="utf-8")
    assert vz.main(["catalog", "--catalog", str(tmp_path)], env={}) == 2
    assert "словарь" in capsys.readouterr().err


def test_material_path_with_equals_sign(photos, tmp_path, capsys):
    odd = png(tmp_path / "a=b.png")
    argv = ["generate", "--room", str(photos["room"]), "--material", str(odd), "--out", str(tmp_path / "out")]
    assert vz.main(argv, env=ENV, send=FakeSend(openai_answer())) == 0
    assert json.loads(capsys.readouterr().out)["inputs"][1]["role"] == "material"


# Ревью 3: испорченные картинки и странные значения каталога — весь класс, а не случаи


@pytest.mark.parametrize(("pos", "value"), [(11, 0), (35, 0)])
def test_broken_png_from_service_is_russian_error(photos, tmp_path, capsys, pos, value):
    raw = bytearray(result_png())
    raw[pos] = value
    body = json.dumps({"data": [{"b64_json": base64.b64encode(bytes(raw)).decode()}]}).encode()
    assert vz.main(args(photos, tmp_path / "out"), env=ENV, send=FakeSend(body)) == 2
    assert capsys.readouterr().err.startswith("Ошибка: ")


def corrupted(data: bytes, seed: int) -> bytes:
    """Детерминированная порча: несколько байт заменены, иногда хвост обрезан."""
    import random

    rnd = random.Random(seed)
    raw = bytearray(data)
    for _ in range(rnd.randint(1, 4)):
        raw[rnd.randrange(len(raw))] = rnd.randrange(256)
    if rnd.random() < 0.3:
        raw = raw[: rnd.randrange(8, len(raw))]
    return bytes(raw)


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_any_corrupted_image_gives_picture_or_user_error(fmt):
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (90, 140, 200)).save(buf, fmt)
    for seed in range(400):
        try:
            vz.watermark(corrupted(buf.getvalue(), seed), "М", mock=False)
        except vz.UserError:
            pass


def test_broken_logo_fails_before_request(photos, tmp_path, capsys):
    raw = bytearray(result_png())
    raw[35] = 0
    logo = tmp_path / "logo.png"
    logo.write_bytes(bytes(raw))
    send = FakeSend(b"{}")
    env = {**ENV, "VISUALIZATION_WATERMARK_LOGO": str(logo)}
    assert vz.main(args(photos, tmp_path / "out"), env=env, send=send) == 2
    assert send.calls == [] and "Логотип" in capsys.readouterr().err


def test_truncated_photo_fails_before_request(photos, tmp_path, capsys):
    room = tmp_path / "cut.jpg"
    room.write_bytes(photos["sample"].read_bytes()[:40])
    send = FakeSend(openai_answer())
    assert vz.main(args({**photos, "room": room}, tmp_path / "out"), env=ENV, send=send) == 2
    assert send.calls == [] and "повреждена" in capsys.readouterr().err


@pytest.mark.parametrize(
    "text",
    [
        "shapes:\n  - id: [corner]\n",
        "shapes:\n  - id: {a: 1}\n",
        'shapes:\n  - id: corner\n    фото: ["a\\0b.png"]\n',
        "shapes:\n  - id: corner\n    фото: [[a.png]]\n",
    ],
)
def test_odd_catalog_values_are_user_error(tmp_path, capsys, text):
    (tmp_path / "catalog.yaml").write_text(text, encoding="utf-8")
    assert vz.main(["catalog", "--catalog", str(tmp_path)], env={}) == 2
    assert capsys.readouterr().err.startswith("Ошибка: ")


@pytest.mark.parametrize(
    ("provider", "body"), [("openai", {"data": 5}), ("openrouter", {"choices": {"a": 1}})]
)
def test_odd_answer_shape_is_russian_error(photos, tmp_path, capsys, provider, body):
    env = {**ENV, "VISUALIZATION_PROVIDER": provider}
    assert vz.main(args(photos, tmp_path / "out"), env=env, send=FakeSend(json.dumps(body).encode())) == 2
    assert capsys.readouterr().err.startswith("Ошибка: ")
