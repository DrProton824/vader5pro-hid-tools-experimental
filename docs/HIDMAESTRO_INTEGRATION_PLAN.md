# Virtual Controller Extension — Implementation Plan

This plan turns `HIDMaestro_Idea_v2.md` into a sequence of small,
independently testable patches on top of `vader5pro-hid-tools`. Every
phase below leaves the service and GUI fully working on its own — you can
stop after any phase and still have a shippable app.

**Hard constraint honored throughout:** `gui/MainPage.py`, every file
under `gui/scripts/`, and the `.ctkproj` layout stay visually untouched.
New functionality is exposed either automatically (no assignment needed)
or through the existing macro system, which already accepts arbitrary
action dicts. No new widgets, tabs, or fields are added anywhere.

## Merged against v1.2

This revision of the bundle is re-merged against your v1.2 source
(`single_source_of_truth.zip`), which touched two of the files this patch
also touches:

- `gui/scripts/macros.py` — v1.2 added extended-key recording (Windows
  key, Application key, nav cluster) and a macro-combobox ordering fix.
  This patch's one-line `_describe_action()` change is reapplied on top
  of that, unchanged in spirit.
- `service/mapping/macro_player.py` — v1.2 added the same extended-key
  replay logic plus a "stuck key" safety net that force-releases any key
  still logically held if playback is interrupted. This patch's
  `controller_down`/`controller_up` handling is merged in alongside that,
  including a matching stuck-*button* safety net for the virtual
  controller (same idea, same `finally` block).

Everything else in this bundle (`shared/config.py`,
`service/hid_interface/constants.py`, `service/mapping/mapper.py`,
`service/mapping/virtual_controller.py`, `service/main.py`) is unchanged
from the original patch — your v1.2 source didn't touch those files, so
they still apply as plain drop-ins.

---

## Phase overview

| Phase | What it adds | Needs a bridge build? | GUI touched? |
|---|---|---|---|
| 1 | Button classification + config schema for new binding types | No | No |
| 2 | `VirtualController` (Python) + wiring into `ButtonMapper` / `MacroPlayer` | No (degrades to no-op) | No |
| 3 | `HMBridge` — the actual C# process that talks to HIDMaestro | Yes (see below) | No |
| 4 (design only, not shipped yet) | GUI-side controller-macro recording | Yes | No (reuses macro list) |

Phases 1 and 2 are pure Python and safe to merge and run today — with no
`HMBridge.exe` present, `VirtualController.is_available` is `False` and
every existing keyboard/macro code path behaves exactly as it does now.

## Phase 1 — Classification + schema

Teaches the codebase the difference between a "standard" button (already
visible to Windows through the native XInput/DirectInput interface) and a
"vendor-only" button (only ever seen by this app), and lets `config.json`
carry two new binding types without changing how the two existing types
(`keybind`, `macro`) behave. `combo` is accepted by the schema but not
dispatched yet — reserved for later.

Files: `service/hid_interface/constants.py`, `shared/config.py`.

## Phase 2 — `VirtualController` + routing

Adds the semantic API (`press`, `release`, `set_dpad`, `set_left_stick`,
`set_right_stick`, `set_left_trigger`, `set_right_trigger`) and wires it
into `ButtonMapper` and `MacroPlayer`:

- Vendor-only buttons **without** an explicit mapping are forwarded to the
  virtual controller under their own name (the "hybrid mode" default).
- Buttons with an explicit `controller_button`/`controller_macro` mapping
  route there instead.
- With no bridge process available, every call is a safe no-op.

Files: `service/mapping/virtual_controller.py` (new),
`service/mapping/mapper.py`, `service/mapping/macro_player.py`,
`service/main.py`, `gui/scripts/macros.py` (one label change, zero
layout change).

## Phase 3 — `HMBridge`

The C# process that owns `HMContext`/`HMController` and is the only place
that references `HIDMaestro.Core.dll`. Written against the real class
shapes in the HIDMaestro source you shared (`HMContext`, `HMController`,
`HMProfileBuilder`, `HidDescriptorBuilder`, `HMGamepadState`, `HMButton`,
`HMHat`, `HMAxis`), including a verified subtlety: `HMButton` only has 18
named bits, but `HMProfileBuilder`'s default `ButtonMap` is identity (bit
*N* → descriptor button *N*), so the bridge just uses a fixed 21-button
name table and raw bit positions rather than reusing the named roles.

**Build it with zero local installs** via `.github/workflows/build.yml`'s
`build_bridge` job — see `bridge/README.md`, Option A. It runs on GitHub's
`windows-2022` runner, which ships Visual Studio 2022 and WDK
`10.0.26100.0` preinstalled (verified against `actions/runner-images`
issue history), so the job calls HIDMaestro's own `scripts/build_all.cmd`
directly with no separate WDK/EWDK download step, then bundles the
resulting `HMBridge.exe` straight into the same `VaderMapper-v...`
artifact the main app build already produces. One workflow run, one
download.

Files: `bridge/HMBridge/HMBridge.csproj`, `bridge/HMBridge/Program.cs`,
`bridge/README.md`, `.github/workflows/build.yml`, `build/build.py`.

### Why a subprocess and not pythonnet

- Zero Python-side dependencies (no `pythonnet`, no CLR bootstrap version
  matrix).
- `VirtualController` degrading to "bridge not found → no-op" is trivial
  and safe; a failed in-process CLR load is a much harder failure mode to
  isolate.
- Latency is a non-issue at human input rates.

### Custom profile

Sticks/triggers use `HidDescriptorBuilder.AddStick()`/`.AddTrigger()`, and
the actual `HMAxis` values are read back from `profile.Sticks[i]`/
`profile.Triggers[i]` after `CreateController()` — the discovery pattern
the SDK's own doc comments recommend, so `Program.cs` never hardcodes
which HID usage code ended up assigned to which stick.

---

## Phase 4 (not shipped in this batch) — controller-macro recording

The idea doc's Option B (GUI-side Raw Input registration) is the right
call: it reuses the exact broadcast-model reasoning already documented in
`rawinput_reader.py`'s own docstring, and needs no new IPC protocol. Once
Phase 3 is proven on hardware:

1. Add a small `gui/scripts/controller_capture.py` that opens its own
   `RAWINPUTDEVICE` registration for usage page `0xFFA0` / usage `0x0001`
   (same constants `service/hid_interface/constants.py` already exports),
   reuses `service.hid_interface.hid_protocol.decode_report()` directly
   (it's a pure function, safe to import from the GUI process), and turns
   `ButtonPressed`/`ButtonReleased` deltas into `controller_down` /
   `controller_up` macro actions with the same `{"key": ..., ...}` shape
   `macros.py` already uses for keyboard actions.
2. Wire it into `macros.py`'s existing `fcmevhr_record` /
   `_on_key_event` / `_draft_actions` flow — controller events append to
   the same draft list the keyboard hook already writes to, so recording
   a macro that mixes a keypress and a controller button press "just
   works" through the existing Record/Stop/Save buttons. No new UI.
3. Assigning a `controller_button` mapping to a single physical button
   (rather than a whole macro) needs one decision: either (a) require a
   one-action macro through the existing macro UI (zero code beyond step
   1/2, recommended starting point), or (b) let `mapping.py`'s
   hotkey-capture field also recognize a controller-button press.

This phase is intentionally left as a design note rather than code: it's
the piece that most needs iteration against real hardware and a working
Phase 3 build.

---

## Applying the patches

See `docs/INSTALL.md` for exact copy/paste and verification steps.
