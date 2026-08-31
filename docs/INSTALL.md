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

Run the downloaded build. Press M1, M2, M3, M4, LM, RM, C, Z, Arrow, or
Circle on the controller and check `joy.cpl` (or any gamepad tester) for
a new "Vader 5 Pro Extended" device reacting. Nothing needs to be assigned
in the GUI for this — vendor-only buttons forward automatically.

If nothing shows up: check `VaderService.exe`'s working directory for a
`bridge/HMBridge.exe` file (confirms it was bundled), then try running
`HMBridge.exe` directly from a terminal **as Administrator** — virtual
device creation needs elevation, which the service doesn't currently
request automatically (see `docs/HIDMAESTRO_INTEGRATION_PLAN.md`, Phase 3
notes). The first line it prints on success is `ok`; anything starting
with `error startup:` means HIDMaestro itself failed to install/create
the device, and the rest of that line is the reason.

## Commit messages (one per file)

- `shared/config.py` — `config: add controller_button/controller_macro/combo binding types and virtual-controller settings`
- `service/hid_interface/constants.py` — `constants: classify buttons as standard vs vendor-only for virtual-controller routing`
- `service/mapping/virtual_controller.py` — `mapping: add VirtualController, a semantic wrapper over the HMBridge subprocess`
- `service/mapping/mapper.py` — `mapper: route controller_button/controller_macro bindings and default-forward unmapped vendor-only buttons`
- `service/mapping/macro_player.py` — `macro_player: play controller_down/controller_up actions through VirtualController, with a stuck-button safety net`
- `service/main.py` — `service: wire optional VirtualController into mapper/macro player, dispose on shutdown`
- `gui/scripts/macros.py` — `macros: label controller_down/controller_up actions in the action list description`
- `bridge/HMBridge/*`, `bridge/README.md` — `bridge: add HMBridge, the HIDMaestro-facing stdin/stdout process`
- `build/build.py` — `build: bundle a pre-built HMBridge.exe into the packaged app when available`
- `.github/workflows/build.yml` — `ci: add optional HMBridge build job on windows-2022, wire its output into the main build`
