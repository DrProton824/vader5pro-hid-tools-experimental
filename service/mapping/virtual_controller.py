#
# service/mapping/virtual_controller.py
# Semantic wrapper around the HMBridge subprocess.
#

"""
Purpose
───────
The only component in this application that knows a virtual controller
exists. Everything else — ButtonMapper, MacroPlayer — calls press()/
release()/set_left_stick() etc. with button names from
shared.config.MAPPABLE_BUTTONS and never needs to know how the virtual
device is actually implemented.

Why a subprocess
────────────────
HIDMaestro (see bridge/README.md) is a C# / .NET library; this app is
Python. Rather than binding to the CLR from Python, a small self-contained
process ("HMBridge.exe", built from bridge/HMBridge/) owns the HIDMaestro
context and controller for its lifetime and speaks a line-oriented
protocol over stdin/stdout:

    press <button>
    release <button>
    hat <direction>            centered | n | ne | e | se | s | sw | w | nw
    axis left <x> <y>          0.0-1.0, 0.5 = center
    axis right <x> <y>
    trigger left <value>       0.0-1.0
    trigger right <value>
    quit

Every command gets one reply line back: "ok" or "error <message>".

Graceful degradation
─────────────────────
If the bridge executable can't be found, fails to start, or its pipe
breaks mid-session, `is_available` becomes False and every public method
on this class turns into a no-op. Nothing in the rest of the service needs
to check that itself — ButtonMapper and MacroPlayer always hold a
VirtualController instance (never None after construction) and call
straight through it. This mirrors the "keyboard path keeps working
regardless" principle used throughout the rest of the app (e.g.
vendor_init.py's write-only helpers, which are equally best-effort).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import threading
from typing import Optional

BRIDGE_EXE_NAME = "HMBridge.exe"

# Overrides bridge discovery entirely -- convenient for local development
# when running the service from source against a bridge built somewhere
# outside the repo tree.
_ENV_OVERRIDE = "VADER_HMBRIDGE_PATH"


def _find_bridge_exe() -> Optional[pathlib.Path]:
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        candidate = pathlib.Path(override)
        if candidate.is_file():
            return candidate

    if getattr(sys, "frozen", False):
        # Bundled exe: build.py copies HMBridge.exe next to the service
        # exe (flat) or into a "bridge" subfolder -- check both.
        base = pathlib.Path(sys.executable).resolve().parent
        for candidate in (base / BRIDGE_EXE_NAME, base / "bridge" / BRIDGE_EXE_NAME):
            if candidate.is_file():
                return candidate
        return None

    # Running from source: look for a locally built bridge under
    # bridge/HMBridge/ (any bin/ configuration -- Debug or Release,
    # build or publish output).
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    search_root = repo_root / "bridge" / "HMBridge"
    if not search_root.exists():
        return None
    for candidate in search_root.rglob(BRIDGE_EXE_NAME):
        return candidate
    return None


class VirtualController:
    """
    Usage
    ─────
        controller = VirtualController()
        ...
        controller.press("M1")
        controller.release("M1")
        ...
        controller.close()

    `enabled=False` (see settings["virtual_controller_enabled"]) skips
    even attempting to locate/start the bridge, identical in effect to the
    bridge simply not being present.
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._available = False
        if enabled:
            self._start()

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        """True once the bridge process is up and its startup reply was
        read successfully. False before construction attempts anything,
        after a failed start, or after the pipe breaks."""
        return self._available

    def press(self, button: str) -> None:
        if button:
            self._send(f"press {button}")

    def release(self, button: str) -> None:
        if button:
            self._send(f"release {button}")

    def set_dpad(self, direction: str) -> None:
        """direction: "centered" or one of the eight compass abbreviations
        (n, ne, e, se, s, sw, w, nw)."""
        self._send(f"hat {direction}")

    def set_left_stick(self, x: float, y: float) -> None:
        self._send(f"axis left {x:.4f} {y:.4f}")

    def set_right_stick(self, x: float, y: float) -> None:
        self._send(f"axis right {x:.4f} {y:.4f}")

    def set_left_trigger(self, value: float) -> None:
        self._send(f"trigger left {value:.4f}")

    def set_right_trigger(self, value: float) -> None:
        self._send(f"trigger right {value:.4f}")

    def close(self) -> None:
        """Best-effort clean shutdown. Safe to call multiple times and
        safe to call even if the bridge never started."""
        process = self._process
        if process is None:
            return
        self._process = None
        self._available = False
        try:
            if process.stdin is not None:
                process.stdin.write("quit\n")
                process.stdin.flush()
            process.wait(timeout=3.0)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    # ── Internal ──────────────────────────────────────────────────────────

    def _start(self) -> None:
        exe = _find_bridge_exe()
        if exe is None:
            return

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                [str(exe)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError:
            return

        try:
            assert process.stdout is not None
            startup_reply = process.stdout.readline()
        except Exception:
            startup_reply = ""

        if not startup_reply.strip().startswith("ok"):
            try:
                process.kill()
            except Exception:
                pass
            return

        self._process = process
        self._available = True

    def _send(self, line: str) -> None:
        if not self._available or self._process is None:
            return
        with self._lock:
            try:
                assert self._process.stdin is not None
                self._process.stdin.write(line + "\n")
                self._process.stdin.flush()
                assert self._process.stdout is not None
                self._process.stdout.readline()  # discard "ok"/"error ..."
            except (OSError, ValueError, AttributeError):
                self._available = False
