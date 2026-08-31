# Patch Notes — for reference only

Every file in this bundle is shipped as a complete, ready-to-drop file —
see `docs/INSTALL.md` for the copy-paste instructions. You don't need
anything below this line for a normal install.

This file is kept only so you can see, line by line, what changed in
`service/main.py` and `gui/scripts/macros.py` if you're diffing against a
version of those files you've since modified yourself and don't want to
overwrite outright.

---

## `service/main.py`

### Edit 1 — import

**Before:**
```python
from service.mapping.macro_player import MacroPlayer
from service.mapping.mapper import ButtonMapper
```

**After:**
```python
from service.mapping.macro_player import MacroPlayer
from service.mapping.mapper import ButtonMapper
from service.mapping.virtual_controller import VirtualController
```

### Edit 2 — construction

**Before:**
```python
    # ── Bootstrap ─────────────────────────────────────────────────────────────
    settings = cfg.load_settings()

    sender = InputSender()
    macro_player = MacroPlayer()
    mapper = ButtonMapper(sender, macro_player)
```

**After:**
```python
    # ── Bootstrap ─────────────────────────────────────────────────────────────
    settings = cfg.load_settings()

    sender = InputSender()
    virtual_controller = VirtualController(
        enabled=bool(settings.get("virtual_controller_enabled", True))
    )
    macro_player = MacroPlayer(virtual_controller=virtual_controller)
    mapper = ButtonMapper(sender, macro_player, virtual_controller=virtual_controller)
```

### Edit 3 — shutdown, `_quit()`

**Before:**
```python
    def _quit() -> None:
        stop_event.set()
        reader.stop()
        hotkey_watcher.stop()
        foreground_watcher.stop()
        icon_holder["icon"].stop()
```

**After:**
```python
    def _quit() -> None:
        stop_event.set()
        reader.stop()
        hotkey_watcher.stop()
        foreground_watcher.stop()
        virtual_controller.close()
        icon_holder["icon"].stop()
```

### Edit 4 — shutdown, final `finally` block

**Before:**
```python
    try:
        icon.run()  # blocks until "Exit" is chosen
    finally:
        stop_event.set()
        reader.stop()
        hotkey_watcher.stop()
        foreground_watcher.stop()
        reader.join(timeout=2.0)
```

**After:**
```python
    try:
        icon.run()  # blocks until "Exit" is chosen
    finally:
        stop_event.set()
        reader.stop()
        hotkey_watcher.stop()
        foreground_watcher.stop()
        virtual_controller.close()
        reader.join(timeout=2.0)
```

`virtual_controller.close()` is safe to call twice.

---

## `gui/scripts/macros.py`

### Edit — `_describe_action()`

**Before:**
```python
    @staticmethod
    def _describe_action(action: Dict[str, Any]) -> str:
        if action["type"] == "wait":
            return f"wait {action['ms']}ms"
        key_display = action.get("key", f"scan:{action.get('scan_code', '?')}")
        return f"{action['type']} {key_display}"
```

**After:**
```python
    @staticmethod
    def _describe_action(action: Dict[str, Any]) -> str:
        if action["type"] == "wait":
            return f"wait {action['ms']}ms"
        if action["type"] in ("controller_down", "controller_up"):
            verb = "controller down" if action["type"] == "controller_down" else "controller up"
            return f"{verb} {action.get('key', '?')}"
        key_display = action.get("key", f"scan:{action.get('scan_code', '?')}")
        return f"{action['type']} {key_display}"
```

This edit applies whether you're on the pre-v1.2 or v1.2 version of the
file — the `_describe_action()` method itself is identical in both, v1.2
only changed other parts of the file (extended-key recording, macro
combobox ordering).

---

## `service/mapping/macro_player.py`

This one changed more substantially between v1.1 and v1.2 (extended-key
replay, stuck-key safety net), so a line-level diff is less useful than
just comparing the shipped file in this bundle against your current copy
directly. The additions are:

- `from .virtual_controller import VirtualController` import, and
  `Optional` added to the `typing` import.
- `virtual_controller: Optional[VirtualController] = None` constructor
  parameter, stored as `self._controller`.
- A `held_controller: list[str]` tracking list alongside the existing
  `held` list, for the same stuck-key/stuck-button safety net purpose.
- One new branch in `_run()`'s loop, handling
  `kind in ("controller_down", "controller_up")` — placed before the
  existing `if kind not in ("press", "release"): continue` line so
  controller actions aren't mistaken for unrecognized types.
- In `finally`, alongside the existing loop that releases any still-held
  keys, a matching loop that releases any still-held controller buttons.
