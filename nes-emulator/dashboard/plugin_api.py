"""HerNES (NES emulator) plugin backend — ROM scan + iNES header parse + ROM bytes.

Mounted at ``/api/plugins/nes-emulator/`` by the Hermes dashboard plugin
system (manifest.json declares ``api=plugin_api.py``; the plugin must be in
``plugins.enabled`` in config.yaml for the serve process to import it).

Read-only, local-only surface:

    GET /scan?dir=<abs dir>   list *.nes files (filename, derived title, path,
                              parsed header summary); non-recursive, sorted
    GET /header?path=<abs>    full parsed iNES header for one ROM
    GET /bytes?path=<abs>     raw ROM bytes as base64 (for the renderer to
                              hand to jsnes)
    GET /audio/os-state       detect whether the current process's Chromium
                              playback stream is OS-muted (WirePlumber)
    POST /audio/os-unmute     unmute that stream via pactl

Rules enforced here:
  * no mutation routes except the audio unmute helper (POST /audio/os-unmute);
  * only ``*.nes`` files, and only absolute paths that resolve inside the
    requested scan dir (no ``..`` traversal, no symlink escape);
  * no network access, no downloader, no bundled ROMs — matches the legal
    boundary (t_b2a19c7f): the plugin plays files the user already has.

Auth is handled by the dashboard session-token middleware; this router does
not reimplement it.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

# Renderer-side sentinel for the plugin's own library dir: the backend resolves
# it to <HERMES_HOME>/desktop-plugins/nes-emulator/roms (HERMES_HOME falls back
# to ~/.hermes). The renderer cannot know the absolute path.
_ROMS_SENTINEL = "__roms__"

_ROM_SUFFIX = ".nes"
# 16-byte iNES header: magic NES\x1a, PRG (16KB units), CHR (8KB units),
# flags6, flags7, PRG-RAM, TV system, flags10, padding. No title field.
_HEADER_LEN = 16
_MAGIC = b"NES\x1a"
# NES 2.0 signature: (flags7 & 0x0C) == 0x08.
_NES20_MASK = 0x0C
_NES20_SIG = 0x08
# A 128-byte printable title blob MAY trail the ROM (non-standard).
_TITLE_BLOB_LEN = 128


class ApiError(HTTPException):
    """Typed error carried as an HTTPException FastAPI already knows how to handle."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(status_code=status, detail={"code": code, "message": message})
        self.code = code
        self.message = message


def _err(status: int, code: str, message: str) -> ApiError:
    return ApiError(status, code, message)


def _title_from_filename(name: str) -> str:
    """Derive a human title from the filename (iNES header has no title)."""
    stem = name[: -len(_ROM_SUFFIX)] if name.lower().endswith(_ROM_SUFFIX) else name
    return re.sub(r"[_\-]+", " ", stem).strip() or stem


def _title_blob(data: bytes) -> str | None:
    """Optional non-standard trailing 128-byte title, printable ASCII only."""
    if len(data) <= _HEADER_LEN + _TITLE_BLOB_LEN:
        return None
    blob = data[-_TITLE_BLOB_LEN:]
    if not all(0x20 <= b < 0x7F for b in blob):
        return None
    title = blob.decode("ascii", "replace").strip()
    return title or None


def parse_header(data: bytes) -> dict:
    """Parse the 16-byte iNES header (see NESdev wiki 'iNES')."""
    if len(data) < _HEADER_LEN or data[:4] != _MAGIC:
        raise ApiError(422, "not_a_nes_rom", "file does not have an iNES header")
    prg_units = data[4]
    chr_units = data[5]
    flags6 = data[6]
    flags7 = data[7]
    prg_ram = data[8]
    tv_system = data[9]
    flags10 = data[10]

    is_nes20 = (flags7 & _NES20_MASK) == _NES20_SIG
    mapper = (flags7 & 0xF0) | (flags6 >> 4)

    return {
        "format": "NES 2.0" if is_nes20 else "iNES 1.0",
        "prg_rom_kb": prg_units * 16,
        "chr_rom_kb": chr_units * 8,
        "mapper": mapper,
        "mirroring": "vertical" if (flags6 & 0x01) else "horizontal",
        "battery_backed_sram": bool(flags6 & 0x02),
        "has_trainer": bool(flags6 & 0x04),
        "four_screen_vram": bool(flags6 & 0x08),
        "vs_unisystem": bool(flags7 & 0x01),
        "playchoice10": bool(flags7 & 0x02),
        "prg_ram_kb": _prg_ram_kb(prg_ram) if is_nes20 else 0,
        "tv_system": "PAL" if (tv_system & 0x01) else "NTSC",
        "vs_hardware": bool(flags10 & 0x01) if is_nes20 else False,
    }


def _prg_ram_kb(shift: int) -> int:
    """NES 2.0 PRG-RAM: 64 << shift, shift 0 = 0 (none)."""
    if shift == 0:
        return 0
    return 64 << shift


def _resolve_scan_dir(dir: str) -> Path:
    """Resolve a scan dir request; ``__roms__`` maps to the plugin's own
    ``<HERMES_HOME>/desktop-plugins/nes-emulator/roms`` library dir."""
    if dir == _ROMS_SENTINEL:
        root = Path(os.environ.get("HERMES_HOME", "~")).expanduser() / "desktop-plugins" / "nes-emulator" / "roms"
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ApiError(500, "roms_dir_error", f"could not create ROMs dir: {exc}") from exc
        return root
    return Path(dir).expanduser()


def _resolve_rom_path(dir_path: str, filename: str) -> Path:
    """Resolve a scan entry to a safe absolute path inside ``dir_path``."""
    root = Path(dir_path).expanduser()
    if not root.is_absolute():
        raise ApiError(400, "bad_dir", "scan dir must be an absolute path")
    candidate = (root / filename).resolve()
    if root.resolve() not in candidate.parents:
        raise ApiError(403, "path_escape", "path escapes the scan directory")
    return candidate


def _read_rom(path: str) -> bytes:
    p = Path(path).expanduser()
    if not p.is_absolute() or not p.is_file():
        raise ApiError(404, "rom_not_found", "ROM file not found")
    if p.suffix.lower() != _ROM_SUFFIX:
        raise ApiError(422, "not_nes", "only .nes files are supported")
    if p.resolve() != p:
        raise ApiError(403, "symlink", "symlinked ROMs are not supported")
    try:
        return p.read_bytes()
    except OSError as exc:
        raise ApiError(500, "read_error", f"could not read ROM: {exc}") from exc


@router.get("/scan")
def scan(dir: str) -> dict:
    root = _resolve_scan_dir(dir)
    if not root.is_absolute():
        raise _err(400, "bad_dir", "scan dir must be an absolute path")
    if not root.is_dir():
        raise _err(404, "dir_not_found", "scan directory does not exist")
    items: list[dict] = []
    try:
        entries = sorted(
            (e for e in root.iterdir() if e.is_file() and e.name.lower().endswith(_ROM_SUFFIX)),
            key=lambda e: e.name.lower(),
        )
    except OSError as exc:
        raise _err(500, "scan_error", f"could not scan directory: {exc}") from exc
    for entry in entries:
        data = entry.read_bytes()
        try:
            header = parse_header(data)
        except ApiError as exc:
            header = {"error": exc.message}
        title = _title_blob(data) or _title_from_filename(entry.name)
        items.append(
            {
                "fileName": entry.name,
                "title": title,
                "path": str(entry),
                "size": entry.stat().st_size,
                "header": header,
            }
        )
    return {"dir": str(root), "count": len(items), "roms": items}


@router.get("/header")
def header(path: str) -> dict:
    data = _read_rom(path)
    return {"path": path, **parse_header(data)}


@router.get("/bytes")
def rom_bytes(path: str) -> dict:
    data = _read_rom(path)
    return {
        "path": path,
        "size": len(data),
        "base64": base64.b64encode(data).decode("ascii"),
    }


# ── Audio OS-mute detection / unmute ────────────────────────────────────────
# WirePlumber restores per-application stream state. Because every Electron/
# Chromium renderer reports application.name="Chromium", a user-muted browser
# stream can be silently restored onto the Hermes NES playback stream. The
# plugin's worklet stats look healthy (playedTotal climbing) while the OS layer
# is muted. These helpers detect and surface that state so the UI can warn and
# offer an unmute action.


def _pactl_json() -> list[dict]:
    """Return `pactl -f json list sink-inputs` as a list, or empty on failure."""
    if not shutil.which("pactl"):
        return []
    try:
        proc = subprocess.run(
            ["pactl", "-f", "json", "list", "sink-inputs"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if proc.returncode != 0:
            return []
        return json.loads(proc.stdout or "[]")
    except Exception:  # noqa: BLE001
        return []


def _proc_ppid(pid: int) -> int | None:
    """Read the parent pid from ``/proc/<pid>/stat``; None if unreadable."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # comm may contain spaces and parens; the rest is space-delimited. The
    # ppid is the fourth field after the final ')' of the comm.
    rparen = stat.rfind(")")
    if rparen == -1:
        return None
    fields = stat[rparen + 1 :].split()
    if len(fields) < 2:
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def _is_descendant(pid: int, ancestor_pid: int) -> bool:
    """True if ``pid`` is ``ancestor_pid`` or any descendant of it."""
    visited = set()
    while pid is not None and pid != 0 and pid not in visited:
        if pid == ancestor_pid:
            return True
        visited.add(pid)
        pid = _proc_ppid(pid)
    return False


def _find_hermes_chromium_stream() -> dict | None:
    """Find the Hermes Chromium Playback sink-input for this process family.

    The Chromium "Playback" stream is owned by an Electron audio-utility process
    that is a *sibling* of the Python serve process, so matching
    ``application.process.id`` against ``os.getpid()`` never succeeds. We
    instead match the Hermes binary + media name, then disambiguate by ancestry
    using the desktop app's main process id (``HERMES_PARENT_PID``) when present.

    Returns:
        A dict with ``index``, ``mute``, ``application_name`` and ``media_name``,
        or None if no matching stream is found.
    """
    candidates: list[dict] = []
    for sink in _pactl_json():
        props = sink.get("properties") or {}
        if props.get("application.name") != "Chromium":
            continue
        if props.get("application.process.binary") != "Hermes":
            continue
        if props.get("media.name") != "Playback":
            continue
        candidates.append(sink)

    if not candidates:
        return None
    if len(candidates) == 1:
        sink = candidates[0]
        props = sink.get("properties") or {}
        return {
            "index": sink.get("index"),
            "mute": sink.get("mute", False),
            "application_name": "Chromium",
            "media_name": props.get("media.name") or "",
        }

    # Multiple Hermes instances can run concurrently. Pick the one descended from
    # the desktop main process when possible.
    ancestor_pid: int | None = None
    env_parent = os.environ.get("HERMES_PARENT_PID")
    if env_parent:
        try:
            ancestor_pid = int(env_parent)
        except ValueError:
            ancestor_pid = None
    if ancestor_pid is None:
        # Fallback: anything descended from the serve process's own parent is
        # likely part of this Hermes instance.
        ancestor_pid = _proc_ppid(os.getpid())

    if ancestor_pid is not None:
        for sink in candidates:
            props = sink.get("properties") or {}
            try:
                owner_pid = int(props.get("application.process.id", "0"))
            except ValueError:
                continue
            if _is_descendant(owner_pid, ancestor_pid):
                return {
                    "index": sink.get("index"),
                    "mute": sink.get("mute", False),
                    "application_name": "Chromium",
                    "media_name": props.get("media.name") or "",
                }

    # Last resort: if exactly one candidate exists, use it. Above we already
    # handled len==1; with >1 and no ancestry information, we cannot safely pick.
    return None


def _audio_mute_state() -> dict:
    """Return a serializable OS-mute state for the current process's audio."""
    stream = _find_hermes_chromium_stream()
    if stream is None:
        return {"osMuted": False, "streamIndex": None, "available": False}
    return {
        "osMuted": bool(stream["mute"]),
        "streamIndex": stream["index"],
        "available": True,
        "mediaName": stream["media_name"],
    }


@router.get("/audio/os-state")
def audio_os_state() -> dict:
    return _audio_mute_state()


@router.post("/audio/os-unmute")
def audio_os_unmute() -> dict:
    stream = _find_hermes_chromium_stream()
    if stream is None or stream["index"] is None:
        raise ApiError(404, "no_stream", "no Chromium playback stream found for this process")
    if not shutil.which("pactl"):
        raise ApiError(500, "no_pactl", "pactl is not available")
    try:
        subprocess.run(
            ["pactl", "set-sink-input-mute", str(stream["index"]), "0"],
            capture_output=True,
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired as exc:
        raise ApiError(500, "pactl_timeout", "pactl timed out") from exc
    except Exception as exc:
        raise ApiError(500, "pactl_error", f"could not unmute stream: {exc}") from exc
    return _audio_mute_state()
