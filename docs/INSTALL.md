# Install guide — Phase 1-3, all at once

This bundle is laid out to mirror your repository exactly. Every file
below already exists at that same relative path in your repo except the
`bridge/` folder (new). **Unzip this bundle directly into your repository
root, overwriting when prompted.** No merging by hand needed — everything
here was already re-applied against your v1.2 source (see the top of
`docs/HIDMAESTRO_INTEGRATION_PLAN.md` for exactly what was re-merged).

```
shared/config.py
service/hid_interface/constants.py
service/mapping/virtual_controller.py   ← new file
service/mapping/mapper.py
service/mapping/macro_player.py
service/main.py
gui/scripts/macros.py
bridge/HMBridge/HMBridge.csproj         ← new file
bridge/HMBridge/Program.cs              ← new file
bridge/HMBridge/app.manifest            ← new file
bridge/README.md                        ← new file
build/build.py
.github/workflows/build.yml
```

This does **not** touch your project's own `README.md`, `PROJECT.md`,
`CHANGELOG.md`, or anything under `docs/` other than the two new files
this bundle adds there.

## 1. Back up first

Standard practice, but worth saying given this overwrites `service/main.py`,
`build/build.py`, and `.github/workflows/build.yml`. Commit your current
state, or work on a branch.

## 2. Copy everything in, restart, confirm nothing changed

Unzip into the repo root. Run `VaderService.exe` (or `python
service/main.py` from source) and `VaderConfig.exe` as usual. Profiles,
macros — including the extended-key ones (Windows key, nav cluster) from
your v1.2 update — hotkeys, tray icon: all identical to before. Nothing
in this bundle changes visible behavior on its own; `VirtualController`
can't find a bridge yet, so it's a no-op everywhere it's called.

## 3. Build the bridge via GitHub Actions

You do not need Visual Studio, the WDK, or .NET installed on your own
machine for this part.

1. Push the repo (with this bundle applied) to GitHub — a throwaway test
   repository is fine.
2. **Actions → Build Windows Executables → Run workflow.**
3. Leave every default as-is (**"Also build HMBridge.exe"** is checked by
   default) and click **Run workflow**.
4. When the run finishes, download the `VaderMapper-v...` artifact.
   `bridge/HMBridge.exe` is already inside it — the same build step that
   produces `VaderService.exe`/`VaderConfig.exe` bundles it in
   automatically now (see `build/build.py`'s `copy_bridge_exe()`).

See `bridge/README.md` for what's actually happening in that workflow job
and what to do if it ever stops working (GitHub does occasionally change
what's preinstalled on its runner images).

## 4. Try it

Restart `VaderService.exe`. Nothing happens yet — setup is lazy, it only
triggers the first time a button actually needs the virtual controller
(see `bridge/README.md`'s "Elevation" section for why). Press a
vendor-only button that has **no explicit mapping**: on a fresh install
that's `Arrow` or `Circle` (the default profile already binds C, Z,
M1-M4, LM, RM to keyboard F13-F20 — see "Explicit mapping wins" below).

You should see a UAC prompt for `HMBridge.exe` at that first press — this
is still being diagnosed as of this bundle (see
`docs/HIDMAESTRO_INTEGRATION_PLAN.md`'s "Diagnostic round" note), so if
nothing visibly happens, that's expected right now and the next step is
to check the logs, not to assume something's broken on your end.

Check both log files (see `bridge/README.md`'s "Diagnosing a failed run"
section for exactly what each line means):

- `bridge_debug.log`, next to `VaderService.exe`
- `HMBridge_debug.log`, next to `HMBridge.exe` (only appears once
  `HMBridge.exe` has actually run at least once)

The line to look for is `Running elevated: True` or `False` near the top
of `HMBridge_debug.log` — that's the direct answer to whether elevation
actually happened, independent of whether a visible prompt appeared.

If it does work: check `joy.cpl` (or any gamepad tester) for a new
"Vader 5 Pro Extended" device reacting to that button, and see
`bridge/README.md`'s button-numbering table if the tester just shows
generic "Button N" labels — that part's expected, not a bug.

**You do not need a `third_party/HIDMaestro` folder anywhere in this
downloaded app folder.** That's a build-time-only checkout used inside
the GitHub Actions run to compile `HMBridge.exe`; the published
`HMBridge.exe` is self-contained and already has everything it needs. If
you added one manually while troubleshooting, delete it.

## Explicit mapping wins

An explicit assignment (a keybind, a macro, `controller_button`,
`controller_macro`) on a button always takes over completely — the
default "forward unmapped vendor-only buttons automatically" behaviour
only applies when a button has **no** assignment at all. Assign a
keybind to M1 in the Mapping tab the normal way, and M1 stops reaching
the virtual controller — it does whatever the keybind says instead, same
as it always has.

This matters for testing because the shipped default profile already
assigns C, Z, M1, M2, M3, M4, LM, and RM to keyboard shortcuts F15, F16,
F17, F18, F19, F20, F13, and F14 respectively (see `DEFAULT_PROFILE` in
`shared/config.py`) — those 8 buttons won't auto-forward to the virtual
pad out of the box, only `Arrow` and `Circle` will (or any button you've
cleared to unmapped). To route one of the pre-mapped buttons to the
virtual controller, either clear its assignment (select it on the
Mapping tab, press Delete/Backspace) so the default forwarding applies,
or explicitly reassign it to `controller_button` (not exposed in the GUI
yet — see `docs/HIDMAESTRO_INTEGRATION_PLAN.md`'s Phase 4 for the planned
path, or edit `config.json` by hand in the meantime, per the binding
shapes documented in `shared/config.py`'s module docstring).

## Commit messages (one per file)

- `shared/config.py` — `config: add controller_button/controller_macro/combo binding types and virtual-controller settings`
- `service/hid_interface/constants.py` — `constants: classify buttons as standard vs vendor-only for virtual-controller routing`
- `service/mapping/virtual_controller.py` — `mapping: add VirtualController, a named-pipe wrapper over an elevated HMBridge process`
- `service/mapping/mapper.py` — `mapper: route controller_button/controller_macro bindings and default-forward unmapped vendor-only buttons`
- `service/mapping/macro_player.py` — `macro_player: play controller_down/controller_up actions through VirtualController, with a stuck-button safety net`
- `service/main.py` — `service: wire optional VirtualController into mapper/macro player, dispose on shutdown`
- `gui/scripts/macros.py` — `macros: label controller_down/controller_up actions in the action list description`
- `bridge/HMBridge/*`, `bridge/README.md` — `bridge: add HMBridge, an elevated named-pipe process bridging to the HIDMaestro SDK`
- `build/build.py` — `build: bundle a pre-built HMBridge.exe into the packaged app when available`
- `.github/workflows/build.yml` — `ci: add optional HMBridge build job on windows-2022, wire its output into the main build`
