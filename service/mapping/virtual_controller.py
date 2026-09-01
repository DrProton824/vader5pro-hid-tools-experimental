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

Why a subprocess, and why a named pipe rather than stdin/stdout
─────────────────────────────────────────────────────────────────
HIDMaestro (see bridge/README.md) is a C# / .NET library; this app is
Python. Rather than binding to the CLR from Python, a small self-contained
process ("HMBridge.exe", built from bridge/HMBridge/) owns the HIDMaestro
context and controller for its lifetime.

HIDMaestro's own CreateController() requires an elevated (administrator)
caller — not just once for driver install, but for every process that
calls it. VaderService.exe itself deliberately stays unelevated (so tray
behaviour, autostart, and hotkeys are unaffected), which means HMBridge
has to be launched as its own elevated child. Windows has no supported
way to hand an *existing* unelevated process's stdio pipes to a newly
*elevated* process — UAC elevation always creates a brand new process, so
a plain subprocess.Popen(..., stdin=PIPE, stdout=PIPE) simply can't be
elevated after the fact.

The fix is to swap the transport: this class creates a named pipe server
*before* launching anything, then starts HMBridge.exe elevated via
ShellExecuteEx's "runas" verb (which is what actually triggers the UAC
consent prompt — a plain CreateProcess call to an admin-manifested exe
just fails outright, it does not prompt). The elevated HMBridge process
then connects to that already-open pipe as a client. Named pipes cross
the elevation boundary fine as long as the low-privilege side created the
pipe (the common, supported direction) — only in-process kernel objects
like window messages are blocked cross-elevation by UIPI, not named
pipes.

Practical effect: once per VaderService.exe start (not once per button
press), Windows shows one UAC consent prompt for HMBridge.exe, the same
one you'd see running it manually as Administrator. If the user declines
it, or `virtual_controller_enabled` is False, or the bridge simply isn't
present, `is_available` stays False and everything below is a no-op —
the rest of the app is completely unaffected either way.

Setup happens on a background thread so VaderService.exe's own startup
(tray icon, hotkeys, ...) never waits on the UAC prompt being answered.

Protocol
────────
One command per line, one reply per line, both directions over the pipe:

    press <button>
    release <button>
    hat <direction>            centered | n | ne | e | se | s | sw | w | nw
    axis left <x> <y>          0.0-1.0, 0.5 = center
    axis right <x> <y>
    trigger left <value>       0.0-1.0
    trigger right <value>
    quit

Replies: "ok" or "error <message>". The first line HMBridge sends after
connecting is its own startup result ("ok", or "error startup: ...").

Graceful degradation
─────────────────────
If the bridge executable can't be found, the user declines the UAC
prompt, HIDMaestro itself fails to start, or the pipe breaks mid-session,
`is_available` becomes/stays False and every public method on this class
is a no-op. ButtonMapper and MacroPlayer always hold a VirtualController
instance (never None) and call straight through it without checking that
themselves.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import pathlib
import sys
import threading
from typing import Optional

BRIDGE_EXE_NAME = "HMBridge.exe"
PIPE_NAME = "VaderRemapperHMBridge"

# Overrides bridge discovery entirely -- convenient for local development
# when running the service from source against a bridge built somewhere
# outside the repo tree.
_ENV_OVERRIDE = "VADER_HMBRIDGE_PATH"

# How long to wait for the user to respond to the UAC prompt and for
# HMBridge to connect, before giving up and treating the bridge as
# unavailable for this session.
CONNECT_TIMEOUT_SECONDS = 45.0

kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

# ── Win32 constants ───────────────────────────────────────────────────────────

PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_MESSAGE = 0x00000004
PIPE_READMODE_MESSAGE = 0x00000002
PIPE_WAIT = 0x00000000
PIPE_UNLIMITED_INSTANCES = 255
NMPWAIT_USE_DEFAULT_WAIT = 0x00000000
ERROR_PIPE_CONNECTED = 535
INVALID_HANDLE_VALUE = wt.HANDLE(-1).value

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_FLAG_NO_UI = 0x00000400
SW_HIDE = 0

WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wt.HWND),
        ("lpVerb", wt.LPCWSTR),
        ("lpFile", wt.LPCWSTR),
        ("lpParameters", wt.LPCWSTR),
        ("lpDirectory", wt.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wt.HINSTANCE),
        ("lpIDList", wt.LPVOID),
        ("lpClass", wt.LPCWSTR),
        ("hkeyClass", wt.HKEY),
        ("dwHotKey", wt.DWORD),
        ("hIconOrMonitor", wt.HANDLE),
        ("hProcess", wt.HANDLE),
    ]


# Every call below gets an explicit signature -- without argtypes, ctypes
# marshals a bare Python int argument as 32-bit by default even on 64-bit
# Windows (the Win32 "long" is always 32 bits, LLP64), which silently
# truncates HANDLE values. See tray.py / rawinput_reader.py for the same
# discipline applied elsewhere in this codebase.
kernel32.CreateNamedPipeW.argtypes = [
    wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPVOID,
]
kernel32.CreateNamedPipeW.restype = wt.HANDLE
kernel32.ConnectNamedPipe.argtypes = [wt.HANDLE, wt.LPVOID]
kernel32.ConnectNamedPipe.restype = wt.BOOL
kernel32.ReadFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
kernel32.ReadFile.restype = wt.BOOL
kernel32.WriteFile.argtypes = [wt.HANDLE, wt.LPCVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
kernel32.WriteFile.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
kernel32.TerminateProcess.restype = wt.BOOL
kernel32.GetLastError.restype = wt.DWORD
shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
shell32.ShellExecuteExW.restype = wt.BOOL


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

    Setup (pipe creation, elevated launch, UAC wait, HIDMaestro startup)
    happens on a background thread; the constructor returns immediately.
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self._pipe: Optional[int] = None
        self._process_handle: Optional[int] = None
        self._lock = threading.Lock()
        self._available = False
        self._closing = False
        if enabled:
            threading.Thread(target=self._start, name="VirtualControllerSetup", daemon=True).start()

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        """True once HMBridge has connected and reported successful
        startup. False before that finishes, if the user declined the UAC
        prompt, if HIDMaestro itself failed to start, or after the pipe
        breaks."""
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
        """Best-effort clean shutdown. Safe to call multiple times, safe
        to call even if setup never finished (or never started)."""
        self._closing = True
        with self._lock:
            pipe, self._pipe = self._pipe, None
            process_handle, self._process_handle = self._process_handle, None
        self._available = False

        if pipe is not None:
            try:
                self._write_line(pipe, "quit")
            except Exception:
                pass
            try:
                kernel32.CloseHandle(pipe)
            except Exception:
                pass

        if process_handle is not None:
            try:
                kernel32.WaitForSingleObject(process_handle, 2000)
                kernel32.TerminateProcess(process_handle, 0)
            except Exception:
                pass
            try:
                kernel32.CloseHandle(process_handle)
            except Exception:
                pass

    # ── Internal: setup ──────────────────────────────────────────────────

    def _start(self) -> None:
        exe = _find_bridge_exe()
        if exe is None or self._closing:
            return

        pipe = kernel32.CreateNamedPipeW(
            f"\\\\.\\pipe\\{PIPE_NAME}",
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
            1,  # one instance -- one VaderService, one bridge, at a time
            4096,
            4096,
            0,
            None,
        )
        if pipe == INVALID_HANDLE_VALUE or self._closing:
            return

        process_handle = self._launch_elevated(exe)
        if process_handle is None:
            kernel32.CloseHandle(pipe)
            return

        if not self._wait_for_connect(pipe):
            kernel32.CloseHandle(pipe)
            kernel32.TerminateProcess(process_handle, 0)
            kernel32.CloseHandle(process_handle)
            return

        if self._closing:
            kernel32.CloseHandle(pipe)
            kernel32.CloseHandle(process_handle)
            return

        startup_reply = self._read_line(pipe)
        if not startup_reply.startswith("ok"):
            kernel32.CloseHandle(pipe)
            kernel32.TerminateProcess(process_handle, 0)
            kernel32.CloseHandle(process_handle)
            return

        self._pipe = pipe
        self._process_handle = process_handle
        self._available = True

    def _launch_elevated(self, exe: pathlib.Path) -> Optional[int]:
        info = SHELLEXECUTEINFOW()
        info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
        info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI
        info.hwnd = None
        info.lpVerb = "runas"
        info.lpFile = str(exe)
        info.lpParameters = PIPE_NAME
        info.lpDirectory = str(exe.parent)
        info.nShow = SW_HIDE

        if not shell32.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
            return None  # bridge not found, elevation declined, or launch failed
        return info.hProcess

    def _wait_for_connect(self, pipe: int) -> bool:
        result: dict[str, bool] = {}

        def _connect() -> None:
            ok = kernel32.ConnectNamedPipe(pipe, None)
            if not ok and kernel32.GetLastError() != ERROR_PIPE_CONNECTED:
                result["ok"] = False
                return
            result["ok"] = True

        thread = threading.Thread(target=_connect, daemon=True)
        thread.start()
        thread.join(timeout=CONNECT_TIMEOUT_SECONDS)
        return result.get("ok", False)

    # ── Internal: pipe I/O ───────────────────────────────────────────────

    @staticmethod
    def _write_line(pipe: int, line: str) -> None:
        data = (line + "\n").encode("utf-8")
        written = wt.DWORD(0)
        if not kernel32.WriteFile(pipe, data, len(data), ctypes.byref(written), None):
            raise OSError("WriteFile failed")

    @staticmethod
    def _read_line(pipe: int) -> str:
        # Message-mode pipe: one WriteFile on the other end is delivered
        # as one ReadFile-able message, so a single read is one line as
        # long as it's under the buffer size (ample for this protocol).
        buf = ctypes.create_string_buffer(4096)
        read = wt.DWORD(0)
        if not kernel32.ReadFile(pipe, buf, len(buf), ctypes.byref(read), None):
            raise OSError("ReadFile failed")
        return buf.raw[: read.value].decode("utf-8", errors="replace").strip()

    def _send(self, line: str) -> None:
        if not self._available or self._pipe is None:
            return
        with self._lock:
            if self._pipe is None:
                return
            try:
                self._write_line(self._pipe, line)
                self._read_line(self._pipe)  # discard "ok"/"error ..."
            except OSError:
                self._available = False
