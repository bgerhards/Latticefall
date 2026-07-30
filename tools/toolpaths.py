#!/usr/bin/env python3
"""
Resolve Godot and Blender across the machines this project has actually run on, and know
how to launch Godot without ever putting a window on the owner's desktop.

The dev machine that first authored `tools/check.py` and `tools/test_parity.py` was macOS,
so both files hardcoded `/Applications/Godot.app/Contents/MacOS/Godot`. This project also
runs from WSL2 Linux. Two Godot builds have existed there at different times: a native
Windows binary (`Godot_v4.7.1-stable_win64_console.exe`, launched through WSL interop) and,
now, a native Linux binary (`Godot_v4.7.1-stable_linux.x86_64`). The Windows build needs
every filesystem path translated (`/mnt/d/dev/Latticefall` -> `D:/dev/Latticefall`) because
a Windows process cannot resolve a WSL path, while the Linux build needs no translation at
all — it *is* a Linux process. This module is the one place that knows both the "where is
the binary" question and the "which paths need translating" question, so the checks
themselves stay platform-agnostic.

**The Linux build is preferred over the Windows build on this machine, and that preference
is deliberate, not incidental.** GL Compatibility reads back nothing under a truly headless
`--headless` flag (probed for LF-061) — it needs a real GPU-backed window to present a frame
before `RenderingServer.frame_post_draw` resolves. On Windows or macOS that means an actual
window on the actual desktop, which steals focus and — worse — can be throttled by the OS if
something covers or backgrounds it, which is what turned one `game renders` check into a
36-minute stall that still reported `ok` (LF-061, decision 051). A native Linux Godot has no
such problem: pointed at an `Xvfb` virtual framebuffer via `xvfb-run`, it opens a real,
GPU-backed (well, Mesa llvmpipe software-GL-backed) window that nothing on the owner's
screen ever renders — there is no compositor and nothing to occlude. This is not the
`--quiet-window` off-screen-positioning trick in `display_settings.gd` (which still parks a
window on a real desktop, just off every monitor); it is a window that never touches a
desktop compositor at all, which is what makes it strictly invisible rather than merely out
of the way. `godot_argv()` below is what wires this up, and `tools/shot.py` is the intended
caller. This supersedes the occlusion workaround (`--no-window`, `--quiet-window`) as the
default path — those remain available as a speed option and a Windows/macOS fallback, not as
the only way to get a frame without disturbing the owner.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
from glob import glob
from pathlib import Path

_MACOS_GODOT = "/Applications/Godot.app/Contents/MacOS/Godot"
_MACOS_BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"

# A native Linux Godot build, the preferred resolution on this machine because only it can
# be driven invisibly (see module docstring). `/mnt/*/godot/linux/...` covers this machine
# (D:\godot\linux); listed first because a glob returns sorted hits and this is meant to be
# the only pattern that matters here.
_LINUX_GODOT_GLOBS = [
    "/mnt/*/godot/linux/Godot_v*_linux.x86_64",
]

# Directories worth globbing for a Windows Godot build when nothing more specific matched.
# `/mnt/*/godot/...` covers this machine (D:\godot); the rest are common install spots.
# Checked *after* the Linux build and PATH and the macOS bundle: a Windows exe can only be
# driven with a real, visible window through WSL interop, so it is the last resort here,
# not the first.
_WIN_GODOT_GLOBS = [
    "/mnt/*/godot/Godot_v*_win64_console.exe",
    "/mnt/*/Godot/Godot_v*_win64_console.exe",
    "/mnt/*/Program Files/Godot/Godot_v*_win64_console.exe",
]

# Windows Blender installs reachable through WSL's `/mnt/<drive>/...`. This machine has
# Blender only as a Windows install (`/mnt/d/Program Files/Blender Foundation/Blender
# 5.2/blender.exe`) — there is no Linux or WSL-native Blender build to prefer the way there
# is for Godot, so this is the fallback rather than a second-class option.
_WIN_BLENDER_GLOBS = [
    "/mnt/*/Program Files/Blender Foundation/Blender */blender.exe",
    "/mnt/*/Program Files (x86)/Blender Foundation/Blender */blender.exe",
]

# What `xvfb-run` hands `Xvfb` for the virtual framebuffer's screen. 1600x900x24 is
# generously larger than anything this project screenshots (max logical viewport 1920x1080
# at 100% scale, scaled by the 0.75 window/viewport ratio — see CLAUDE.md), so nothing gets
# clipped by the virtual screen itself.
_XVFB_SCREEN_ARGS = ["-a", "-s", "-screen 0 1600x900x24"]


def _first_existing(paths: list[str]) -> str | None:
    for p in paths:
        if Path(p).exists():
            return p
    return None


def _first_glob(patterns: list[str]) -> str | None:
    for pattern in patterns:
        hits = sorted(glob(pattern))
        if hits:
            return hits[0]
    return None


@functools.lru_cache(maxsize=1)
def godot() -> str | None:
    """Resolve the Godot binary, or None if nothing on this machine has it.

    Resolution order: `$LF_GODOT` (explicit override) -> a native Linux build under
    `/mnt/*/godot/linux/Godot_v*_linux.x86_64` -> `godot`/`godot4` on PATH -> the macOS app
    bundle -> a Windows console build reachable through WSL's `/mnt/<drive>/...`.

    The Linux build is deliberately checked *before* PATH and the macOS bundle would be
    checked on a machine that had both, and well before the Windows exe: it is the only
    build that can be driven with `godot_argv(..., want_window=False)` without a real window
    landing on the owner's desktop (see module docstring). Preferring it is not a style
    choice — the Windows exe glob exists only as a fallback for a machine with no Linux
    build installed.

    Cached: this is re-checked once per `host_path()` call in the parity check's inner loop
    (hundreds of anchor x policy x difficulty combinations), and the filesystem glob is the
    slow part of resolving it.
    """
    env = os.environ.get("LF_GODOT")
    if env and Path(env).exists():
        return env

    linux = _first_glob(_LINUX_GODOT_GLOBS)
    if linux:
        return linux

    which = shutil.which("godot") or shutil.which("godot4")
    if which:
        return which

    if Path(_MACOS_GODOT).exists():
        return _MACOS_GODOT

    return _first_glob(_WIN_GODOT_GLOBS)


def blender() -> str | None:
    """Resolve the Blender binary, or None.

    Resolution order: `$LF_BLENDER` -> `blender` on PATH -> the macOS app bundle -> the
    Windows install reachable through WSL's `/mnt/<drive>/...`. Unlike `godot()`, there is
    no Linux build to prefer here — this project's Blender pipeline runs (by hand, or via
    the sprite-smith skill) as `blender -b --python tools/blender/render.py -- ...`, i.e.
    Blender is always the *parent* process, never something Python launches as a subprocess.
    So `is_windows_exe(blender())` matters only if some future caller starts shelling out to
    launch Blender itself — as of this writing nothing under `tools/` does; `render.py`,
    `mask_glow.py` and `pack_atlas.py` all run as scripts *inside* an already-running
    Blender or as plain Python over its output, and none of them resolve or invoke the
    Blender binary. A caller that does start launching Blender as a subprocess must run its
    path-bearing argv (`--python <script>`, any `-- <args>` that carry filesystem paths)
    through `host_path()`, exactly like Godot.
    """
    env = os.environ.get("LF_BLENDER")
    if env and Path(env).exists():
        return env

    which = shutil.which("blender")
    if which:
        return which

    if Path(_MACOS_BLENDER).exists():
        return _MACOS_BLENDER

    return _first_glob(_WIN_BLENDER_GLOBS)


def is_windows_exe(path: str) -> bool:
    """True when `path` is a `.exe` — i.e. a native Windows binary, which on this platform
    means it is running through WSL interop rather than directly against the Linux fs."""
    return path.lower().endswith(".exe")


@functools.lru_cache(maxsize=1)
def _running_under_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


_MNT_DRIVE = re.compile(r"^/mnt/([a-zA-Z])(/.*)?$")


def host_path_for(exe: str | None, p: str | Path) -> str:
    """Convert a Linux path to whatever `exe` needs to see it as.

    Only matters when `exe` is a Windows binary AND we are under WSL: then `/mnt/d/foo`
    becomes `D:/foo` (forward slashes — both Godot and Blender accept them, and it avoids
    re-escaping backslashes for the subprocess argv). Every other case — a native Linux
    binary, a macOS bundle, a path not under `/mnt/<drive>/` — is returned unchanged.

    This is the general form `host_path()` and `blender_host_path()` both call. It exists
    because `host_path()` used to hardcode `godot()` as *the* resolved binary, which is
    wrong for any other process this project drives through WSL interop: on this machine
    Godot resolved to a native Linux build (no translation needed) while Blender resolved
    to a Windows `.exe` (translation required), and a caller building a Blender command
    line with the old `host_path()` got a silent no-op — `/mnt/d/...` handed unchanged to
    a Windows process, which cannot resolve it. Two different binaries can need two
    different answers to "is this a Windows exe", so the function has to take the exe as
    a parameter rather than assuming.
    """
    s = str(p)
    if not exe or not is_windows_exe(exe) or not _running_under_wsl():
        return s
    m = _MNT_DRIVE.match(s)
    if not m:
        return s
    drive, rest = m.group(1), (m.group(2) or "/")
    return f"{drive.upper()}:{rest}"


def host_path(p: str | Path) -> str:
    """Convert a Linux path to whatever the *resolved Godot binary* needs to see it as.

    Thin wrapper over `host_path_for(godot(), p)` — kept as the name every existing Godot
    call site already uses. `godot()` itself is cached, so repeated calls in a tight loop
    (every anchor x policy x difficulty in the parity check) do not re-glob the filesystem.

    Callers must use this ONLY for paths handed to Godot's argv (`--path`, `--shot`,
    `--a11y`, a script path, ...). Paths the Python side reads back afterward — to hash a
    screenshot, parse a JSON report, whatever — must stay Linux paths and must NOT go
    through this function.
    """
    return host_path_for(godot(), p)


def blender_host_path(p: str | Path) -> str:
    """Convert a Linux path to whatever the *resolved Blender binary* needs to see it as.

    `host_path_for(blender(), p)`. On this machine `blender()` resolves to a Windows
    `.exe` (there is no native Linux or WSL build), so this is the translation every
    Blender invocation's `--python <script>` argument — and any other filesystem path
    handed to Blender's argv — must go through. Using `host_path()` here would be wrong:
    it checks `godot()`'s resolution, which on this machine is a native Linux binary that
    needs no translation, so it would silently pass `/mnt/d/...` straight through to a
    process that cannot read it.

    Same rule as `host_path()`: only for paths going *into* Blender's argv. Paths the
    Python side reads back afterward (a rendered PNG, the sprite manifest) stay Linux
    paths.
    """
    return host_path_for(blender(), p)


def _is_linux_native(exe: str | None) -> bool:
    """True when `exe` is a native Linux binary — not a `.exe` run through WSL interop, and
    not the macOS app bundle. Shared by `needs_virtual_display()` and `godot_argv()` so the
    two do not drift on what "native Linux build" means."""
    return bool(exe) and not is_windows_exe(exe) and not exe.startswith("/Applications/")


def needs_virtual_display(exe: str | None) -> bool:
    """True when launching `exe` without wrapping it would put a real window on the owner's
    desktop, and an `Xvfb` virtual framebuffer is therefore the difference between an
    invisible capture and a focus-stealing one.

    Two things both have to be true:

    - `exe` is a native Linux build (not a `.exe` run through WSL interop, and not the
      macOS app bundle). Those two still open a real window on a real desktop no matter
      what wraps them — WSL interop and `osascript`-free macOS both hand the window to the
      host compositor directly, and there is no equivalent of `Xvfb` to interpose there
      from this side. Only the Linux build's window can be redirected to a framebuffer
      nothing ever presents to a screen.
    - `$DISPLAY` is set, i.e. there is a desktop (WSLg's forwarded display, a real X11 or
      Wayland session, ...) that a window would actually land on. If `$DISPLAY` is unset —
      a genuinely headless box with no desktop at all — there is nothing to hide a window
      from, and wrapping in `Xvfb` would be protecting against an audience that does not
      exist. (It would also still work fine as a way to *give* Godot a display if this ever
      runs somewhere with none — `xvfb-run` does not require a pre-existing `$DISPLAY` — but
      that is not the case this function is answering.)
    """
    return _is_linux_native(exe) and bool(os.environ.get("DISPLAY"))


def xvfb_prefix() -> list[str]:
    """The argv prefix that runs a command against a throwaway `Xvfb` virtual framebuffer,
    or `[]` if `xvfb-run` is not installed on this machine.

    `-a` picks a free display number automatically, so this is safe to run concurrently
    (a sweep or a parallel test run does not collide on `:99`). The screen size is generous
    padding over anything this project screenshots — see `_XVFB_SCREEN_ARGS`.
    """
    if not shutil.which("xvfb-run"):
        return []
    return ["xvfb-run", *_XVFB_SCREEN_ARGS]


def godot_argv(project_root: str | Path, extra_args: list[str], want_window: bool) -> list[str]:
    """Build the full subprocess argv to launch Godot against this project.

    This is the single place that knows how to launch Godot invisibly, so nothing else
    should assemble a Godot command line by hand. `extra_args` is everything that goes after
    `--path <root>` — engine flags like `--fixed-fps 60`, the `--` separator, and the game's
    own CLI (`--autoplay`, `--anchor`, `--shot`, ...). Every element is passed through
    `host_path()`, which is a no-op for anything that is not a `/mnt/<drive>/...` path, so
    callers do not need to sort path arguments from flag arguments themselves.

    `want_window=False` (what `tools/shot.py` and the gate's rendered checks use) prefixes
    the whole command with `xvfb_prefix()` whenever `needs_virtual_display()` says a window
    would otherwise land on the owner's desktop — i.e. only for a native Linux build with a
    real `$DISPLAY` to hide from. `want_window=True` never wraps, even if the resolved build
    could be hidden; something that explicitly wants to be seen (a human debugging by eye)
    should get exactly that.

    A native Linux Godot also gets `--rendering-driver opengl3` unconditionally, whether or
    not it is wrapped: this machine's Linux build has only Mesa llvmpipe software GL behind
    it, and GL Compatibility is what every existing `--shot`/`--a11y` verification hook was
    written against.

    Raises `RuntimeError` if no Godot binary resolves at all — every caller of this needs a
    real binary to run, so failing loudly here beats a caller trying to subprocess.run(None).
    """
    exe = godot()
    if exe is None:
        raise RuntimeError(
            "no Godot binary found on this machine: checked $LF_GODOT, a Linux build under "
            "/mnt/*/godot/linux/, `godot`/`godot4` on PATH, the macOS app bundle, and a "
            "Windows console exe under /mnt/*/godot/")

    argv = [exe, "--path", host_path(project_root)]
    if _is_linux_native(exe):
        # Always ask for GL Compatibility's actual backend on a native Linux build, wrapped
        # or not — see docstring.
        argv += ["--rendering-driver", "opengl3"]
    argv += [host_path(a) for a in extra_args]

    if not want_window and needs_virtual_display(exe):
        prefix = xvfb_prefix()
        if prefix:
            argv = prefix + argv
        # If xvfb-run is missing, this falls back to a real window rather than raising —
        # the caller still gets a working capture, just not an invisible one. Callers that
        # care should check `shutil.which("xvfb-run")` themselves if that distinction
        # matters to them; `tools/shot.py` does not currently need to, because this machine
        # has it installed.

    return argv


def blender_argv(script: str | Path, script_args: list[str]) -> list[str]:
    """Build the full subprocess argv to run `script` headless inside Blender.

    Mirrors `godot_argv()`: the single place that knows how to launch Blender, so nothing
    else assembles the command line by hand. Always `-b --python <script> -- <script_args>`
    — this project's Blender scripts (`render.py`) parse their own arguments after `--`,
    exactly like the documented `blender -b --python tools/blender/render.py -- --only X`
    invocation in `CLAUDE.md`.

    `script` goes through `blender_host_path()` because it is a filesystem path handed to
    Blender's argv. `script_args` does NOT — `render.py`'s own arguments are flags and
    asset names (`--only`, `pulse_turret`), never filesystem paths, and translating them
    would be wrong for the day one of them is. A caller that starts passing a path in
    `script_args` (an output directory override, say) must translate that element itself
    with `blender_host_path()` before it reaches here, the same discipline `godot_argv()`
    documents for `extra_args`.

    Raises `RuntimeError` if no Blender binary resolves at all, for the same reason
    `godot_argv()` does: failing loudly here beats a caller trying to subprocess.run(None).
    """
    exe = blender()
    if exe is None:
        raise RuntimeError(
            "no Blender binary found on this machine: checked $LF_BLENDER, `blender` on "
            "PATH, the macOS app bundle, and a Windows install under "
            "/mnt/*/Program Files*/Blender Foundation/")
    return [exe, "-b", "--python", blender_host_path(script), "--", *script_args]
