"""Unit tests for the nes-emulator plugin backend router.

Read-only, local-only contract: iNES header parsing (mapper/size/battery/
NES 2.0), title-from-filename + trailing 128-byte blob, scan filtering, and
the bytes route. Run from anywhere:

    python -m pytest tests -q
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
DASHBOARD = HERE.parent / "dashboard"
sys.path.insert(0, str(DASHBOARD))

import plugin_api  # noqa: E402
from plugin_api import _title_blob, _title_from_filename, parse_header  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(plugin_api.router)
    return TestClient(app)


def _rom(
    prg_units: int = 2,
    chr_units: int = 1,
    flags6: int = 0,
    flags7: int = 0,
    prg_ram: int = 0,
    extra: bytes = b"",
) -> bytes:
    header = (
        b"NES\x1a"
        + bytes([prg_units, chr_units, flags6, flags7, prg_ram, 0, 0])
        + b"\x00" * 5
    )
    return header + extra


# ── L2: iNES header parsing ──────────────────────────────────────────────────


def test_parse_basic_header():
    h = parse_header(_rom(prg_units=2, chr_units=1))
    assert h["prg_rom_kb"] == 32
    assert h["chr_rom_kb"] == 8
    assert h["mapper"] == 0
    assert h["mirroring"] == "horizontal"
    assert h["battery_backed_sram"] is False
    assert h["format"] == "iNES 1.0"


def test_parse_mapper_and_battery_and_vertical():
    # flags6: 0x01 vertical + 0x02 battery + 0x10 mapper-lo bit (mapper 1 low)
    # flags7: 0x00 mapper-hi bits -> mapper = 1
    h = parse_header(_rom(flags6=0x13, flags7=0x00))
    assert h["mapper"] == 1
    assert h["mirroring"] == "vertical"
    assert h["battery_backed_sram"] is True
    assert h["has_trainer"] is False


def test_parse_nes20_detection():
    # flags7 = 0x08 -> NES 2.0 signature present; mapper hi nibble 0x20
    h = parse_header(_rom(flags7=0x28, prg_ram=1))
    assert h["format"] == "NES 2.0"
    assert h["mapper"] == (0x20 | 0)  # mapper lo = flags6>>4 = 0
    assert h["prg_ram_kb"] == 128  # 64 << 1


def test_parse_rejects_bad_magic():
    with pytest.raises(plugin_api.ApiError) as exc:
        parse_header(b"XXXX" + b"\x00" * 12)
    assert exc.value.code == "not_a_nes_rom"


def test_parse_rejects_short_file():
    with pytest.raises(plugin_api.ApiError):
        parse_header(b"NES\x1a")


# ── L2: title derivation ─────────────────────────────────────────────────────


def test_title_from_filename():
    assert _title_from_filename("some_game.nes") == "some game"
    assert _title_from_filename("mega-man-2.nes") == "mega man 2"
    assert _title_from_filename("kirby.nes") == "kirby"


def test_title_blob_preferred():
    blob = b"My Cool Game".ljust(128, b" ")
    data = _rom(extra=b"\x00" * 16 + blob)
    assert _title_blob(data) == "My Cool Game"


def test_title_blob_rejects_non_printable():
    data = _rom(extra=b"\x00" * 16 + b"\xff" * 128)
    assert _title_blob(data) is None


# ── L2: routes ───────────────────────────────────────────────────────────────


def _write_rom(dir: Path, name: str, body: bytes) -> Path:
    p = dir / name
    p.write_bytes(body)
    return p


def test_scan_lists_only_nes_sorted(client: TestClient, tmp_path: Path):
    _write_rom(tmp_path, "b.nes", _rom())
    _write_rom(tmp_path, "a.nes", _rom(prg_units=4))
    (tmp_path / "notes.txt").write_text("ignore me")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.nes").write_bytes(_rom())  # non-recursive

    r = client.get("/scan", params={"dir": str(tmp_path)})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert [rom["fileName"] for rom in body["roms"]] == ["a.nes", "b.nes"]
    assert body["roms"][0]["title"] == "a"
    assert body["roms"][0]["header"]["prg_rom_kb"] == 64


def test_scan_rejects_relative_dir(client: TestClient):
    assert client.get("/scan", params={"dir": "relative/path"}).status_code == 400


def test_scan_roms_sentinel_creates_library_dir(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    r = client.get("/scan", params={"dir": "__roms__"})
    assert r.status_code == 200
    assert r.json()["count"] == 0
    assert (tmp_path / "home" / "desktop-plugins" / "nes-emulator" / "roms").is_dir()


def test_scan_roms_sentinel_lists_files(client: TestClient, tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    roms = home / "desktop-plugins" / "nes-emulator" / "roms"
    roms.mkdir(parents=True)
    (roms / "homebrew.nes").write_bytes(_rom())
    r = client.get("/scan", params={"dir": "__roms__"})
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["roms"][0]["fileName"] == "homebrew.nes"


def test_scan_missing_dir(client: TestClient):
    assert client.get("/scan", params={"dir": "/nope/nope"}).status_code == 404


def test_scan_marks_bad_header(client: TestClient, tmp_path: Path):
    _write_rom(tmp_path, "broken.nes", b"not a rom")
    r = client.get("/scan", params={"dir": str(tmp_path)})
    assert r.status_code == 200
    assert "error" in r.json()["roms"][0]["header"]


def test_bytes_roundtrip(client: TestClient, tmp_path: Path):
    body = _rom(prg_units=1) + b"\xde\xad\xbe\xef"
    p = _write_rom(tmp_path, "game.nes", body)
    r = client.get("/bytes", params={"path": str(p)})
    assert r.status_code == 200
    data = r.json()
    assert data["size"] == len(body)
    assert base64.b64decode(data["base64"]) == body


def test_bytes_rejects_non_nes(client: TestClient, tmp_path: Path):
    p = tmp_path / "file.txt"
    p.write_text("hi")
    assert client.get("/bytes", params={"path": str(p)}).status_code == 422


def test_bytes_rejects_missing(client: TestClient):
    assert client.get("/bytes", params={"path": "/nope/x.nes"}).status_code == 404


def test_bytes_rejects_traversal(client: TestClient, tmp_path: Path):
    p = _write_rom(tmp_path, "game.nes", _rom())
    r = client.get("/bytes", params={"path": str(p.parent / ".." / "game.nes")})
    assert r.status_code in (403, 404)


def test_no_mutation_routes(client: TestClient, tmp_path: Path):
    p = _write_rom(tmp_path, "game.nes", _rom())
    # POST/DELETE on known paths -> 405
    assert client.post("/scan", params={"dir": str(tmp_path)}).status_code == 405
    assert client.delete("/bytes", params={"path": str(p)}).status_code == 405


# ── L2: audio OS-mute detection (regression for t_86d80aae / t_5c5b1e4e) ───


def _sink(
    pid: int,
    mute: bool,
    app: str = "Chromium",
    binary: str = "Hermes",
    media: str = "Playback",
) -> dict:
    return {
        "index": pid,
        "mute": mute,
        "properties": {
            "application.name": app,
            "application.process.binary": binary,
            "application.process.id": str(pid),
            "media.name": media,
        },
    }


@patch("plugin_api._pactl_json")
def test_audio_os_state_muted(mock_pactl, client: TestClient):
    # Use a foreign audio-utility pid (not the pytest process) and set a parent
    # ancestor so that pid-scope matching still succeeds.
    audio_utility_pid = os.getpid() + 12345
    mock_pactl.return_value = [_sink(audio_utility_pid, True)]
    with patch.dict(os.environ, {"HERMES_PARENT_PID": str(os.getpid())}):
        r = client.get("/audio/os-state")
    assert r.status_code == 200
    body = r.json()
    assert body["osMuted"] is True
    assert body["available"] is True
    assert body["streamIndex"] == audio_utility_pid


@patch("plugin_api._pactl_json")
def test_audio_os_state_unmuted(mock_pactl, client: TestClient):
    audio_utility_pid = os.getpid() + 12345
    mock_pactl.return_value = [_sink(audio_utility_pid, False)]
    with patch.dict(os.environ, {"HERMES_PARENT_PID": str(os.getpid())}):
        r = client.get("/audio/os-state")
    assert r.status_code == 200
    body = r.json()
    assert body["osMuted"] is False
    assert body["available"] is True


@patch("plugin_api._pactl_json")
def test_audio_os_state_no_stream(mock_pactl, client: TestClient):
    mock_pactl.return_value = []
    r = client.get("/audio/os-state")
    assert r.status_code == 200
    body = r.json()
    assert body["osMuted"] is False
    assert body["available"] is False


@patch("plugin_api._pactl_json")
@patch("plugin_api._is_descendant")
def test_audio_os_state_ignores_foreign_sibling(
    mock_is_descendant, mock_pactl, client: TestClient
):
    # A second Hermes instance with a non-descendant audio-utility pid must not be
    # matched when HERMES_PARENT_PID is set. We also include a valid descendant
    # candidate so there is a second option to reject.
    foreign_audio_pid = os.getpid() + 99999
    local_audio_pid = os.getpid() + 12345
    mock_pactl.return_value = [
        _sink(local_audio_pid, True),
        _sink(foreign_audio_pid, True),
    ]
    mock_is_descendant.side_effect = lambda pid, ancestor: pid == local_audio_pid
    with patch.dict(os.environ, {"HERMES_PARENT_PID": str(os.getpid())}):
        r = client.get("/audio/os-state")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["streamIndex"] == local_audio_pid


@patch("plugin_api.shutil.which")
@patch("plugin_api.subprocess.run")
@patch("plugin_api._pactl_json")
def test_audio_os_unmute(mock_pactl, mock_run, mock_which, client: TestClient):
    audio_utility_pid = os.getpid() + 12345
    mock_which.return_value = "/usr/bin/pactl"
    mock_pactl.side_effect = [
        [_sink(audio_utility_pid, True)],
        [_sink(audio_utility_pid, False)],
    ]
    with patch.dict(os.environ, {"HERMES_PARENT_PID": str(os.getpid())}):
        r = client.post("/audio/os-unmute")
    assert r.status_code == 200
    body = r.json()
    assert body["osMuted"] is False
    assert body["available"] is True
    mock_run.assert_called_once_with(
        ["pactl", "set-sink-input-mute", str(audio_utility_pid), "0"],
        capture_output=True,
        check=False,
        timeout=5,
    )
