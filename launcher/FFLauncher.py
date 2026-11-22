"""FF SlowSeparation — Fast Fusion PC Recompiled Launcher & Setup Hub (SteelTemple)."""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from ff_dump import (
    DumpError,
    EXEFS_REQUIRED,
    discover_dump,
    extract_nup_overlay,
    find_extract_nup,
    load_expected_ff as load_expected_ff_dump,
    nro_to_nso,
    reuse_extracted_nup,
    rmtree_retry,
    sha256_file,
    verify_exefs_hashes,
)


APP_TITLE = "FF SlowSeparation"
APP_SUBTITLE = "Fast Fusion  ·  Native Windows PC Recompilation"
DEFAULT_VERSION = "Alpha 0.1.2"
GITHUB_URL = "https://github.com/STS78721/SlowSeparation"
RELEASES_URL = GITHUB_URL + "/releases"
RELEASES_API_URL = "https://api.github.com/repos/STS78721/SlowSeparation/releases/latest"
RELEASES_LIST_API_URL = "https://api.github.com/repos/STS78721/SlowSeparation/releases?per_page=10"
UPDATE_SKIP_TOP = {
    "exefs",
    "romfs",
    "romfs_packed",
    "generated",
    "spirv",
    "profile",
    "save",
    "logs",
    "shader-cache",
    "work",
    "mods",
}


# SteelTemple SlowSeparation palette (runtime/ui/theme.h): midnight indigo, hot magenta neon, cyan glints.
C_BG_DEEP = "#06051A"
C_BG = "#0B0A1F"
C_PANEL = "#110F2A"
C_RAISED = "#1A1740"
C_RAISED_HOVER = "#241F52"
C_LINE = "#342E60"
C_LINE_BRIGHT = "#5A4F9C"
C_TEXT = "#F6F4FF"
C_TEXT_SOFT = "#C0BAE2"
C_TEXT_DIM = "#847EB0"
C_NEON = "#FF2E88"
C_NEON_HOVER = "#FF5AA2"
C_NEON_DOWN = "#D81E6E"
C_NEON_TEXT = "#0B0A1F"
C_CYAN = "#40E0FF"
C_OK = "#4FE3B0"
C_WARN = "#FFB040"
C_BAD = "#FF5C6E"


def resource_base() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def read_app_version() -> str:
    for base in (app_dir(), resource_base(), resource_base().parent):
        for name in ("VERSION", "ff_version.txt"):
            p = base / name
            if p.is_file():
                txt = p.read_text(encoding="utf-8-sig", errors="ignore").strip()
                if txt:
                    return txt.splitlines()[0].strip()
    return DEFAULT_VERSION


def version_tuple(ver: str) -> tuple[tuple[int, ...], str]:
    m = re.search(r"(\d+(?:\.\d+)+)(?:([a-z])(?![a-z]))?", str(ver or "").strip().lower())
    if not m:
        return ((), "")
    nums = tuple(int(x) for x in m.group(1).split("."))
    return (nums, m.group(2) or "")


def is_same_version(ver1: str, ver2: str) -> bool:
    def norm(v: str) -> str:
        v = re.sub(r"^v", "", str(v or "").strip().lower())
        return re.sub(r"[^a-z0-9]", "", v)

    return norm(ver1) == norm(ver2)


def is_up_to_date(local_ver: str, remote_tag: str) -> bool:
    if is_same_version(local_ver, remote_tag):
        return True
    lv, rv = version_tuple(local_ver), version_tuple(remote_tag)
    return bool(lv[0] and rv[0] and lv >= rv)


def _find_update_asset(release: dict) -> tuple[str | None, str | None]:
    tag = (release.get("tag_name") or "").strip()
    raw_assets = release.get("assets") or []
    assets = raw_assets.get("links") or [] if isinstance(raw_assets, dict) else raw_assets
    tag_clean = tag.replace("-", "").replace(".", "")
    candidates = [
        f"SteelTemple-FF-SlowSeparation-{tag}-Update.zip",
        f"SteelTemple-FF-SlowSeparation-{tag_clean}-Update.zip",
        f"SteelTemple-FF-SlowSeparation-Update-{tag}.zip",
    ]
    asset = next(
        (
            item
            for item in assets
            for cand in candidates
            if item.get("name", "").casefold() == cand.casefold()
        ),
        None,
    )
    if not asset:
        asset = next(
            (
                item
                for item in assets
                if "update" in item.get("name", "").lower() and item.get("name", "").lower().endswith(".zip")
            ),
            None,
        )
    if not asset:
        return (None, None)
    return (asset.get("name"), asset.get("browser_download_url") or asset.get("url"))


def select_latest_release(releases: list[dict]) -> dict | None:
    """Pick newest non-draft release across regular releases and pre-releases."""
    candidates = [r for r in (releases or []) if isinstance(r, dict) and not r.get("draft")]
    if not candidates:
        return None

    def rank(idx_rel: tuple[int, dict]) -> tuple:
        idx, rel = idx_rel
        tag = (rel.get("tag_name") or "").strip()
        name = (rel.get("name") or "").strip()
        ver = version_tuple(tag)
        if not ver[0]:
            ver = version_tuple(name)
        _, asset_url = _find_update_asset(rel)
        return (bool(ver[0]), ver, 1 if asset_url else 0, -idx)

    return max(enumerate(candidates), key=rank)[1]


def find_running_ff_processes(root: Path) -> list[dict]:
    if os.name != "nt":
        return []
    target = str((root / "ff.exe").resolve()).replace("'", "''")
    command = (
        f"$target = '{target}'; "
        "$items = @(Get-CimInstance Win32_Process -Filter \\\"Name='ff.exe'\\\" -ErrorAction SilentlyContinue | "
        "Where-Object { $_.ExecutablePath -and "
        "[String]::Equals([IO.Path]::GetFullPath($_.ExecutablePath), $target, "
        "[StringComparison]::OrdinalIgnoreCase) } | "
        "Select-Object ProcessId, ExecutablePath); "
        "if ($items.Count -eq 1) { $items[0] | ConvertTo-Json -Compress } "
        "else { $items | ConvertTo-Json -Compress }"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            return []
        output = result.stdout.strip()
        if not output or output == "null":
            return []
        parsed = json.loads(output)
        if isinstance(parsed, dict):
            return [{"pid": parsed.get("ProcessId"), "path": parsed.get("ExecutablePath")}]
        if isinstance(parsed, list):
            return [
                {"pid": item.get("ProcessId"), "path": item.get("ExecutablePath")}
                for item in parsed
                if isinstance(item, dict)
            ]
    except Exception:
        pass
    return []


def game_process_warning(root: Path, action: str) -> str | None:
    processes = find_running_ff_processes(root)
    if not processes:
        return None
    details = []
    for process in processes:
        label = []
        if process.get("pid") is not None:
            label.append(f"PID {process['pid']}")
        if process.get("path"):
            label.append(str(process["path"]))
        details.append(" - ".join(label) if label else "ff.exe")
    return "Fast Fusion (ff.exe) is currently running:\n" + "\n".join(details) + f"\n\nClose the game before {action}."


def extract_update_archive(archive_path: Path, stage_dir: Path) -> list[str]:
    """Stage safe files from the official SlowSeparation Update.zip (flat or FF-SlowSeparation/ nested)."""
    archive_path, stage_dir = Path(archive_path), Path(stage_dir)
    selected: dict[str, zipfile.ZipInfo] = {}
    with zipfile.ZipFile(archive_path) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise ValueError(f"Update archive CRC check failed for {bad_member}.")
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            path = Path(name)
            parts = path.parts
            if parts and parts[0].casefold() == "ff-slowseparation":
                parts = parts[1:]
            if not parts or path.is_absolute() or ".." in parts:
                raise ValueError(f"Unexpected path in update archive: {info.filename}")
            top = parts[0].casefold()
            if top in UPDATE_SKIP_TOP or top.startswith("."):
                continue
            if parts[-1].casefold() in {"ff.ini", ".ds_store"}:
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise ValueError(f"Symbolic links are not allowed in updates: {info.filename}")
            # Empty files (e.g. Python package markers) are allowed.
            key = "/".join(parts).casefold()
            if key in selected:
                raise ValueError(f"Duplicate update file: {info.filename}")
            selected[key] = info

        launcher_keys = [k for k in selected if k.endswith("fflauncher.exe")]
        if not launcher_keys:
            raise ValueError("Update archive is missing required binary: FFLauncher.exe.")
        if selected[launcher_keys[0]].file_size < 1024 * 1024:
            raise ValueError("Update launcher executable is unexpectedly small.")

        written: list[str] = []
        for key, info in selected.items():
            parts = Path(info.filename.replace("\\", "/")).parts
            if parts and parts[0].casefold() == "ff-slowseparation":
                parts = parts[1:]
            rel = Path(*parts)
            target = stage_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            written.append(rel.as_posix())
        return written


def build_update_relauncher_script(
    game_dir: Path,
    stage_dir: Path,
    title: str,
    pid: int = 0,
    restart: bool = True,
    silent: bool = False,
) -> str:
    def ps_quote(value: object) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    game_path = ps_quote(Path(game_dir).resolve())
    stage_path = ps_quote(Path(stage_dir).resolve())
    title_text = ps_quote(title)
    wait_pid_line = f"Wait-Process -Id {int(pid)} -Timeout 15 -ErrorAction SilentlyContinue" if pid and pid > 0 else ""
    msg_err = (
        ""
        if silent
        else "try { Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show(('Update failed and changes were rolled back: ' + $_.Exception.Message), 'Update Failed') | Out-Null } catch {}\n"
    )
    msg_ok = (
        ""
        if silent
        else "try { Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show(($title + ' installed successfully. The launcher will restart.'), 'Update Complete') | Out-Null } catch {}\n"
    )
    restart_block = (
        "if (Test-Path -LiteralPath $targetLauncher -PathType Leaf) {\n"
        "    Start-Process -FilePath $targetLauncher -WorkingDirectory $game\n"
        "}\n"
        if restart
        else ""
    )
    return f"""
$ErrorActionPreference = 'Stop'
$game = {game_path}
$stage = {stage_path}
$title = {title_text}
$targetLauncher = Join-Path $game 'FFLauncher.exe'
$targetLauncherFull = [IO.Path]::GetFullPath($targetLauncher)
$logDir = Join-Path $game 'logs'
$logFile = Join-Path $logDir 'update.log'
function Write-UpdateLog([string]$msg) {{
    try {{
        if (-not (Test-Path -LiteralPath $logDir)) {{ New-Item -ItemType Directory -Force -Path $logDir | Out-Null }}
        Add-Content -LiteralPath $logFile -Value ('[' + (Get-Date -Format 's') + '] ' + $msg) -ErrorAction SilentlyContinue
    }} catch {{}}
}}
$rollback = Join-Path $stage '_rollback'
New-Item -ItemType Directory -Force -Path $rollback | Out-Null
$changed = [System.Collections.Generic.List[string]]::new()
$appliedCount = 0
Write-UpdateLog ('Applying update: ' + $title)
try {{
    {wait_pid_line}
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {{
        $alive = @(Get-CimInstance Win32_Process -Filter "Name='FFLauncher.exe'" -ErrorAction SilentlyContinue | Where-Object {{
            $_.ExecutablePath -and [String]::Equals([IO.Path]::GetFullPath($_.ExecutablePath), $targetLauncherFull, [StringComparison]::OrdinalIgnoreCase)
        }})
        if ($alive.Count -eq 0) {{ break }}
        Start-Sleep -Milliseconds 250
    }}
    Start-Sleep -Milliseconds 250
    Get-ChildItem -LiteralPath $stage -Recurse -File | ForEach-Object {{
        $name = $_.Name
        if ($name.EndsWith('.tmp') -or $name.EndsWith('.update-new')) {{ return }}
        if ($_.DirectoryName -eq $stage -and $name.EndsWith('.zip')) {{ return }}
        $rel = $_.FullName.Substring($stage.Length).TrimStart('\\','/')
        if ($rel.StartsWith('_rollback', [StringComparison]::OrdinalIgnoreCase)) {{ return }}
        $source = $_.FullName
        $target = Join-Path $game $rel
        $backup = Join-Path $rollback $rel
        $backupDir = Split-Path -Parent $backup
        if (-not (Test-Path -LiteralPath $backupDir)) {{ New-Item -ItemType Directory -Force -Path $backupDir | Out-Null }}
        $targetDir = Split-Path -Parent $target
        if (-not (Test-Path -LiteralPath $targetDir)) {{ New-Item -ItemType Directory -Force -Path $targetDir | Out-Null }}
        if (Test-Path -LiteralPath $target -PathType Leaf) {{ Copy-Item -LiteralPath $target -Destination $backup -Force }}
        $changed.Add($rel)
        $newFile = $target + '.update-new'
        for ($attempt = 1; $attempt -le 10; $attempt++) {{
            try {{
                Copy-Item -LiteralPath $source -Destination $newFile -Force
                Move-Item -LiteralPath $newFile -Destination $target -Force
                break
            }} catch {{
                if ($attempt -ge 10) {{ throw }}
                Start-Sleep -Milliseconds 300
            }}
        }}
        $appliedCount++
    }}
    Write-UpdateLog ('Applied ' + $appliedCount + ' files cleanly.')
}} catch {{
    Write-UpdateLog ('ERROR: ' + $_.Exception.Message + ' (rolling back ' + $changed.Count + ' files)')
    for ($i = $changed.Count - 1; $i -ge 0; $i--) {{
        $rel = $changed[$i]; $target = Join-Path $game $rel; $backup = Join-Path $rollback $rel
        if (Test-Path -LiteralPath $backup -PathType Leaf) {{ Copy-Item -LiteralPath $backup -Destination $target -Force -ErrorAction SilentlyContinue }}
        else {{ Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue }}
    }}
    {msg_err}exit 1
}}
{msg_ok}{restart_block}Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
"""


def find_icon_ico() -> Path | None:
    for p in (
        app_dir() / "ff.ico",
        app_dir() / "assets" / "ff.ico",
        app_dir() / "runtime" / "ff.ico",
        resource_base() / "assets" / "ff.ico",
        resource_base() / "ff.ico",
        app_dir().parent / "runtime" / "ff.ico",
    ):
        if p.is_file():
            return p
    return None


def find_icon_png() -> Path | None:
    for p in (
        app_dir() / "runtime" / "ff.png",
        resource_base() / "assets" / "ff.png",
        resource_base() / "ff.png",
        app_dir().parent / "runtime" / "ff.png",
    ):
        if p.is_file():
            return p
    return None


def find_craft_png() -> Path | None:
    for p in (
        app_dir() / "runtime" / "ff_craft.png",
        resource_base() / "assets" / "ff_craft.png",
        resource_base() / "ff_craft.png",
        app_dir().parent / "runtime" / "ff_craft.png",
    ):
        if p.is_file():
            return p
    return None


def set_windows_app_id() -> None:
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SteelTemple.FF.SlowSeparation")
        except Exception:
            pass


def parse_ini(path: Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path or not path.is_file():
        return values
    try:
        for raw in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            s = raw.strip()
            if not s or s.startswith(("#", ";", "[")) or "=" not in s:
                continue
            k, v = s.split("=", 1)
            values[k.strip().lower()] = v.split("#", 1)[0].split(";", 1)[0].strip()
    except OSError:
        pass
    return values


INI_SECTIONS: list[tuple[str, set[str]]] = [
    ("graphics", {"resolution", "aniso"}),
    (
        "display",
        {
            "fullscreen",
            "window_size",
            "output_res",
            "vk_present",
            "upscaler",
            "sharpness",
            "fg_mode",
            "perf_overlay",
            "color_preset",
        },
    ),
    ("frame rate", {"fps_limit"}),
    ("input", {"pad_layout", "stick_deadzone"}),
    ("audio", {"volume"}),
    ("performance", {"pipe_skip", "pipe_prebuild"}),
]


def save_ini_settings(path: Path, updates: dict[str, str]) -> None:
    path = Path(path)
    lines = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines(keepends=True) if path.is_file() else []
    for key, value in updates.items():
        k_low = key.casefold()
        sec = next((s for s, keys in INI_SECTIONS if k_low in keys), "")
        replaced = False
        if sec:
            active = ""
            for i, line in enumerate(lines):
                s = line.strip()
                if s.startswith("[") and s.endswith("]"):
                    active = s[1:-1].casefold()
                    continue
                if active != sec or s.startswith(("#", ";")) or "=" not in line:
                    continue
                if line.split("=", 1)[0].strip().casefold() == k_low:
                    lines[i] = f"{key} = {value}\n"
                    replaced = True
                    break
            if not replaced:
                sec_idx = next(
                    (i for i, l in enumerate(lines) if l.strip().casefold() == f"[{sec}]"),
                    -1,
                )
                if sec_idx >= 0:
                    lines.insert(sec_idx + 1, f"{key} = {value}\n")
                else:
                    if lines and lines[-1].strip():
                        lines.append("\n")
                    lines.append(f"[{sec}]\n")
                    lines.append(f"{key} = {value}\n")
        else:
            for i, line in enumerate(lines):
                s = line.lstrip()
                if s.startswith(("#", ";", "[")) or "=" not in line:
                    continue
                if line.split("=", 1)[0].strip().casefold() == k_low:
                    lines[i] = f"{key} = {value}\n"
                    replaced = True
                    break
            if not replaced:
                lines.append(f"{key} = {value}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".ini.tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    tmp.replace(path)


def clean_default_settings() -> dict[str, str]:
    """Safe, hardware-agnostic baseline defaults matching runtime/cfg.c."""
    return {
        "resolution": "original",
        "output_res": "1920x1080",
        "window_size": "1920x1080",
        "fullscreen": "0",
        "upscaler": "fsr",
        "sharpness": "80",
        "fg_mode": "off",
        "vk_present": "mailbox",
        "fps_limit": "60",
        "perf_overlay": "off",
        "aniso": "game",
        "color_preset": "default",
        "pad_layout": "auto",
        "stick_deadzone": "8",
        "volume": "100",
        "pipe_skip": "fullscreen",
        "pipe_prebuild": "1",
    }


def detect_gpu_name() -> str:
    if os.name != "nt":
        return "Vulkan Host GPU"
    try:
        r = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout.strip())
            names = [data] if isinstance(data, str) else list(data)
            names = [n for n in names if n]
            if names:
                names.sort(key=lambda n: (not any(k in n.lower() for k in ("nvidia", "geforce", "amd", "radeon")), n))
                return names[0]
    except Exception:
        pass
    return "Vulkan GPU (Auto)"


def resolve_root_dir() -> Path:
    root = app_dir()
    if (root / "ff.exe").is_file() or (root / "tools" / "build_game.py").is_file():
        return root
    if (root.parent / "tools" / "build_game.py").is_file():
        return root.parent
    return root


def load_expected_ff(root: Path) -> dict[str, str]:
    return load_expected_ff_dump(root, resource_bases=[resource_base(), resource_base().parent])


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]


def create_shortcut(game_dir: Path, name: str = "FF SlowSeparation") -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Windows shortcuts are only available on Windows."
    try:
        desktop = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"
        lnk = desktop / f"{name}.lnk"
        target = game_dir / ("FFLauncher.exe" if (game_dir / "FFLauncher.exe").is_file() else "ff.exe")
        ico = find_icon_ico() or target
        ps = (
            "$ws = New-Object -ComObject WScript.Shell; "
            f"$s = $ws.CreateShortcut('{str(lnk).replace(chr(39), chr(39)*2)}'); "
            f"$s.TargetPath = '{str(target).replace(chr(39), chr(39)*2)}'; "
            f"$s.WorkingDirectory = '{str(game_dir).replace(chr(39), chr(39)*2)}'; "
            f"$s.IconLocation = '{str(ico).replace(chr(39), chr(39)*2)},0'; "
            "$s.Description = 'Play Fast Fusion PC (FF SlowSeparation)'; "
            "$s.Save()"
        )
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if r.returncode == 0 and lnk.is_file():
            return True, str(lnk)
        return False, r.stderr.strip() or "Could not create desktop shortcut."
    except Exception as exc:
        return False, str(exc)


class FFLauncherApp(tk.Tk):
    def __init__(self, initial_tab: str = "play", shot_path: str = "", skip_update_check: bool = False) -> None:
        super().__init__()
        self.root_dir = resolve_root_dir()
        self.version = read_app_version()
        self.shot_path = shot_path
        self._skip_update_check = skip_update_check
        self.gpu_name = "Checking GPU..."

        self.title(f"{APP_TITLE} {self.version} — Fast Fusion PC")
        self.geometry("1040x700")
        self.minsize(920, 620)
        self.configure(bg=C_BG)

        self._icon_photo = None
        self._hero_photo = None
        self._craft_photo = None
        self._apply_window_icon()
        self._configure_ttk_styles()

        self.vars: dict[str, tk.StringVar] = {}
        defaults = clean_default_settings()
        for k, v in defaults.items():
            self.vars[k] = tk.StringVar(value=v)

        self.dump_root_var = tk.StringVar()
        self.exefs_var = tk.StringVar()
        self.romfs_var = tk.StringVar()
        self.shortcut_var = tk.BooleanVar(value=True)
        self.setup_status_var = tk.StringVar(value="Select your Fast Fusion 1.0.0 ExeFS & RomFS folders to build ff.exe locally.")
        self.setup_running = False
        self.refresh_running = False
        self._last_nup_extract: tuple[Path, Path, Path] | None = None
        self.current_tab = initial_tab
        self.latest_release: dict | None = None
        self.release_check_active = False
        self.update_in_progress = False
        self._auto_installing = False
        self.auto_install_updates = True

        self._build_layout()
        self._load_ini_into_vars()
        self._refresh_status_cards()
        self.select_tab(initial_tab)

        threading.Thread(target=self._probe_gpu_async, daemon=True).start()
        if self.shot_path:
            self.after(450, self._capture_shot_and_exit)
        else:
            self.after(250, self._auto_refresh_existing_build)
            if not getattr(self, "_skip_update_check", False):
                self.after(700, self.check_for_updates)

    def _apply_window_icon(self) -> None:
        ico = find_icon_ico()
        if ico and os.name == "nt":
            try:
                self.iconbitmap(default=str(ico))
            except Exception:
                pass
        png = find_icon_png() or ico
        if png and png.is_file():
            try:
                im = Image.open(png).convert("RGBA")
                self._icon_photo = ImageTk.PhotoImage(im.resize((48, 48), Image.Resampling.LANCZOS))
                self._hero_photo = ImageTk.PhotoImage(im.resize((72, 72), Image.Resampling.LANCZOS))
                self.iconphoto(True, self._icon_photo)
            except Exception:
                pass
        ball = find_craft_png()
        if ball and ball.is_file():
            try:
                bim = Image.open(ball).convert("RGBA")
                self._craft_photo = ImageTk.PhotoImage(bim.resize((82, 82), Image.Resampling.LANCZOS))
            except Exception:
                pass

    def _configure_ttk_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("FF.TCombobox", fieldbackground=C_RAISED, background=C_RAISED, foreground=C_TEXT,
                        bordercolor=C_LINE, arrowcolor=C_NEON, selectbackground=C_RAISED_HOVER,
                        selectforeground=C_NEON, padding=5)
        style.map("FF.TCombobox", fieldbackground=[("readonly", C_RAISED)], foreground=[("readonly", C_TEXT)])
        style.configure("FF.Horizontal.TProgressbar", troughcolor=C_BG_DEEP, background=C_NEON,
                        bordercolor=C_LINE, lightcolor=C_NEON, darkcolor=C_NEON_DOWN, thickness=12)

    def _probe_gpu_async(self) -> None:
        name = detect_gpu_name()
        self.after(0, lambda: self._set_gpu_label(name))

    def _set_gpu_label(self, name: str) -> None:
        self.gpu_name = name
        if hasattr(self, "gpu_label"):
            self.gpu_label.configure(text=name)

    def _ini_path(self) -> Path:
        return self.root_dir / "ff.ini"

    def _exe_path(self) -> Path:
        return self.root_dir / ("ff.exe" if os.name == "nt" else "ff")

    def _mods_dir(self) -> Path:
        return self.root_dir / "mods"

    def _profile_dir(self) -> Path:
        return self.root_dir / "profile"

    def _logs_dir(self) -> Path:
        return self.root_dir / "logs"

    def _load_ini_into_vars(self) -> None:
        defaults = clean_default_settings()
        ini = parse_ini(self._ini_path())
        for k, d in defaults.items():
            self.vars[k].set(ini.get(k, d))

    def _build_layout(self) -> None:
        # Top neon slipstream banner
        banner = tk.Canvas(self, height=116, bg=C_BG_DEEP, highlightthickness=0)
        banner.pack(fill=tk.X, side=tk.TOP)
        self.banner = banner
        banner.bind("<Configure>", self._draw_banner)

        # Navigation strip
        nav = tk.Frame(self, bg=C_PANEL, height=46, highlightbackground=C_LINE, highlightthickness=1)
        nav.pack(fill=tk.X, side=tk.TOP)
        nav.pack_propagate(False)
        self.nav_buttons: dict[str, tk.Button] = {}
        tabs = [
            ("play", "PLAY"),
            ("setup", "SETUP FROM DUMP"),
            ("settings", "GRAPHICS & INPUT"),
            ("mods", "MODS & SAVES"),
        ]
        for key, label in tabs:
            btn = tk.Button(
                nav,
                text=label,
                command=lambda k=key: self.select_tab(k),
                font=("Segoe UI", 9, "bold"),
                bg=C_PANEL,
                fg=C_TEXT_SOFT,
                activebackground=C_RAISED_HOVER,
                activeforeground=C_NEON,
                bd=0,
                padx=18,
                pady=10,
                cursor="hand2",
            )
            btn.pack(side=tk.LEFT)
            self.nav_buttons[key] = btn

        self.profile_badge = tk.Label(
            nav,
            text="",
            font=("Segoe UI", 8, "bold"),
            bg=C_RAISED,
            fg=C_NEON,
            padx=10,
            pady=4,
        )
        self.profile_badge.pack(side=tk.RIGHT, padx=14, pady=9)

        # Content container
        self.body = tk.Frame(self, bg=C_BG)
        self.body.pack(fill=tk.BOTH, expand=True, padx=22, pady=18)

        self.pages: dict[str, tk.Frame] = {
            "play": self._build_play_page(),
            "setup": self._build_setup_page(),
            "settings": self._build_settings_page(),
            "mods": self._build_mods_page(),
        }

        # Footer strip
        foot = tk.Frame(self, bg=C_BG_DEEP, height=34)
        foot.pack(fill=tk.X, side=tk.BOTTOM)
        foot.pack_propagate(False)
        tk.Label(
            foot,
            text=f"FF SlowSeparation  ·  {self.version}  ·  No copyrighted game assets included",
            font=("Segoe UI", 8),
            fg=C_TEXT_DIM,
            bg=C_BG_DEEP,
        ).pack(side=tk.LEFT, padx=16)
        tk.Label(
            foot,
            text=f"Folder: {self.root_dir}",
            font=("Segoe UI", 8),
            fg=C_TEXT_DIM,
            bg=C_BG_DEEP,
        ).pack(side=tk.RIGHT, padx=16)

    def _draw_banner(self, _event=None) -> None:
        c = self.banner
        c.delete("all")
        w = max(900, c.winfo_width())
        h = c.winfo_height()
        # Night-sky gradient in bands
        top, bot = (22, 18, 52), (6, 5, 18)
        bands = 12
        for i in range(bands):
            t = i / (bands - 1)
            col = "#%02X%02X%02X" % tuple(int(top[k] + (bot[k] - top[k]) * t) for k in range(3))
            c.create_rectangle(0, h * i / bands, w, h * (i + 1) / bands + 1, fill=col, outline="")
        # Slipstream: neon speed streaks, dim tails into bright heads
        rows = (0.18, 0.30, 0.44, 0.58, 0.72, 0.86)
        lens = (0.30, 0.46, 0.22, 0.52, 0.34, 0.26)
        offs = (0.40, 0.30, 0.55, 0.36, 0.50, 0.62)
        for k, (r, ln, of) in enumerate(zip(rows, lens, offs)):
            y = h * r
            x0, x1 = w * of, w * min(0.86, of + ln)
            base = C_NEON if k % 2 == 0 else C_CYAN
            tail = C_LINE_BRIGHT
            mid = x0 + (x1 - x0) * 0.55
            c.create_line(x0, y, mid, y, fill=tail, width=2)
            c.create_line(mid, y, x1, y, fill=base, width=2)
        # Start-line chevrons and a neon base line (magenta into cyan)
        for i in range(3):
            x = w - 300 + i * 22
            c.create_polygon(x, 28, x + 10, 28, x + 26, h / 2, x + 10, h - 28, x, h - 28, x + 16, h / 2,
                             fill=C_NEON if i == 2 else C_LINE_BRIGHT, outline="")
        c.create_line(0, h - 2, w / 2, h - 2, fill=C_NEON, width=3)
        c.create_line(w / 2, h - 2, w, h - 2, fill=C_CYAN, width=3)

        # The racer emblem on the right
        bx, by = w - 110, 58
        if self._craft_photo is not None:
            c.create_oval(bx - 34, by + 32, bx + 34, by + 44, fill="#03020C", outline="")
            c.create_image(bx, by, image=self._craft_photo)

        x_text = 26
        if self._hero_photo is not None:
            c.create_image(62, 58, image=self._hero_photo)
            x_text = 116
        c.create_text(x_text, 38, anchor="w", text=APP_TITLE, font=("Segoe UI", 22, "bold"), fill=C_TEXT)
        c.create_text(
            x_text + 2,
            70,
            anchor="w",
            text=f"{APP_SUBTITLE}  ·  {self.version}",
            font=("Segoe UI", 10),
            fill=C_NEON,
        )

    def select_tab(self, key: str) -> None:
        self.current_tab = key
        for k, page in self.pages.items():
            if k == key:
                page.pack(fill=tk.BOTH, expand=True)
            else:
                page.pack_forget()
        for k, btn in self.nav_buttons.items():
            if k == key:
                btn.configure(bg=C_RAISED_HOVER, fg=C_NEON)
            else:
                btn.configure(bg=C_PANEL, fg=C_TEXT_SOFT)

    def _card(self, parent: tk.Misc, title: str, padx: int = 16, pady: int = 14) -> tk.Frame:
        outer = tk.Frame(parent, bg=C_PANEL, highlightbackground=C_LINE, highlightthickness=1, padx=padx, pady=pady)
        if title:
            tk.Label(outer, text=title, font=("Segoe UI", 10, "bold"), fg=C_NEON, bg=C_PANEL).pack(anchor="w", pady=(0, 8))
        return outer

    def _accent_btn(self, parent: tk.Misc, text: str, cmd, width: int = 18) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=cmd,
            font=("Segoe UI", 10, "bold"),
            bg=C_NEON,
            fg=C_NEON_TEXT,
            activebackground=C_NEON_HOVER,
            activeforeground=C_NEON_TEXT,
            bd=0,
            padx=16,
            pady=9,
            width=width,
            cursor="hand2",
        )

    def _plain_btn(self, parent: tk.Misc, text: str, cmd, width: int = 16) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=cmd,
            font=("Segoe UI", 9, "bold"),
            bg=C_RAISED,
            fg=C_TEXT,
            activebackground=C_RAISED_HOVER,
            activeforeground=C_NEON,
            bd=0,
            padx=14,
            pady=8,
            width=width,
            cursor="hand2",
        )

    def _build_play_page(self) -> tk.Frame:
        page = tk.Frame(self.body, bg=C_BG)

        top = self._card(page, "RACE READINESS")
        top.pack(fill=tk.X, pady=(0, 14))
        self.build_state_lbl = tk.Label(top, text="", font=("Segoe UI", 11, "bold"), fg=C_TEXT, bg=C_PANEL)
        self.build_state_lbl.pack(anchor="w")
        self.build_hint_lbl = tk.Label(top, text="", font=("Segoe UI", 9), fg=C_TEXT_SOFT, bg=C_PANEL, wraplength=880, justify="left")
        self.build_hint_lbl.pack(anchor="w", pady=(4, 12))

        cta_row = tk.Frame(top, bg=C_PANEL)
        cta_row.pack(fill=tk.X)
        self.play_btn = self._accent_btn(cta_row, "PLAY FAST FUSION", self.launch_game, width=26)
        self.play_btn.pack(side=tk.LEFT)
        self.goto_setup_btn = self._plain_btn(cta_row, "OPEN DUMP SETUP", lambda: self.select_tab("setup"), width=20)
        self.goto_setup_btn.pack(side=tk.LEFT, padx=(10, 0))
        self._plain_btn(cta_row, "CREATE DESKTOP SHORTCUT", self.on_create_shortcut, width=24).pack(side=tk.RIGHT)

        upd = tk.Frame(top, bg=C_PANEL)
        upd.pack(fill=tk.X, pady=(14, 0))
        self.update_status = tk.Label(
            upd,
            text="Checking GitHub Releases…",
            font=("Segoe UI", 9),
            fg=C_TEXT_DIM,
            bg=C_PANEL,
            wraplength=880,
            justify="left",
        )
        self.update_status.pack(anchor="w")
        btn_row = tk.Frame(upd, bg=C_PANEL)
        btn_row.pack(fill=tk.X, pady=(6, 0))
        self.update_action_btn = self._accent_btn(btn_row, "INSTALL UPDATE", self.apply_latest_update, width=18)
        self.release_btn = self._plain_btn(btn_row, "VIEW RELEASE NOTES", self.open_latest_release, width=20)
        self._plain_btn(btn_row, "CHECK FOR UPDATES", self.check_for_updates, width=18).pack(side=tk.LEFT)
        # Hidden until an update is available / notes needed
        self.update_action_btn.pack_forget()
        self.release_btn.pack_forget()

        grid = tk.Frame(page, bg=C_BG)
        grid.pack(fill=tk.BOTH, expand=True)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        c1 = self._card(grid, "ACTIVE HARDWARE & PROFILE")
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self._kv_row(c1, "Graphics Adapter", "", attr="gpu_label")
        self._kv_row(c1, "Config File", "", attr="ini_state_label")
        self._kv_row(c1, "Internal Resolution", "", attr="res_summary_label")
        self._kv_row(c1, "Display / Upscaler", "", attr="upscaler_summary_label")
        self._kv_row(c1, "Frame Generation", "", attr="fg_summary_label")

        c2 = self._card(grid, "CLEAN CLIENT DEFAULTS")
        c2.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        notes = [
            ("Zero-Config Baseline", "Starts cleanly at 1920x1080 Windowed, Original internal (console tier), FSR sharpness 80, Frame Gen Off, 60 FPS."),
            ("Native 1:1 Rendering", "Races render at full size: Original, 720p, 900p, 1080p, 1440p, or 2160p (4K), with no half-res dynamic shrink. Pick it here or in-game (F1)."),
            ("No Personal Settings Bundled", "Your ff.ini and saves stay strictly local on your PC; updates never overwrite them."),
            ("Controls", "Supports Xbox, DualSense, Nintendo Switch / Switch 2 Pro controllers, plus Keyboard."),
        ]
        for head, body in notes:
            tk.Label(c2, text=head, font=("Segoe UI", 9, "bold"), fg=C_TEXT, bg=C_PANEL).pack(anchor="w", pady=(2, 0))
            tk.Label(c2, text=body, font=("Segoe UI", 8), fg=C_TEXT_DIM, bg=C_PANEL, wraplength=410, justify="left").pack(anchor="w", pady=(1, 6))

        return page

    def _kv_row(self, parent: tk.Misc, key: str, value: str, attr: str) -> None:
        row = tk.Frame(parent, bg=C_PANEL)
        row.pack(fill=tk.X, pady=4)
        tk.Label(row, text=key, font=("Segoe UI", 9), fg=C_TEXT_DIM, bg=C_PANEL).pack(side=tk.LEFT)
        lbl = tk.Label(row, text=value, font=("Segoe UI", 9, "bold"), fg=C_TEXT, bg=C_PANEL)
        lbl.pack(side=tk.RIGHT)
        setattr(self, attr, lbl)

    def _build_setup_page(self) -> tk.Frame:
        page = tk.Frame(self.body, bg=C_BG)

        card = self._card(page, "ONE-STEP SETUP FROM YOUR FAST FUSION 1.0.0 DUMP")
        card.pack(fill=tk.X, pady=(0, 12))
        tk.Label(
            card,
            text="No copyrighted game data is shipped with FF SlowSeparation. Select your dumped Switch 2 ExeFS and RomFS folders, "
            "a NUP-extracted dump tree (update alone or base + updates\\<ver>), or a .nup container. Setup verifies Fast Fusion 1.0.0 "
            "SHA-256 hashes, packs RomFS, lifts ARM64 modules, compiles ff.exe locally, then pre-warms every shader so the first race runs without stutter.",
            font=("Segoe UI", 9),
            fg=C_TEXT_SOFT,
            bg=C_PANEL,
            wraplength=920,
            justify="left",
        ).pack(anchor="w", pady=(0, 10))

        dump_row = tk.Frame(card, bg=C_PANEL)
        dump_row.pack(fill=tk.X, pady=3)
        tk.Label(
            dump_row,
            text="Dump Folder / .NUP (auto-detect):",
            width=38,
            anchor="w",
            font=("Segoe UI", 9),
            fg=C_TEXT_DIM,
            bg=C_PANEL,
        ).pack(side=tk.LEFT)
        tk.Entry(
            dump_row,
            textvariable=self.dump_root_var,
            bg=C_BG_DEEP,
            fg=C_TEXT,
            insertbackground=C_NEON,
            relief=tk.FLAT,
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 8), ipady=5)
        self.folder_btn = self._plain_btn(dump_row, "Folder...", self._pick_dump_root, width=9)
        self.folder_btn.pack(side=tk.RIGHT, padx=(4, 0))
        self.nup_btn = self._plain_btn(dump_row, ".NUP...", self._pick_nup_file, width=8)
        self.nup_btn.pack(side=tk.RIGHT)
        self.exefs_browse_btn = self._path_picker(card, "ExeFS Folder (all six modules, main … subsdk1):", self.exefs_var, self._pick_exefs)
        self.romfs_browse_btn = self._path_picker(card, "RomFS Folder (raw dump or romfs_packed):", self.romfs_var, self._pick_romfs)

        opt_row = tk.Frame(card, bg=C_PANEL)
        opt_row.pack(fill=tk.X, pady=(8, 6))
        tk.Checkbutton(
            opt_row,
            text="Create 'FF SlowSeparation' Desktop shortcut after build",
            variable=self.shortcut_var,
            bg=C_PANEL,
            fg=C_TEXT,
            activebackground=C_PANEL,
            activeforeground=C_NEON,
            selectcolor=C_BG_DEEP,
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)
        self.build_btn = self._accent_btn(opt_row, "VERIFY & BUILD FF.EXE", self.start_dump_setup, width=24)
        self.build_btn.pack(side=tk.RIGHT)

        prog_card = self._card(page, "BUILD PROGRESS & LOG")
        prog_card.pack(fill=tk.BOTH, expand=True)
        tk.Label(prog_card, textvariable=self.setup_status_var, font=("Segoe UI", 9, "bold"), fg=C_TEXT, bg=C_PANEL).pack(anchor="w", pady=(0, 6))
        self.setup_prog = ttk.Progressbar(prog_card, style="FF.Horizontal.TProgressbar", maximum=100, value=0)
        self.setup_prog.pack(fill=tk.X, pady=(0, 8))
        self.setup_log = tk.Text(
            prog_card,
            height=11,
            bg=C_BG_DEEP,
            fg=C_TEXT_SOFT,
            insertbackground=C_NEON,
            relief=tk.FLAT,
            font=("Consolas", 9),
        )
        self.setup_log.pack(fill=tk.BOTH, expand=True)
        return page

    def _path_picker(self, parent: tk.Misc, label: str, var: tk.StringVar, cmd) -> tk.Button:
        row = tk.Frame(parent, bg=C_PANEL)
        row.pack(fill=tk.X, pady=3)
        tk.Label(row, text=label, width=38, anchor="w", font=("Segoe UI", 9), fg=C_TEXT_DIM, bg=C_PANEL).pack(side=tk.LEFT)
        tk.Entry(row, textvariable=var, bg=C_BG_DEEP, fg=C_TEXT, insertbackground=C_NEON, relief=tk.FLAT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 8), ipady=5)
        btn = self._plain_btn(row, "Browse...", cmd, width=10)
        btn.pack(side=tk.RIGHT)
        return btn

    def _set_setup_busy(self, busy: bool) -> None:
        self.setup_running = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for attr in ("folder_btn", "nup_btn", "exefs_browse_btn", "romfs_browse_btn", "build_btn"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.configure(state=state)

    def _apply_dump_resolve(self, resolved, *, log: bool = True, update_status: bool = True) -> None:
        self.exefs_var.set(str(resolved.exefs))
        self.romfs_var.set(str(resolved.romfs))
        if log:
            for note in resolved.notes:
                self._log_setup(note)
        if update_status:
            self.setup_status_var.set("Dump paths resolved. Click VERIFY & BUILD FF.EXE when ready.")

    def _pick_dump_root(self) -> None:
        if self.setup_running:
            return
        d = filedialog.askdirectory(title="Select Fast Fusion Dump Root / NUP-extracted Folder")
        if not d:
            return
        self.dump_root_var.set(d)
        try:
            work = self.root_dir / "work" / "dump-resolve"
            work.mkdir(parents=True, exist_ok=True)
            resolved = discover_dump(Path(d), work)
            self.setup_log.delete("1.0", tk.END)
            self._apply_dump_resolve(resolved)
        except DumpError as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not auto-detect dump folders:\n{exc}", parent=self)

    def _resolve_or_extract_nup(
        self,
        nup: Path,
        *,
        exefs_hint: str = "",
        pct_lo: int = 2,
        pct_hi: int = 99,
    ):
        nup_out = self.root_dir / "work" / "nup-extract"
        resolved = reuse_extracted_nup(nup, nup_out)
        if resolved is None and exefs_hint:
            cand_parent = Path(exefs_hint).parent
            if cand_parent.name.startswith("nup-extract"):
                resolved = reuse_extracted_nup(nup, cand_parent)
        if resolved is not None:
            self.after(0, lambda: self._log_setup("Reusing extracted NUP tree (no re-extract needed)."))
            return resolved

        self.after(
            0,
            lambda name=nup.name: self.setup_status_var.set(
                f"Extracting {name} (base+update if present, else update alone)…"
            ),
        )

        def on_progress(line: str, pct: int | None) -> None:
            def _ui() -> None:
                self._log_setup(line)
                if pct is not None:
                    p = pct_lo + int((pct_hi - pct_lo) * max(0, min(100, pct)) / 100)
                    self.setup_prog.configure(value=p)
                    self.setup_status_var.set(f"Extracting {nup.name}… {pct}%")

            self.after(0, _ui)

        script = find_extract_nup(self.root_dir, resource_bases=[resource_base(), resource_base().parent])
        return extract_nup_overlay(
            nup,
            nup_out,
            python=self._find_setup_python(),
            extract_script=script,
            root=self.root_dir,
            on_progress=on_progress,
        )

    def _pick_nup_file(self) -> None:
        if self.setup_running:
            return
        path = filedialog.askopenfilename(
            title="Select Fast Fusion .NUP Container",
            filetypes=[("NX Package (NUP)", "*.nup"), ("All files", "*.*")],
        )
        if not path:
            return
        nup = Path(path)
        self.dump_root_var.set(str(nup))
        self.setup_log.delete("1.0", tk.END)
        self._set_setup_busy(True)
        self.setup_prog.configure(value=2)
        self.setup_status_var.set(f"Extracting {nup.name} (base+update if present, else update alone)…")
        self.update_idletasks()

        def worker() -> None:
            try:
                resolved = self._resolve_or_extract_nup(nup, pct_lo=2, pct_hi=99)
                self._last_nup_extract = (nup.resolve(), resolved.exefs.resolve(), resolved.romfs.resolve())
                self.after(
                    0,
                    lambda r=resolved: (
                        self.setup_prog.configure(value=100),
                        self._apply_dump_resolve(r),
                    ),
                )
            except Exception as exc:
                err = str(exc)
                self.after(
                    0,
                    lambda e=err: (
                        self.setup_prog.configure(value=0),
                        self.setup_status_var.set(f"NUP extract failed: {e}"),
                        self._log_setup(f"ERROR: {e}"),
                        messagebox.showerror(APP_TITLE, e, parent=self),
                    ),
                )
            finally:
                self.after(0, lambda: self._set_setup_busy(False))

        threading.Thread(target=worker, daemon=True).start()

    def _pick_exefs(self) -> None:
        if self.setup_running:
            return
        d = filedialog.askdirectory(title="Select ExeFS Folder (main, main.npdm, rtld, sdk)")
        if d:
            self.exefs_var.set(d)

    def _pick_romfs(self) -> None:
        if self.setup_running:
            return
        d = filedialog.askdirectory(title="Select RomFS or Packed RomFS Folder")
        if d:
            self.romfs_var.set(d)

    def _build_settings_page(self) -> tk.Frame:
        page = tk.Frame(self.body, bg=C_BG)

        cols = tk.Frame(page, bg=C_BG)
        cols.pack(fill=tk.BOTH, expand=True)
        cols.columnconfigure(0, weight=1)
        cols.columnconfigure(1, weight=1)

        left = self._card(cols, "DISPLAY & RESOLUTION")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self._combo_row(left, "Internal Resolution", "resolution", ["original", "720", "900", "1080", "1440", "2160"],
                        hint="original = console tier (1920x1080 docked). 1440 = 2560x1440, 2160 = 4K. Native 1:1, no DRS.")
        self._combo_row(left, "Output / Window Size", "output_res",
                        ["1280x720", "1600x900", "1920x1080", "2560x1440", "3840x2160"])
        self._combo_row(left, "Window Mode", "fullscreen", ["0", "1"], hint="0 = Windowed, 1 = Fullscreen")
        self._combo_row(left, "Upscaler", "upscaler", ["fsr", "bilinear", "off", "smrs"])
        self._combo_row(left, "FSR Sharpness (0-100)", "sharpness", ["0", "40", "60", "80", "90", "100"])
        self._combo_row(left, "V-Sync / Present", "vk_present", ["mailbox", "fifo", "immediate"])

        right = self._card(cols, "PERFORMANCE, COLOR & INPUT")
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self._combo_row(right, "Frame Generation", "fg_mode", ["off", "msm_fg", "fsr3_fg", "xefg"])
        self._combo_row(right, "FPS Limit", "fps_limit", ["60", "120", "144", "30"])
        self._combo_row(right, "Color Preset", "color_preset", ["default", "vivid", "clarity"])
        self._combo_row(right, "Perf Overlay", "perf_overlay", ["off", "fps", "full"])
        self._combo_row(right, "Anisotropic Filtering", "aniso", ["game", "2", "4", "8", "16"])
        self._combo_row(right, "Controller Button Layout", "pad_layout", ["auto", "nintendo", "xbox", "xbox_prompts"])

        bar = tk.Frame(page, bg=C_PANEL, highlightbackground=C_LINE, highlightthickness=1, padx=16, pady=12)
        bar.pack(fill=tk.X, pady=(12, 0))
        self._plain_btn(bar, "RESET TO DEFAULT PROFILE", self.on_reset_defaults, width=25).pack(side=tk.LEFT)
        self._accent_btn(bar, "SAVE SETTINGS", self.on_save_settings, width=18).pack(side=tk.RIGHT)
        self._plain_btn(bar, "RELOAD", self._reload_settings_ui, width=12).pack(side=tk.RIGHT, padx=(0, 10))
        return page

    def _combo_row(self, parent: tk.Misc, label: str, key: str, values: list[str], hint: str = "") -> None:
        wrap = tk.Frame(parent, bg=C_PANEL)
        wrap.pack(fill=tk.X, pady=4)
        top = tk.Frame(wrap, bg=C_PANEL)
        top.pack(fill=tk.X)
        tk.Label(top, text=label, font=("Segoe UI", 9, "bold"), fg=C_TEXT, bg=C_PANEL).pack(side=tk.LEFT)
        cb = ttk.Combobox(top, textvariable=self.vars[key], values=values, state="readonly", style="FF.TCombobox", width=18)
        cb.pack(side=tk.RIGHT)
        if hint:
            tk.Label(wrap, text=hint, font=("Segoe UI", 8), fg=C_TEXT_DIM, bg=C_PANEL).pack(anchor="w")

    def _build_mods_page(self) -> tk.Frame:
        page = tk.Frame(self.body, bg=C_BG)

        c_top = self._card(page, "GAME & DATA FOLDERS")
        c_top.pack(fill=tk.X, pady=(0, 12))
        row = tk.Frame(c_top, bg=C_PANEL)
        row.pack(fill=tk.X)
        self._plain_btn(row, "Game Folder", lambda: open_folder(self.root_dir), width=16).pack(side=tk.LEFT)
        self._plain_btn(row, "Saves / Profile", lambda: open_folder(self._profile_dir()), width=16).pack(side=tk.LEFT, padx=8)
        self._plain_btn(row, "Mods Folder", lambda: open_folder(self._mods_dir()), width=16).pack(side=tk.LEFT)
        self._plain_btn(row, "Logs Folder", lambda: open_folder(self._logs_dir()), width=16).pack(side=tk.LEFT, padx=8)

        c_bak = self._card(page, "SAVE BACKUP VAULT")
        c_bak.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            c_bak,
            text="Create a timestamped snapshot of your profile/save folder before installing updates or testing mods.",
            font=("Segoe UI", 9),
            fg=C_TEXT_SOFT,
            bg=C_PANEL,
        ).pack(anchor="w", pady=(0, 8))
        self.bak_list = tk.Listbox(c_bak, bg=C_BG_DEEP, fg=C_TEXT, selectbackground=C_RAISED_HOVER,
                                   selectforeground=C_NEON, highlightthickness=1, highlightbackground=C_LINE,
                                   font=("Consolas", 9), activestyle="none")
        self.bak_list.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        brow = tk.Frame(c_bak, bg=C_PANEL)
        brow.pack(fill=tk.X)
        self._accent_btn(brow, "CREATE SAVE SNAPSHOT", self.on_create_backup, width=22).pack(side=tk.LEFT)
        self._plain_btn(brow, "REFRESH LIST", self._refresh_backups, width=14).pack(side=tk.RIGHT)
        self._refresh_backups()
        return page

    def _refresh_backups(self) -> None:
        self.bak_list.delete(0, tk.END)
        bdir = self._profile_dir() / "backups"
        if bdir.is_dir():
            snaps = sorted((p.name for p in bdir.iterdir() if p.is_dir()), reverse=True)
            for s in snaps:
                self.bak_list.insert(tk.END, s)
        if self.bak_list.size() == 0:
            self.bak_list.insert(tk.END, "No save snapshots yet.")

    def on_create_backup(self) -> None:
        save_dir = self._profile_dir() / "save"
        if not save_dir.is_dir() or not any(save_dir.iterdir()):
            messagebox.showinfo(APP_TITLE, "No save data found yet under profile/save.", parent=self)
            return
        stamp = datetime.now().strftime("save_%Y%m%d_%H%M%S")
        dst = self._profile_dir() / "backups" / stamp
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(save_dir, dst)
        self._refresh_backups()
        messagebox.showinfo(APP_TITLE, f"Created save backup:\n{dst}", parent=self)

    def _refresh_status_cards(self) -> None:
        exe = self._exe_path()
        ready = exe.is_file()
        ini_exists = self._ini_path().is_file()

        if ready:
            self.build_state_lbl.configure(text="Ready to Race  (ff.exe found)", fg=C_OK)
            self.build_hint_lbl.configure(text=f"Executable: {exe}. Press PLAY below to launch Fast Fusion natively.")
            self.play_btn.configure(state=tk.NORMAL)
        else:
            self.build_state_lbl.configure(text="Setup Required  (no game files included)", fg=C_WARN)
            self.build_hint_lbl.configure(
                text="Open SETUP FROM DUMP to point FF SlowSeparation at your dumped Fast Fusion 1.0.0 ExeFS & RomFS folders."
            )
            self.play_btn.configure(state=tk.DISABLED)

        if ini_exists:
            self.profile_badge.configure(text="CUSTOM ff.ini ACTIVE")
            self.ini_state_label.configure(text="ff.ini (custom)", fg=C_NEON)
        else:
            self.profile_badge.configure(text="DEFAULT PROFILE ACTIVE")
            self.ini_state_label.configure(text="Clean Defaults (no ff.ini)", fg=C_OK)

        self.res_summary_label.configure(text=self.vars["resolution"].get())
        self.upscaler_summary_label.configure(text=f"{self.vars['output_res'].get()} · {self.vars['upscaler'].get().upper()}")
        self.fg_summary_label.configure(text=self.vars["fg_mode"].get())

    def _reload_settings_ui(self) -> None:
        self._load_ini_into_vars()
        self._refresh_status_cards()

    def on_save_settings(self) -> None:
        updates = {k: self.vars[k].get().strip() for k in clean_default_settings()}
        if updates["resolution"] == "original":
            # Ensure output window size also stays synchronized if left untouched
            updates["window_size"] = updates["output_res"]
        else:
            updates["window_size"] = updates["output_res"]
        save_ini_settings(self._ini_path(), updates)
        self._refresh_status_cards()
        messagebox.showinfo(APP_TITLE, f"Saved settings to {self._ini_path().name}.", parent=self)

    def on_reset_defaults(self) -> None:
        if self._ini_path().is_file():
            try:
                self._ini_path().unlink()
            except OSError as exc:
                messagebox.showerror(APP_TITLE, f"Could not delete ff.ini: {exc}", parent=self)
                return
        self._load_ini_into_vars()
        self._refresh_status_cards()
        messagebox.showinfo(
            APP_TITLE,
            "Restored the clean Zero-Config Default Profile (removed custom ff.ini).",
            parent=self,
        )

    def on_create_shortcut(self) -> None:
        ok, info = create_shortcut(self.root_dir)
        if ok:
            messagebox.showinfo(APP_TITLE, f"Created Desktop shortcut:\n{info}", parent=self)
        else:
            messagebox.showwarning(APP_TITLE, info, parent=self)

    def launch_game(self) -> None:
        exe = self._exe_path()
        if not exe.is_file():
            self.select_tab("setup")
            return
        try:
            subprocess.Popen([str(exe)], cwd=str(self.root_dir))
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Failed to start {exe.name}:\n{exc}", parent=self)

    def _log_setup(self, msg: str) -> None:
        self.setup_log.insert(tk.END, msg.rstrip() + "\n")
        self.setup_log.see(tk.END)

    def start_dump_setup(self) -> None:
        if self.setup_running:
            return
        dump_root = self.dump_root_var.get().strip()
        exefs_txt = self.exefs_var.get().strip()
        romfs_txt = self.romfs_var.get().strip()

        has_root = bool(dump_root and Path(dump_root).exists())
        has_pair = bool(exefs_txt and romfs_txt and Path(exefs_txt).is_dir() and Path(romfs_txt).is_dir())
        if not has_root and not has_pair:
            messagebox.showwarning(
                APP_TITLE,
                "Please select a dump folder / .nup, or both ExeFS and RomFS folders first.",
                parent=self,
            )
            return

        self.setup_log.delete("1.0", tk.END)
        self._set_setup_busy(True)
        self.setup_prog.configure(value=2)
        self.setup_status_var.set("Resolving Fast Fusion 1.0.0 dump...")
        threading.Thread(
            target=self._run_setup_worker,
            args=(dump_root, exefs_txt, romfs_txt),
            daemon=True,
        ).start()

    def _find_setup_python(self) -> Path:
        for cand in (
            self.root_dir / "python" / "python.exe",
            self.root_dir / "setup" / "python" / "python.exe",
            self.root_dir / ".venv" / "Scripts" / "python.exe",
        ):
            if cand.is_file():
                return cand
        return Path(sys.executable)

    def _run_step(self, cmd: list[str], env: dict[str, str], pct_start: int, pct_end: int) -> None:
        self.after(0, lambda: self._log_setup("+ " + " ".join(cmd)))
        proc = subprocess.Popen(
            cmd,
            cwd=str(self.root_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert proc.stdout is not None
        lines = 0
        for line in proc.stdout:
            lines += 1
            text = line.rstrip()
            prog = min(pct_end, pct_start + int((pct_end - pct_start) * min(1.0, lines / 60.0)))
            self.after(0, lambda t=text, p=prog: (self._log_setup(t), self.setup_prog.configure(value=p)))
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"Step failed with exit code {rc}: {' '.join(cmd[:3])}")

    def _build_game_cmd(
        self,
        py: Path,
        *,
        gen_dir: Path,
        build_out: Path,
        exefs: Path,
        packed_romfs: Path,
        expected_path: Path | None = None,
    ) -> list[str]:
        cmd = [
            str(py),
            str(self.root_dir / "tools" / "build_game.py"),
            "--version",
            "ff100",
            "--gen",
            str(gen_dir),
            "--out",
            str(build_out),
            "--exefs",
            str(exefs),
            "--romfs",
            str(packed_romfs),
            "--profile",
            str(self._profile_dir()),
        ]
        if expected_path is not None:
            cmd += ["--expected", str(expected_path)]
        rt_lib = self.root_dir / "runtime" / "libffrt.a"
        if rt_lib.is_file():
            cmd += ["--runtime", str(self.root_dir / "runtime"), "--runtime-lib", str(rt_lib)]
        llvm_dir = self.root_dir / "llvm"
        if llvm_dir.is_dir():
            cmd += ["--toolchain", str(llvm_dir), "--deps", str(self.root_dir / "runtime" / "deps")]
        ninja_bin = self.root_dir / "bin" / "ninja.exe"
        if ninja_bin.is_file():
            cmd += ["--ninja", str(ninja_bin)]
        return cmd

    def _run_setup_worker(self, dump_root: str, exefs_txt: str, romfs_txt: str) -> None:
        try:
            # Resolve dump root / .nup off the Tkinter UI thread.
            resolve_root: Path | None = None
            if dump_root:
                resolve_root = Path(dump_root)
            elif exefs_txt:
                exefs_path = Path(exefs_txt)
                if any(part.lower() == "updates" for part in exefs_path.parts):
                    for parent in exefs_path.parents:
                        if parent.name.lower() == "updates":
                            resolve_root = parent.parent
                            break

            if resolve_root is not None:
                resolved = None
                if resolve_root.is_file() and resolve_root.suffix.lower() == ".nup":
                    try:
                        resolved = self._resolve_or_extract_nup(
                            resolve_root,
                            exefs_hint=exefs_txt,
                            pct_lo=2,
                            pct_hi=16,
                        )
                    except DumpError as exc:
                        raise RuntimeError(str(exc)) from exc
                    self._last_nup_extract = (
                        resolve_root.resolve(),
                        resolved.exefs.resolve(),
                        resolved.romfs.resolve(),
                    )
                    self.after(0, lambda: self.setup_prog.configure(value=16))
                elif resolve_root.is_dir():
                    work = self.root_dir / "work" / "dump-resolve"
                    work.mkdir(parents=True, exist_ok=True)
                    try:
                        resolved = discover_dump(resolve_root, work)
                    except DumpError as exc:
                        raise RuntimeError(str(exc)) from exc

                if resolved is not None:
                    self.after(0, lambda r=resolved: self._apply_dump_resolve(r, log=True, update_status=False))
                    exefs_txt = str(resolved.exefs)
                    romfs_txt = str(resolved.romfs)

            exefs = Path(exefs_txt)
            romfs = Path(romfs_txt)
            if not exefs.is_dir() or not romfs.is_dir():
                raise RuntimeError("Please select a dump folder / .nup, or both ExeFS and RomFS folders first.")

            self.after(0, lambda: self.setup_status_var.set("1/5 Verifying Fast Fusion 1.0.0 ExeFS hashes..."))
            expected = load_expected_ff(self.root_dir)
            try:
                lines = verify_exefs_hashes(exefs, expected)
            except DumpError as exc:
                raise RuntimeError(str(exc)) from exc
            for line in lines:
                self.after(0, lambda t=line: self._log_setup(t))
            verified_msg = f"ExeFS SHA-256 verification passed ({len(expected)}/{len(expected)} files matched)."
            self.after(0, lambda m=verified_msg: (self._log_setup(m), self.setup_prog.configure(value=18)))

            # Keep rt_default_exefs inside self.root_dir/exefs so portable installs stay self-contained.
            local_exefs = self.root_dir / "exefs"
            if exefs.resolve() != local_exefs.resolve():
                local_exefs.mkdir(parents=True, exist_ok=True)
                keep_files = {p.name for p in exefs.iterdir() if p.is_file()}
                for old in list(local_exefs.iterdir()):
                    if old.is_file() and old.name not in keep_files:
                        old.unlink(missing_ok=True)
                copied: set[str] = set()
                for name in EXEFS_REQUIRED:
                    src = exefs / name
                    if src.is_file():
                        shutil.copy2(src, local_exefs / name)
                        copied.add(name)
                for src in exefs.iterdir():
                    if src.is_file() and src.name not in copied:
                        shutil.copy2(src, local_exefs / src.name)
                exefs = local_exefs
                self.after(
                    0,
                    lambda p=str(exefs): (
                        self.exefs_var.set(p),
                        self._log_setup(f"Staged verified ExeFS into {p}"),
                    ),
                )

            py = self._find_setup_python()
            env = dict(os.environ)
            for d in (self.root_dir / "python", self.root_dir / "llvm" / "bin", self.root_dir / "bin"):
                if d.is_dir():
                    env["PATH"] = str(d) + os.pathsep + env.get("PATH", "")

            # Step 2: RomFS pack or use already packed folder
            packed_romfs = self.root_dir / "romfs_packed"
            if (romfs / "index.json").is_file() and (romfs / "romfs.bin").is_file():
                packed_romfs = romfs
                self.after(0, lambda: (self._log_setup(f"Using pre-packed RomFS at {romfs}"),
                                       self.setup_prog.configure(value=40)))
            elif (packed_romfs / "index.json").is_file() and (packed_romfs / "romfs.bin").is_file():
                self.after(0, lambda: (self._log_setup(f"Using existing packed RomFS at {packed_romfs}"),
                                       self.setup_prog.configure(value=40)))
            else:
                self.after(0, lambda: self.setup_status_var.set("2/5 Packing Fast Fusion RomFS..."))
                packer = self.root_dir / "tools" / "pack_romfs.py"
                self._run_step([str(py), str(packer), "--dir", str(romfs), "--out", str(packed_romfs)], env, 20, 42)


            # Step 3: Lift ExeFS ARM64 -> C
            gen_dir = self.root_dir / "work" / "generated" / "ff100"
            gen_ready = (
                (gen_dir / "layout.json").is_file()
                and (gen_dir / "patches.h").is_file()
                and all(
                    (gen_dir / m / "manifest.json").is_file() and (gen_dir / m / f"{m}_table.c").is_file()
                    for m in ("main", "subsdk0", "subsdk1", "sdk")
                )
            )
            if gen_ready:
                self.after(0, lambda: (self._log_setup(f"Reusing existing lifted C in {gen_dir}"),
                                       self.setup_prog.configure(value=66)))
            else:
                self.after(0, lambda: self.setup_status_var.set("3/5 Lifting ARM64 code to native C..."))
                gen_script = self.root_dir / "recomp" / "gen.py"
                patches = self.root_dir / "recomp" / "patches-ff100.txt"
                self._run_step(
                    [str(py), str(gen_script), "--exefs", str(exefs), "--out", str(gen_dir), "--patches", str(patches),
                     "--modules", "main,subsdk0,subsdk1,sdk"],   # Fast Fusion links subsdk1 too (gen.py lifts 3 by default)
                    env,
                    56,
                    68,
                )

            # Step 4: Compile & link ff.exe into clean work/build/ff100 then place finished runtime files at root
            self.after(0, lambda: self.setup_status_var.set("4/5 Compiling & linking ff.exe..."))
            build_out = self.root_dir / "work" / "build" / "ff100"
            build_out.mkdir(parents=True, exist_ok=True)
            cmd = self._build_game_cmd(
                py,
                gen_dir=gen_dir,
                build_out=build_out,
                exefs=exefs,
                packed_romfs=packed_romfs,
            )
            self._run_step(cmd, env, 68, 88)

            for fname in (
                "ff.exe",
                "romfs-index.bin",
                "SDL3.dll",
                "libopus-0.dll",
                "libiconv-2.dll",
                "ns2_xlate.exe",
                "ns2_xlate.id",
                "Roboto-Medium.ttf",
                "LICENSE-Roboto.txt",
            ):
                src = build_out / fname
                if src.is_file():
                    shutil.copy2(src, self.root_dir / fname)
                elif fname in ("Roboto-Medium.ttf", "LICENSE-Roboto.txt"):
                    fallback = self.root_dir / "bin" / fname
                    if fallback.is_file() and not (self.root_dir / fname).is_file():
                        shutil.copy2(fallback, self.root_dir / fname)
            for patch_dir in ("romfs-patches", "romfs-patches-xbox"):
                src_dir = build_out / patch_dir
                dst_dir = self.root_dir / patch_dir
                if src_dir.is_dir():
                    shutil.rmtree(dst_dir, ignore_errors=True)
                    shutil.copytree(src_dir, dst_dir)

            # The shader translator ships in bin/; ff.exe looks for it beside itself (no translator = no shaders).
            self._sync_staged_bin_files()

            # Step 5: shader pre-warm (the MKW-THWOMP warm step, done the Fast Fusion way). prepare_shaders.py lists
            # every shader in this dump's ExeFS and RomFS, seeds its default variant, and runs ff.exe --prepare-shaders,
            # which translates each one into the shader cache and builds its pipeline libraries once, so the GPU
            # driver's own cache is warm before the first race. Not fatal: the game still translates on demand.
            self._prewarm_shaders(py, env, exefs, packed_romfs, 88, 99)

            if self.shortcut_var.get():
                create_shortcut(self.root_dir)
            self._write_runtime_stamp()

            self.after(
                0,
                lambda: (
                    self.setup_prog.configure(value=100),
                    self.setup_status_var.set("Setup complete! ff.exe is ready to play."),
                    self._refresh_status_cards(),
                    self.select_tab("play"),
                    messagebox.showinfo(APP_TITLE, "Fast Fusion PC setup finished cleanly! Press PLAY to start.", parent=self),
                ),
            )
        except Exception as exc:
            err = str(exc)
            self.after(
                0,
                lambda e=err: (
                    self.setup_prog.configure(value=0),
                    self.setup_status_var.set(f"Setup failed: {e}"),
                    self._log_setup(f"ERROR: {e}"),
                    messagebox.showerror(APP_TITLE, e, parent=self),
                ),
            )
        finally:
            self.after(0, lambda: self._set_setup_busy(False))

    WARM_SECONDS = 50

    def _prewarm_shaders(self, py: Path, env: dict[str, str], exefs: Path, packed_romfs: Path,
                         pct_start: int = 88, pct_end: int = 99) -> bool:
        """The MKW-THWOMP warm step, for Fast Fusion: ff.exe runs once (windowed, muted, on a scratch profile so the
        player's first start stays a first start) along tools/data/ff_warm_route.txt into the first race, recording
        every shader it meets (shader-programs.bin, shader-answers.bin, pipeline-keys.bin). ff.exe --prepare-shaders
        then translates those into the shader cache and builds their pipeline libraries once, so the GPU driver's own
        cache is warm too. Not fatal: the game still translates on demand."""
        exe = self._exe_path()
        route = self.root_dir / "tools" / "data" / "ff_warm_route.txt"
        if not exe.is_file() or not (self.root_dir / "ns2_xlate.exe").is_file():
            self.after(0, lambda: self._log_setup("Shader pre-warm skipped (ff.exe or ns2_xlate.exe missing)."))
            return False
        self.after(0, lambda: self.setup_status_var.set("5/5 Pre-warming shaders (a short muted test race)..."))
        self.after(0, lambda: self._log_setup(f"Warm run: ff.exe for {self.WARM_SECONDS} s on a scratch profile..."))
        warm_profile = self.root_dir / "work" / "warm-profile"
        rmtree_retry(warm_profile)
        wenv = dict(env, NS2_VOLUME="0", NS2_FULLSCREEN="0", NS2_WINDOW_SIZE="1280x720")
        if route.is_file():
            wenv["NS2_INPUT_SCRIPT"] = str(route)
        mid = pct_start + (pct_end - pct_start) * 3 // 4
        try:
            proc = subprocess.Popen(
                [str(exe), "--seconds", str(self.WARM_SECONDS), "--profile", str(warm_profile)],
                cwd=str(self.root_dir), env=wenv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            t0 = time.time()
            while proc.poll() is None:
                frac = min(1.0, (time.time() - t0) / (self.WARM_SECONDS + 15))
                self.after(0, lambda v=pct_start + int((mid - pct_start) * frac): self.setup_prog.configure(value=v))
                if time.time() - t0 > self.WARM_SECONDS + 90:
                    proc.kill()
                    raise RuntimeError("the warm run did not end")
                time.sleep(1)
            if proc.returncode != 0:
                raise RuntimeError(f"the warm run ended with exit code {proc.returncode}; see logs\\ff.log")
            for name in ("seed-programs.bin", "seed-answers.bin"):   # --prepare-shaders prefers a seed when present
                (self.root_dir / name).unlink(missing_ok=True)
            self.after(0, lambda: self.setup_status_var.set("5/5 Building the recorded shaders' pipelines..."))
            self._run_step([str(exe), "--prepare-shaders"], env, mid, pct_end)
        except Exception as exc:
            self.after(0, lambda e=str(exc): self._log_setup(f"Shader pre-warm did not finish ({e}); "
                                                              "the game translates the rest while you play."))
            return False
        finally:
            try:
                rmtree_retry(warm_profile)
            except OSError:
                pass
        self.after(0, lambda: self._log_setup("Shader pre-warm complete: shader cache and pipeline libraries are ready."))
        return True

    def _runtime_stamp(self) -> str:
        rt_lib = self.root_dir / "runtime" / "libffrt.a"
        if not rt_lib.is_file():
            return ""
        lib_hash = hashlib.sha256(rt_lib.read_bytes()).hexdigest()[:16]
        xlate_id = self.root_dir / "bin" / "ns2_xlate.id"
        xlate_txt = xlate_id.read_text(encoding="utf-8", errors="ignore").strip() if xlate_id.is_file() else ""
        return f"{self.version}|{rt_lib.stat().st_size}|{lib_hash}|{xlate_txt}"

    def _resolve_packed_romfs(self) -> Path | None:
        direct = self.root_dir / "romfs_packed"
        if (direct / "romfs.bin").is_file():
            return direct
        mods_c = self.root_dir / "work" / "build" / "ff100" / "modules.c"
        if mods_c.is_file():
            txt = mods_c.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'rt_default_romfs\[\]\s*=\s*"([^"]+)"', txt)
            if m:
                cand = Path(m.group(1).replace("\\\\", "\\"))
                if (cand / "romfs.bin").is_file():
                    return cand
        return None

    def _runtime_stamp_path(self) -> Path:
        return self.root_dir / "work" / "build" / "ff100" / ".ff_runtime_stamp"

    def _write_runtime_stamp(self) -> None:
        stamp = self._runtime_stamp()
        if not stamp:
            return
        sp = self._runtime_stamp_path()
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(stamp + "\n", encoding="utf-8")

    def _sync_staged_bin_files(self) -> None:
        if not self._exe_path().is_file():
            return
        src_exe = self.root_dir / "bin" / "ns2_xlate.exe"
        src_id = self.root_dir / "bin" / "ns2_xlate.id"
        if not src_exe.is_file() or not src_id.is_file():
            return
        dst_exe = self.root_dir / "ns2_xlate.exe"
        dst_id = self.root_dir / "ns2_xlate.id"
        try:
            src_bytes = src_id.read_bytes()
            if not dst_id.is_file() or not dst_exe.is_file() or dst_id.read_bytes() != src_bytes:
                shutil.copy2(src_exe, dst_exe)
                shutil.copy2(src_id, dst_id)
        except OSError:
            pass

    def _auto_refresh_existing_build(self) -> None:
        self._sync_staged_bin_files()
        if self.setup_running or self.refresh_running or self.update_in_progress:
            return
        exe = self._exe_path()
        exefs = self.root_dir / "exefs"
        packed_romfs = self._resolve_packed_romfs()
        gen_dir = self.root_dir / "work" / "generated" / "ff100"
        rt_lib = self.root_dir / "runtime" / "libffrt.a"
        if not (
            exe.is_file()
            and exefs.is_dir()
            and packed_romfs is not None
            and (gen_dir / "layout.json").is_file()
            and rt_lib.is_file()
        ):
            return
        want_stamp = self._runtime_stamp()
        if not want_stamp:
            return
        stamp_path = self._runtime_stamp_path()
        have_stamp = stamp_path.read_text(encoding="utf-8", errors="ignore").strip() if stamp_path.is_file() else ""
        stale = (have_stamp != want_stamp) or (rt_lib.stat().st_mtime > exe.stat().st_mtime + 1.0)
        if not stale:
            return
        if game_process_warning(self.root_dir, "updating ff.exe") is not None:
            return
        self.refresh_running = True
        self.play_btn.configure(state=tk.DISABLED)
        self.build_state_lbl.configure(text=f"Updating ff.exe for {self.version}…", fg=C_WARN)
        threading.Thread(
            target=self._run_refresh_worker,
            args=(exefs, packed_romfs, gen_dir, want_stamp, have_stamp),
            daemon=True,
        ).start()

    def _run_refresh_worker(
        self, exefs: Path, packed_romfs: Path, gen_dir: Path, want_stamp: str, have_stamp: str = ""
    ) -> None:
        err: str | None = None
        try:
            py = self._find_setup_python()
            env = dict(os.environ)
            for d in (self.root_dir / "python", self.root_dir / "llvm" / "bin", self.root_dir / "bin"):
                if d.is_dir():
                    env["PATH"] = str(d) + os.pathsep + env.get("PATH", "")
            build_out = self.root_dir / "work" / "build" / "ff100"
            build_out.mkdir(parents=True, exist_ok=True)
            cmd = self._build_game_cmd(
                py,
                gen_dir=gen_dir,
                build_out=build_out,
                exefs=exefs,
                packed_romfs=packed_romfs,
            )
            proc = subprocess.run(
                cmd,
                cwd=str(self.root_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode != 0:
                tail = "\n".join((proc.stdout or "").strip().splitlines()[-8:])
                raise RuntimeError(f"Runtime update build failed (exit {proc.returncode}):\n{tail}")
            for fname in ("ff.exe", "ns2_xlate.exe", "ns2_xlate.id", "SDL3.dll", "libopus-0.dll"):
                src = build_out / fname
                if src.is_file():
                    shutil.copy2(src, self.root_dir / fname)
            self._sync_staged_bin_files()
            old_xlate = have_stamp.rsplit("|", 1)[-1].strip() if "|" in have_stamp else ""
            new_xlate = want_stamp.rsplit("|", 1)[-1].strip() if "|" in want_stamp else ""
            can_reuse_shaders = (
                bool(old_xlate)
                and old_xlate == new_xlate
                and (self.root_dir / "shader-programs.bin").is_file()
                and (self.root_dir / "pipeline-keys.bin").is_file()
            )
            if can_reuse_shaders:
                subprocess.run(
                    [str(self._exe_path()), "--prepare-shaders"],
                    cwd=str(self.root_dir),
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                # An update can bring a new shader translator, which starts a new cache: warm it again.
                self._prewarm_shaders(py, env, exefs, packed_romfs, 0, 100)
            stamp_path = self._runtime_stamp_path()
            stamp_path.parent.mkdir(parents=True, exist_ok=True)
            stamp_path.write_text(want_stamp + "\n", encoding="utf-8")
        except Exception as exc:
            err = str(exc)
        finally:
            self.after(0, lambda e=err: self._finish_refresh_worker(e))

    def _finish_refresh_worker(self, err: str | None) -> None:
        self.refresh_running = False
        self._refresh_status_cards()
        if err:
            self.build_hint_lbl.configure(text=err.splitlines()[0], fg=C_BAD)
        else:
            self.build_hint_lbl.configure(text=f"Updated {self._exe_path().name} to {self.version}. Press PLAY to launch.")

    def check_for_updates(self) -> None:
        if getattr(self, "release_check_active", False) or getattr(self, "update_in_progress", False):
            return
        self.release_check_active = True
        self.update_action_btn.pack_forget()
        self.release_btn.pack_forget()
        self.update_status.config(text="Checking GitHub Releases…", fg=C_TEXT_DIM)
        threading.Thread(target=self._check_release_worker, daemon=True).start()

    def _check_release_worker(self) -> None:
        try:
            headers = {"Accept": "application/vnd.github+json", "User-Agent": "FFLauncher"}
            data: dict | None = None
            try:
                req = urllib.request.Request(RELEASES_LIST_API_URL, headers=headers)
                with urllib.request.urlopen(req, timeout=12) as r:
                    releases = json.loads(r.read().decode("utf-8"))
                if isinstance(releases, list):
                    if not releases:
                        self.after(
                            0,
                            lambda: self._show_release_result(
                                self.version, f"Your FF SlowSeparation is up to date ({self.version}).", RELEASES_URL, None, None
                            ),
                        )
                        return
                    data = select_latest_release(releases)
                    if data is None:
                        self.after(
                            0,
                            lambda: self._show_release_result(
                                self.version, f"Your FF SlowSeparation is up to date ({self.version}).", RELEASES_URL, None, None
                            ),
                        )
                        return
            except Exception:
                data = None
            if data is None:
                req = urllib.request.Request(RELEASES_API_URL, headers=headers)
                with urllib.request.urlopen(req, timeout=12) as r:
                    data = json.loads(r.read().decode("utf-8"))
            tag = (data.get("tag_name") or "").strip()
            title = (data.get("name") or tag or "Latest FF SlowSeparation release").strip()
            page = data.get("html_url") or (f"{GITHUB_URL}/releases/tag/{tag}" if tag else RELEASES_URL)
            if not tag:
                raise ValueError("The latest release did not include a version tag.")
            asset_name, asset_url = _find_update_asset(data)
            self.after(0, lambda: self._show_release_result(tag, title, page, asset_url, asset_name))
        except Exception as exc:
            detail = str(exc)
            if "404" in detail or "Not Found" in detail:
                self.after(
                    0,
                    lambda: self._show_release_result(
                        self.version, f"Your FF SlowSeparation is up to date ({self.version}).", RELEASES_URL, None, None
                    ),
                )
            else:
                self.after(0, lambda: self._show_release_error("Update check failed: " + detail))

    def _show_release_error(self, message: str) -> None:
        self.release_check_active = False
        self.update_status.config(text=message, fg=C_BAD)

    def _show_release_result(
        self, tag: str, title: str, page: str, asset_url: str | None = None, asset_name: str | None = None
    ) -> None:
        self.release_check_active = False
        self.latest_release = {
            "tag": tag,
            "title": title,
            "page": page,
            "asset_url": asset_url,
            "asset_name": asset_name,
        }
        clean_title = title.split(":", 1)[0].strip()
        if (
            is_up_to_date(self.version, tag)
            or is_same_version(self.version, clean_title)
            or (not version_tuple(tag)[0] and is_up_to_date(self.version, clean_title))
        ):
            self.update_status.config(text=f"Your FF SlowSeparation is up to date ({self.version})", fg=C_OK)
            self.update_action_btn.pack_forget()
            self.release_btn.pack_forget()
            return
        if not asset_url:
            self.update_status.config(text=f"Update found ({clean_title}), but Update.zip is missing.", fg=C_WARN)
            self.update_action_btn.pack_forget()
            self.release_btn.config(text="VIEW RELEASE NOTES")
            self.release_btn.pack(side=tk.LEFT, padx=(10, 0))
            return
        self.update_status.config(text=f"Update available: {clean_title} — installing…", fg=C_WARN)
        self.update_action_btn.config(state=tk.DISABLED, text="AUTO-INSTALLING…")
        self.update_action_btn.pack(side=tk.LEFT, padx=(10, 0))
        self.release_btn.config(text="VIEW RELEASE NOTES")
        self.release_btn.pack(side=tk.LEFT, padx=(10, 0))
        if self.auto_install_updates:
            self.after(400, lambda: self.apply_latest_update(auto=True))

    def open_latest_release(self) -> None:
        url = self.latest_release["page"] if (self.latest_release and self.latest_release.get("page")) else RELEASES_URL
        webbrowser.open(url)

    def apply_latest_update(self, auto: bool = False) -> None:
        if (
            getattr(self, "update_in_progress", False)
            or getattr(self, "refresh_running", False)
            or getattr(self, "setup_running", False)
            or not self.latest_release
        ):
            return
        warning = game_process_warning(self.root_dir, "applying a launcher/game update")
        if warning:
            messagebox.showwarning("Close the Game First", warning, parent=self)
            return
        title = self.latest_release.get("title", "FF SlowSeparation Update")
        if not auto and not messagebox.askyesno(
            "Install FF SlowSeparation Update",
            f"Download and install {title} now?\n\nThis updates FFLauncher and toolchain files. "
            "Your dump, saves, and personal ff.ini stay local.",
            parent=self,
        ):
            return
        if not self.latest_release.get("asset_url") or not self.latest_release.get("asset_name"):
            self.update_status.config(text="Update.zip download URL is missing.", fg=C_BAD)
            return
        self.update_in_progress = True
        self._auto_installing = bool(auto)
        self.update_action_btn.config(state=tk.DISABLED, text="DOWNLOADING UPDATE…")
        self.update_status.config(text="Connecting to GitHub Releases…", fg=C_TEXT_DIM)
        threading.Thread(target=self._download_update_worker, args=(self.latest_release.copy(),), daemon=True).start()

    def _download_update_worker(self, release: dict) -> None:
        stage = None
        try:
            target = self.root_dir
            target.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=".ff-update-", dir=str(target)))
            archive_path = stage / release["asset_name"]
            request = urllib.request.Request(
                release["asset_url"],
                headers={"User-Agent": "FFLauncher", "Accept": "application/octet-stream"},
            )
            with urllib.request.urlopen(request, timeout=120) as response, archive_path.open("wb") as output:
                total = int(response.headers.get("Content-Length", "0") or 0)
                downloaded = 0
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    downloaded += len(block)
                    if total:
                        percent = min(100, downloaded * 100 // total)
                        self._set_update_status(f"Downloading update… {percent}%", C_TEXT_DIM)
            self._set_update_status("Validating update archive…", C_TEXT_DIM)
            files = extract_update_archive(archive_path, stage)
            archive_path.unlink(missing_ok=True)
            lower = {f.casefold() for f in files}
            if "fflauncher.exe" not in lower:
                raise ValueError("The validated update archive is missing FFLauncher.exe.")
            self.after(0, lambda: self._update_ready(release, stage, files))
        except Exception as exc:
            if stage:
                shutil.rmtree(stage, ignore_errors=True)
            detail = str(exc)
            self.after(0, lambda: self._update_failed(detail))

    def _set_update_status(self, text: str, color: str) -> None:
        try:
            self.after(0, lambda: self.update_status.config(text=text, fg=color))
        except tk.TclError:
            pass

    def _update_failed(self, detail: str) -> None:
        self.update_in_progress = False
        self.update_action_btn.config(state=tk.NORMAL, text="INSTALL UPDATE")
        self.update_status.config(text=f"Update failed:\n{detail[:160]}", fg=C_BAD)

    def _update_ready(self, release: dict, stage: Path, files: list[str]) -> None:
        title = release.get("title", "FF SlowSeparation Update")
        auto = getattr(self, "_auto_installing", False)
        if not auto and not messagebox.askyesno(
            "Update Ready",
            f"{title} is downloaded and verified.\n\nInstall it now? The launcher will close, apply the files, and restart automatically.",
            parent=self,
        ):
            shutil.rmtree(stage, ignore_errors=True)
            self.update_in_progress = False
            self.update_action_btn.config(state=tk.NORMAL, text="INSTALL UPDATE")
            self.update_status.config(text="Update downloaded; installation cancelled.", fg=C_TEXT_DIM)
            return
        try:
            self._start_update_relauncher(stage, title)
            os._exit(0)
        except Exception as exc:
            shutil.rmtree(stage, ignore_errors=True)
            self._update_failed(str(exc))

    def _start_update_relauncher(self, stage: Path, title: str) -> None:
        script = build_update_relauncher_script(self.root_dir, stage, title, pid=os.getpid(), restart=True, silent=False)
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        flags = getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-EncodedCommand",
                encoded,
            ],
            cwd=str(self.root_dir),
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _capture_shot_and_exit(self) -> None:
        try:
            self.update_idletasks()
            self.update()
            out_path = Path(self.shot_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                hwnd = self.winfo_id()
                # Parent top-level HWND for decorated frame
                parent_hwnd = ctypes.windll.user32.GetParent(hwnd) or hwnd
                rect = (ctypes.c_long * 4)()
                ctypes.windll.user32.GetWindowRect(parent_hwnd, rect)
                w = max(1, int(rect[2] - rect[0]))
                h = max(1, int(rect[3] - rect[1]))
                hdc_win = ctypes.windll.user32.GetWindowDC(parent_hwnd)
                hdc_mem = ctypes.windll.gdi32.CreateCompatibleDC(hdc_win)
                hbmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc_win, w, h)
                old = ctypes.windll.gdi32.SelectObject(hdc_mem, hbmp)
                ctypes.windll.user32.PrintWindow(parent_hwnd, hdc_mem, 2)  # PW_RENDERFULLCONTENT
                class BITMAPINFOHEADER(ctypes.Structure):
                    _fields_ = [
                        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                        ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                        ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32),
                    ]
                bmi = BITMAPINFOHEADER()
                bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.biWidth = w
                bmi.biHeight = -h
                bmi.biPlanes = 1
                bmi.biBitCount = 32
                bmi.biCompression = 0
                buf = (ctypes.c_ubyte * (w * h * 4))()
                ctypes.windll.gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
                ctypes.windll.gdi32.SelectObject(hdc_mem, old)
                ctypes.windll.gdi32.DeleteObject(hbmp)
                ctypes.windll.gdi32.DeleteDC(hdc_mem)
                ctypes.windll.user32.ReleaseDC(parent_hwnd, hdc_win)
                img = Image.frombuffer("RGBA", (w, h), bytes(buf), "raw", "BGRA", 0, 1)
                img.save(out_path)
            else:
                from PIL import ImageGrab
                x, y, w, h = self.winfo_rootx(), self.winfo_rooty(), self.winfo_width(), self.winfo_height()
                ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(out_path)
        finally:
            self.after(40, self.destroy)


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--tab", default="play", choices=["play", "setup", "settings", "mods"])
    ap.add_argument("--shot", default="", help="Capture launcher window screenshot and exit")
    ap.add_argument("--check", action="store_true", help="Verify clean startup & exit")
    args = ap.parse_args()

    set_windows_app_id()
    app = FFLauncherApp(initial_tab=args.tab, shot_path=args.shot, skip_update_check=args.check)
    if args.check:
        app.update_idletasks()
        app.destroy()
        print("FFLauncher check: OK")
        return 0
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
