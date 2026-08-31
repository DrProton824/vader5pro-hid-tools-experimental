# service/mapping/macro_player.py
# Macro playback via Win32 SendInput scancodes and/or the virtual controller.
#

"""
Recording and playback
──────────────────────
Macro actions are recorded by the GUI's macros.py using the `keyboard`
library, which reports hardware scan codes rather than virtual-key
names. Replaying by scan code (KEYEVENTF_SCANCODE) sidesteps needing a
second name-to-VK table that would have to agree with keyboard's naming
exactly — each action is just replayed with the same scan code it was
captured with.

Extended keys (Windows, Application, navigation cluster)
───────────────────────────────────────────────────────
Those additionally need KEYEVENTF_EXTENDEDKEY set (see extended_keys.py
for exactly which scan codes and why). An action carries "extended":
true when macros.py determined at capture time that it needs this.
Regular letters, digits, symbols, F-keys, and left-side modifiers are
unaffected and still replay exactly as before.

Controller actions
───────────────────
"controller_down" / "controller_up" carry a button name in the same
"key" field keyboard actions already use, and are routed to
VirtualController.press()/release() instead of SendInput. A macro's
action list can freely mix keyboard and controller actions — each
action's own "type" decides where it goes.

Stuck-key / stuck-button safety net
─────────────────────────────────────
If playback stops mid-sequence (exception, a future cancel path, ...)
while a key or controller button is still logically "held," it is
force-released in `finally` — otherwise a key can end up physically
stuck down system-wide (or a controller button stuck pressed) until the
user happens to tap that exact key/button themselves.

Threading
─────────
Each play() call runs on its own daemon thread so a macro's "wait"
actions never block the HID reader thread — other buttons keep working
while a macro is mid-playback. MAX_CONCURRENT_MACROS caps how many can
run at once (e.g. rapid re-presses of different macro buttons); beyond
that, further presses are dropped rather than queued.

On top of that, the *same* macro (identified by the identity of its
`actions` list, which is the same object each time a given macro button
is pressed since it's looked up from the in-memory config rather than
re-parsed per press) is never played more than once concurrently — a
repress while it's still running is dropped. This avoids two copies of
the same macro racing each other and stomping on shared keys (e.g. both
holding/releasing "shift" out of sync with one another).
"""

from __future__ import annotations

import ctypes
import threading
import time
from typing import Any, Optional

from .input_sender import INPUT, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_KEYUP, KEYEVENTF_EXTENDEDKEY
from .extended_keys import ALWAYS_EXTENDED, NAV_CLUSTER
from .virtual_controller import VirtualController

KEYEVENTF_SCANCODE = 0x0008

MAX_CONCURRENT_MACROS = 2
MAX_MACRO_ACTIONS = 500   # guards against a corrupt/huge macro locking up a thread
MAX_WAIT_MS = 5000        # guards against a single bogus "wait" entry stalling playback

_SendInput = ctypes.windll.user32.SendInput


def _needs_extended(scan_code: int, action: dict[str, Any]) -> bool:
    if scan_code in ALWAYS_EXTENDED:
        return True
    if scan_code in NAV_CLUSTER:
        return bool(action.get("extended"))  # numpad-as-nav (Num Lock off) is not extended
    return False


def _make_scancode_input(scan_code: int, key_up: bool, extended: bool = False) -> INPUT:
    flags = (
        KEYEVENTF_SCANCODE
        | (KEYEVENTF_KEYUP if key_up else 0)
        | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    )
    inp = INPUT(type=INPUT_KEYBOARD)
    inp._input.ki = KEYBDINPUT(wVk=0, wScan=scan_code, dwFlags=flags)
    return inp


class MacroPlayer:
    """
    Usage
    ─────
        player = MacroPlayer(virtual_controller=virtual_controller)
        player.play(binding["actions"])   # returns immediately

    `virtual_controller` is optional — omit it (or pass None) to keep the
    keyboard-only behaviour from before this class supported controller
    actions. Any "controller_down"/"controller_up" action in a macro is
    then simply skipped, the same way an unrecognized action type already
    was.
    """

    def __init__(self, virtual_controller: Optional[VirtualController] = None) -> None:
        self._active = 0
        self._active_macros: set[int] = set()
        self._lock = threading.Lock()
        self._controller = virtual_controller

    def play(self, actions: list[dict[str, Any]]) -> None:
        if not actions:
            return
        macro_id = id(actions)
        with self._lock:
            if self._active >= MAX_CONCURRENT_MACROS:
                return
            if macro_id in self._active_macros:
                return
            self._active += 1
            self._active_macros.add(macro_id)
        threading.Thread(target=self._run, args=(actions, macro_id), name="MacroPlayer", daemon=True).start()

    def _run(self, actions: list[dict[str, Any]], macro_id: int) -> None:
        held: list[tuple[int, bool]] = []  # scan codes currently held: [(scan_code, extended), ...]
        held_controller: list[str] = []    # controller button names currently held
        try:
            for action in actions[:MAX_MACRO_ACTIONS]:
                kind = action.get("type")

                if kind == "wait":
                    ms = action.get("ms", 0)
                    if isinstance(ms, (int, float)) and ms > 0:
                        time.sleep(min(ms, MAX_WAIT_MS) / 1000)
                    continue

                if kind in ("controller_down", "controller_up"):
                    target = action.get("key", "")
                    if target and self._controller is not None:
                        if kind == "controller_down":
                            self._controller.press(target)
                            if target not in held_controller:
                                held_controller.append(target)
                        else:
                            self._controller.release(target)
                            if target in held_controller:
                                held_controller.remove(target)
                    continue

                if kind not in ("press", "release"):
                    continue

                scan_code = action.get("scan_code")
                if not isinstance(scan_code, int):
                    continue

                extended = _needs_extended(scan_code, action)
                key_up = kind == "release"
                token = (scan_code, extended)

                if key_up:
                    if token in held:
                        held.remove(token)
                else:
                    held.append(token)

                inp = _make_scancode_input(scan_code, key_up=key_up, extended=extended)
                _SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        finally:
            for scan_code, extended in reversed(held):
                inp = _make_scancode_input(scan_code, key_up=True, extended=extended)
                _SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            if self._controller is not None:
                for target in reversed(held_controller):
                    self._controller.release(target)
            with self._lock:
                self._active -= 1
                self._active_macros.discard(macro_id)
