# HMBridge — build guide

`HMBridge.exe` is the only part of this project that touches HIDMaestro
directly. It's a small process the Python service launches — elevated,
on its own — and talks to over a named pipe (see "Elevation" below for
why a named pipe and not stdin/stdout).

**Status:** written against the real class shapes in the HIDMaestro
source (`HMContext`, `HMController`, `HMProfileBuilder`,
`HidDescriptorBuilder`, `HMGamepadState`, `HMButton`, `HMHat`, `HMAxis`).
Confirmed building successfully via the GitHub Actions job below; not yet
verified end-to-end against real hardware.

## `third_party/HIDMaestro` is a build-time-only checkout

If you've cloned HIDMaestro locally, or the `build_bridge` CI job checked
it out — that folder is **only** needed to *compile* `HMBridge.exe`
(`HMBridge.csproj`'s `ProjectReference` points at it). It has nothing to
do with the finished app. `HMBridge.exe` is published self-contained
(`SelfContained=true`, `PublishSingleFile=true`), so `HIDMaestro.Core`
and everything else it needs is already compiled into that one `.exe`.

**If you have a `third_party/HIDMaestro` folder sitting inside your
*distributed/unzipped* `VaderMapper-v...` app folder, delete it.** It
isn't used at runtime and never will be — the CI job checks it out fresh
into the GitHub-hosted runner's own temporary workspace every run and
that copy is thrown away when the run ends. Only `bridge/HMBridge.exe`
itself belongs in the shipped app.

## Option A — GitHub Actions (recommended, nothing to install)

`.github/workflows/build.yml` has a `build_bridge` job that does the
whole build for you on a Microsoft-hosted runner:

1. Push this repo to GitHub (a throwaway test repo is fine).
2. Go to **Actions → Build Windows Executables → Run workflow**.
3. Leave **"Also build HMBridge.exe"** checked (it's on by default) and
   click **Run workflow**.
4. Download the `VaderMapper-v...` artifact from the finished run.
   `bridge/HMBridge.exe` is already inside it, next to the two `.exe`
   files — nothing further to assemble, and nothing to download or
   install on your own machine.

### Why this works without an EWDK download

`build_bridge` pins `runs-on: windows-2022` rather than `windows-latest`.
That specific GitHub-hosted image ships with Visual Studio 2022 and WDK
`10.0.26100.0` already installed — exactly the toolchain HIDMaestro's own
`scripts/build_all.cmd` expects — so the workflow calls that script
directly with no separate WDK/EWDK ISO download step.

### If the preinstalled WDK disappears

GitHub does occasionally change what's preinstalled on its runner images.
If `build_bridge` starts failing at the "Confirm WDK 10.0.26100.0 is
present" step, the fallback is the Enterprise WDK (EWDK) — a
self-contained, ISO-based build environment Microsoft publishes
specifically for CI use. In that case: find the current EWDK ISO link for
build `10.0.26100.0` at
<https://learn.microsoft.com/en-us/windows-hardware/drivers/download-the-wdk>
(Microsoft rotates these URLs), add a step that downloads and mounts it,
and replace `scripts\build_all.cmd` with the EWDK's own
`LaunchBuildEnv.cmd` followed by the same build commands — see
<https://learn.microsoft.com/en-us/windows-hardware/drivers/develop/using-the-enterprise-wdk>.

## Option B — build locally (only if you want to iterate on Program.cs)

You don't need this for a one-off build — Option A covers that. This is
for actively developing/debugging the bridge itself.

Requirements: Visual Studio 2022 (Community is fine) with the "Desktop
development with C++" workload, Windows Driver Kit `10.0.26100.0`, and
the .NET 10 SDK.

```bat
git clone https://github.com/hifihedgehog/HIDMaestro third_party\HIDMaestro
cd third_party\HIDMaestro
scripts\build_all.cmd
cd ..\..\bridge\HMBridge
dotnet publish -c Release
```

`service/mapping/virtual_controller.py` searches
`bridge/HMBridge/bin/**/HMBridge.exe` when running the Python service
from source, so this output location is picked up automatically. Delete
`third_party/HIDMaestro` once the build succeeds — you don't need to keep
it around, and you don't need to ship it (see above).

## Elevation

`CreateController()` requires an elevated (administrator) caller —
**every time it runs, not just once for the initial driver install.**
This is a HIDMaestro requirement, confirmed directly in its own source
(its test/probe suite is full of `// Requires elevation (CreateController)`
comments), not something specific to this bridge.

`VaderService.exe` itself deliberately stays unelevated (autostart, tray
behaviour and hotkeys are unaffected by any of this). Windows has no
supported way to hand an *existing unelevated* process's stdin/stdout
pipes to a *newly elevated* one — a UAC elevation always spawns a
completely separate process — so the transport can't be a plain
subprocess pipe if HMBridge needs elevation and VaderService doesn't.

The fix: `virtual_controller.py` creates a named pipe **before** starting
anything, then launches `HMBridge.exe` elevated via `ShellExecuteEx`'s
`"runas"` verb — the same call that shows the UAC consent prompt when you
right-click → "Run as administrator" yourself. The elevated `HMBridge.exe`
then connects to that already-open pipe as a client (named pipes cross
the elevation boundary fine in this direction — a low-privilege process
creating the pipe and a higher-privilege one connecting to it — unlike
window messages, which UIPI does block cross-elevation). All of `Program.cs`'s
actual command handling is unchanged; only the I/O layer moved from
`Console.In`/`Console.Out` to a `NamedPipeClientStream`.

**Practical effect:** once per `VaderService.exe` launch (not once per
button press), Windows shows one UAC consent prompt for `HMBridge.exe` —
the same prompt you'd see running it manually as Administrator. This is
inherent to HIDMaestro's driver model; there's no way to make it silent
while keeping the rest of the app unelevated. If you'd rather have zero
prompts and don't mind the whole app running elevated instead — including
switching autostart from the registry Run key to a Scheduled Task, since
Run-key entries don't elevate — that's a bigger, separate change and
isn't implemented here; set `"virtual_controller_enabled": false` in
`config.json`'s settings if you'd rather just skip the prompt entirely
and not use this feature at all.

## Things worth double-checking once you have a build

- **VID/PID collision.** `Program.cs` uses `0x1209`/`0x5051`, a
  placeholder from the pid.codes open allocation range. Worth checking
  against HIDMaestro's own profile catalog before relying on this.
- **The UAC prompt itself.** `ShellExecuteEx` is called with
  `SEE_MASK_FLAG_NO_UI`, which suppresses *Explorer's own* error dialog
  on failure (declined elevation, bad path, ...) — `virtual_controller.py`
  handles that failure itself and just leaves `is_available` False. The
  actual UAC consent dialog is a separate, unsuppressable system prompt
  and will still appear normally.
