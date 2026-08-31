# HMBridge — build guide

`HMBridge.exe` is the only part of this project that touches HIDMaestro
directly. It's a small console process the Python service starts as a
subprocess (see `service/mapping/virtual_controller.py`) and talks to over
stdin/stdout.

**Status:** written against the real class shapes in the HIDMaestro source
(`HMContext`, `HMController`, `HMProfileBuilder`, `HidDescriptorBuilder`,
`HMGamepadState`, `HMButton`, `HMHat`, `HMAxis`), but it has not been
compiled or run against real hardware yet. The GitHub Actions build below
is the fastest way to find out if it compiles cleanly, without installing
anything locally.

## Option A — GitHub Actions (recommended, nothing to install)

`.github/workflows/build.yml` has a `build_bridge` job that does the whole
build for you on a Microsoft-hosted runner:

1. Push this repo to GitHub (a throwaway test repo is fine).
2. Go to **Actions → Build Windows Executables → Run workflow**.
3. Leave **"Also build HMBridge.exe"** checked (it's on by default) and
   click **Run workflow**.
4. Wait for the run to finish (the bridge build typically takes a few
   minutes — it compiles two small native DLLs, then the .NET SDK, then
   the bridge itself).
5. Download the `VaderMapper-v...` artifact from the finished run.
   `bridge/HMBridge.exe` is already inside it, next to the two `.exe`
   files — nothing further to assemble.

### Why this works without an EWDK download

`build_bridge` pins `runs-on: windows-2022` rather than `windows-latest`.
That specific GitHub-hosted image ships with **Visual Studio 2022 and WDK
10.0.26100.0 already installed** — exactly the toolchain HIDMaestro's own
`scripts/build_all.cmd` expects — so the workflow can call that script
directly with no separate WDK/EWDK ISO download step. (`windows-latest`
has moved to newer images over time, some of which don't ship the WDK the
same way, which is why the job pins the version explicitly rather than
using the floating label.)

### If the preinstalled WDK disappears

GitHub does occasionally change what's preinstalled on its runner images.
If the `build_bridge` job starts failing at the "Confirm WDK 10.0.26100.0
is present" step, the fallback is the Enterprise WDK (EWDK) — a
self-contained, ISO-based build environment Microsoft publishes
specifically for CI use, requiring no installation. In that case:

1. Find the current EWDK ISO download link for WDK build `10.0.26100.0`
   at <https://learn.microsoft.com/en-us/windows-hardware/drivers/download-the-wdk>
   (Microsoft rotates these URLs, so it's not hardcoded here).
2. Add a step to `build_bridge` before "Build HIDMaestro native driver +
   SDK" that downloads and mounts that ISO, then replaces
   `scripts\build_all.cmd` with the EWDK's own `LaunchBuildEnv.cmd`
   followed by the same build commands, per
   <https://learn.microsoft.com/en-us/windows-hardware/drivers/develop/using-the-enterprise-wdk>.

This bundle doesn't include that fallback step by default since the
directly-preinstalled path is faster and simpler while it keeps working.

## Option B — build locally (only if you want to iterate on Program.cs)

You do not need this for a one-off build — Option A covers that. This is
for actively developing/debugging the bridge itself.

Requirements: Visual Studio 2022 (Community is fine) with the "Desktop
development with C++" workload, Windows Driver Kit `10.0.26100.0`, and the
.NET 10 SDK.

```bat
git clone https://github.com/hifihedgehog/HIDMaestro third_party\HIDMaestro
cd third_party\HIDMaestro
scripts\build_all.cmd
cd ..\..\bridge\HMBridge
dotnet publish -c Release
```

`service/mapping/virtual_controller.py` searches
`bridge/HMBridge/bin/**/HMBridge.exe` when running the Python service from
source, so this output location is picked up automatically. Run
`HMBridge.exe` directly in a terminal first (as Administrator — virtual
device creation needs `SeLoadDriverPrivilege`) to sanity-check it prints
`ok` before wiring it into the full app; type `press A` and check `joy.cpl`
for a new "Vader 5 Pro Extended" device reacting, then `quit` to exit.

## Things worth double-checking once you have a build

- **VID/PID collision.** `Program.cs` uses `0x1209`/`0x5051`, a
  placeholder from the pid.codes open allocation range. Nothing here has
  checked it against every profile in HIDMaestro's own catalog — worth a
  look (`HIDMaestroTest.exe list` / `search`) before relying on this.
- **Admin requirement.** `InstallDriver()`/`CreateController()` both need
  elevation. `service/main.py` does not currently elevate the service
  process to request that automatically — see
  `docs/HIDMAESTRO_INTEGRATION_PLAN.md` for the proposed message-box based
  install flow, not implemented yet since it depends on this bridge
  working first.
- **Nullable/definite-assignment.** `Program.cs` assigns `ctx`/
  `controller` only inside a `try` block whose `catch` always returns —
  standard C# definite-assignment analysis accepts this, but if you
  restructure that method, keep the early-return-on-failure shape or the
  compiler will start requiring nullable types there again.
