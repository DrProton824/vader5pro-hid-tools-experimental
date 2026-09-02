#
# service/mapping/mapper.py
# Mapping engine – the bridge between HID events and key/controller injection.
#

"""
Purpose
───────
This module knows nothing about HID reports. It knows nothing about Win32
SendInput, and it knows nothing about HIDMaestro. It only translates
ButtonEvent objects into calls on an InputSender (keybind), a MacroPlayer
(macro / controller_macro), or a VirtualController (controller_button),
using the current config bindings.

Keeping this layer thin and independent makes it trivially testable: you
can unit-test it with fake InputSender/MacroPlayer/VirtualController
instances and fake events, without needing a controller or Windows at all.

Routing
───────
1. "macro" / "controller_macro" -> MacroPlayer.play() on press; release
   is a no-op.
2. "controller_button" -> VirtualController.press()/release().
3. "combo" -> reserved for a future phase, currently a no-op (see
   docs/HIDMAESTRO_INTEGRATION_PLAN.md).
4. "keybind" with a real value -> InputSender.press()/release(), unchanged.
5. Everything else -- "keybind" with an empty value, or no assignment at
   all -- forward vendor-only buttons (buttons with no native
   XInput/DirectInput representation — see
   service/hid_interface/constants.py) to the virtual controller under
   their own name. Standard buttons are left alone; the native gamepad
   interface already delivers them to Windows.

Why (4) and (5) are split on the *value*, not just presence of a binding
─────────────────────────────────────────────────────────────────────────
shared/config.py's load_bindings_for() always returns an entry for every
one of the 27 MAPPABLE_BUTTONS -- an unmapped button resolves to
{"type": "keybind", "value": ""}, not a missing key. So "does this
button have an explicit assignment" can't be answered by checking
whether a binding is present; it has to check whether the keybind's
*value* is non-empty, the same way InputSender already treats an empty
shortcut string as unmapped everywhere else in the app.

`virtual_controller` is always a VirtualController instance (never None) —
its own `is_available` flag makes every call here a safe no-op when no
bridge process is running, so this module never has to special-case that
itself.
"""

from __future__ import annotations

from ..hid_interface.constants import VENDOR_ONLY_BUTTONS
from ..hid_interface.hid_protocol import ButtonEvent, ButtonPressed, ButtonReleased
from .input_sender import InputSender
from .macro_player import MacroPlayer
from .virtual_controller import VirtualController


class ButtonMapper:
    """
    Receives button events and forwards them to the input sender
    (keybind), the macro player (macro / controller_macro), or the
    virtual controller (controller_button / default vendor-only
    forwarding), per the current bindings.

    Usage
    ─────
        sender = InputSender()
        virtual_controller = VirtualController()
        macro_player = MacroPlayer(virtual_controller=virtual_controller)
        mapper = ButtonMapper(sender, macro_player, virtual_controller=virtual_controller)
        mapper.update_bindings(config.load_bindings())

        # Then wire into the HID reader:
        reader = HIDReaderThread(mapper.handle_event)
    """

    def __init__(
        self,
        sender: InputSender,
        macro_player: MacroPlayer,
        virtual_controller: VirtualController | None = None,
    ) -> None:
        self._sender = sender
        self._macro_player = macro_player
        self._virtual_controller = virtual_controller or VirtualController(enabled=False)
        self._bindings: dict[str, dict] = {}

    def update_bindings(self, bindings: dict[str, dict]) -> None:
        """Push new bindings (called on startup and config reload)."""
        self._bindings = bindings
        # InputSender only needs to pre-parse the keybind subset.
        keybind_mapping = {
            button: binding.get("value", "")
            for button, binding in bindings.items()
            if binding.get("type") == "keybind"
        }
        self._sender.update_mappings(keybind_mapping)

    def handle_event(self, event: ButtonEvent) -> None:
        """
        Called on the HID reader thread for every state change.

        Must be fast – no I/O, no blocking calls in the hot path. Macro
        playback and virtual-controller I/O both run off this thread (see
        MacroPlayer and VirtualController), so neither ever blocks it.
        """
        binding = self._bindings.get(event.button) or {}
        kind = binding.get("type")

        if kind == "macro" or kind == "controller_macro":
            if isinstance(event, ButtonPressed):
                self._macro_player.play(binding.get("actions", []))
            return  # macros play in full on press; release is a no-op

        if kind == "controller_button":
            target = binding.get("value", "")
            if isinstance(event, ButtonPressed):
                self._virtual_controller.press(target)
            elif isinstance(event, ButtonReleased):
                self._virtual_controller.release(target)
            return

        if kind == "combo":
            return  # reserved for a future phase, currently a no-op

        # kind == "keybind" (explicit, or the empty-value shape
        # load_bindings_for() uses for "no assignment") -- only a
        # non-empty value counts as a real, explicit assignment.
        value = binding.get("value", "") if kind == "keybind" else ""
        if value:
            if isinstance(event, ButtonPressed):
                self._sender.press(event.button)
            elif isinstance(event, ButtonReleased):
                self._sender.release(event.button)
            return

        # No real assignment — forward vendor-only buttons to the virtual
        # controller by default (hybrid mode). Standard buttons already
        # reach Windows through the native gamepad interface.
        if event.button in VENDOR_ONLY_BUTTONS:
            if isinstance(event, ButtonPressed):
                self._virtual_controller.press(event.button)
            elif isinstance(event, ButtonReleased):
                self._virtual_controller.release(event.button)
