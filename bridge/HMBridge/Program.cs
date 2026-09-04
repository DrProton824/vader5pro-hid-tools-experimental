// bridge/HMBridge/Program.cs
//
// Named-pipe bridge between the Vader Remapper Python service and the
// HIDMaestro SDK. This is the ONLY place in the whole application that
// references HIDMaestro.Core directly -- see
// service/mapping/virtual_controller.py on the Python side, which knows
// nothing about HIDMaestro and only ever speaks the line protocol below.
//
// Why a named pipe and not stdin/stdout
// ──────────────────────────────────────
// HIDMaestro's CreateController() requires an elevated caller every time
// it runs, not just once for driver install. VaderService.exe stays
// unelevated, so this process has to be launched as its own elevated
// child via ShellExecuteEx's "runas" verb -- and Windows has no way to
// hand an *existing* process's stdio pipes to a *newly elevated* one
// (UAC always creates a brand new process). virtual_controller.py
// therefore creates a named pipe server before launching this exe at
// all; this process's only job on the connection side is to connect to
// that already-open pipe as a client and take the pipe name from argv[0].
//
// Protocol
// ────────
// One command per line, one reply per line, both directions over the
// pipe:
//
//   press <button>              button name, e.g. "M1", "A", "LB"
//   release <button>
//   hat <direction>              centered | n | ne | e | se | s | sw | w | nw
//   axis left <x> <y>            0.0-1.0, 0.5 = center
//   axis right <x> <y>
//   trigger left <value>         0.0-1.0
//   trigger right <value>
//   quit
//
// Replies are exactly one line: "ok" or "error <message>". The first
// line this process sends after connecting is its own startup result --
// VirtualController.py reads that to decide whether the bridge is usable
// at all.
//
// Button layout
// ─────────────
// VaderButtons below fixes the bit order used for the raw HMButton
// bitmask. Sticks/triggers use HidDescriptorBuilder.AddStick()/
// AddTrigger(), and the actual HMAxis values are read back from
// profile.Sticks[i]/profile.Triggers[i] after CreateController() -- the
// discovery pattern HIDMaestro's own doc comments recommend, so this
// file never hardcodes which HID usage code (X, Z, Rx, ...) ended up
// assigned to which stick.
//
// NOTE: written against the HIDMaestro SDK source referenced in
// bridge/README.md. Not yet compiled or run against real hardware --
// see that file for what to double check first.

using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Pipes;
using HIDMaestro;

namespace VaderRemapper.HMBridge;

internal static class Program
{
    // Placeholder VID/PID for the virtual "Vader 5 Pro Extended"
    // controller -- from the pid.codes open allocation range, chosen to
    // avoid colliding with the real Vader 5 Pro (VID 0x37D7) or any
    // registered vendor. Change only here if a collision turns up during
    // testing.
    private const ushort VirtualVid = 0x1209;
    private const ushort VirtualPid = 0x5051;

    // How long to wait for the pipe server (already running by the time
    // this process starts -- see virtual_controller.py) to accept the
    // connection.
    private const int ConnectTimeoutMs = 15000;

    // Written next to this exe, best-effort. See virtual_controller.py's
    // matching bridge_debug.log on the Python side -- between the two,
    // a failed run should be diagnosable without guesswork.
    private static readonly string LogPath = Path.Combine(
        AppContext.BaseDirectory, "HMBridge_debug.log");

    private static void Log(string message)
    {
        try
        {
            File.AppendAllText(LogPath, $"{DateTime.Now:HH:mm:ss} {message}{Environment.NewLine}");
        }
        catch { /* best-effort -- logging must never be why this process crashes */ }
    }

    // Fixed bit order for the raw HMButton bitmask (identity ButtonMap --
    // HMProfileBuilder's default -- so bit index N here IS descriptor
    // button N). Everything except the 4 D-pad directions and the 2
    // triggers, which travel through the hat switch and analog axes
    // instead. Index order only matters internally to this process; the
    // Python side only ever sends button names as strings.
    private static readonly string[] VaderButtons =
    {
        "A", "B", "X", "Y",
        "LB", "RB",
        "SELECT", "START",
        "LS", "RS",
        "HOME",
        "M1", "M2", "M3", "M4",
        "LM", "RM",
        "C", "Z",
        "Arrow", "Circle",
    };

    private static int Main(string[] args)
    {
        Log($"Starting. args={string.Join(' ', args)}");

        bool isElevated;
        try
        {
            using var identity = System.Security.Principal.WindowsIdentity.GetCurrent();
            var principal = new System.Security.Principal.WindowsPrincipal(identity);
            isElevated = principal.IsInRole(System.Security.Principal.WindowsBuiltInRole.Administrator);
        }
        catch (Exception ex)
        {
            Log($"Could not determine elevation status: {ex.Message}");
            isElevated = false;
        }
        // This is the single most important line in this log file: it
        // answers definitively whether ShellExecuteEx's "runas" verb
        // actually elevated this process, which is otherwise impossible
        // to tell from the Python side or from Task Manager alone.
        Log($"Running elevated: {isElevated}");

        if (args.Length < 1 || string.IsNullOrWhiteSpace(args[0]))
        {
            Log("No pipe name given on the command line -- exiting.");
            return 1; // nothing to connect to, nowhere to report an error
        }

        string pipeName = args[0];

        NamedPipeClientStream pipe;
        try
        {
            pipe = new NamedPipeClientStream(".", pipeName, PipeDirection.InOut, PipeOptions.None);
            pipe.Connect(ConnectTimeoutMs);
            Log("Connected to pipe.");
        }
        catch (Exception ex)
        {
            Log($"Failed to connect to pipe '{pipeName}': {ex.Message}");
            return 1; // Python side's own connect timeout already covers this
        }

        using var reader = new StreamReader(pipe);
        using var writer = new StreamWriter(pipe) { AutoFlush = true };

        HMContext ctx;
        HMController controller;
        HMGamepadState state = default;
        state.Axes = new Dictionary<HMAxis, float>();

        HMAxis leftX, leftY, rightX, rightY, leftTrigger, rightTrigger;

        try
        {
            ctx = new HMContext();
            Log($"IsDriverInstalled: {ctx.IsDriverInstalled}");
            if (!ctx.IsDriverInstalled)
            {
                Log("Calling InstallDriver()...");
                ctx.InstallDriver();
                Log("InstallDriver() returned without throwing.");
            }

            var descriptor = new HidDescriptorBuilder()
                .Gamepad()
                .AddStick("Left")
                .AddStick("Right")
                .AddTrigger("Left")
                .AddTrigger("Right")
                .AddButtons(VaderButtons.Length)
                .AddHat();

            var profile = new HMProfileBuilder()
                .Id("vader5pro-extended")
                .Name("Vader 5 Pro Extended (Vader Remapper)")
                .Vendor("Vader Remapper")
                .Vid(VirtualVid)
                .Pid(VirtualPid)
                .ProductString("Vader 5 Pro Extended")
                .Type("gamepad")
                .Connection("usb")
                .FromDescriptorBuilder(descriptor)
                .Build();

            Log("Calling CreateController()...");
            controller = ctx.CreateController(profile);
            Log("CreateController() succeeded.");

            leftX = leftY = rightX = rightY = leftTrigger = rightTrigger = HMAxis.None;

            if (profile.Sticks.Count > 0)
            {
                leftX = profile.Sticks[0].XAxis;
                leftY = profile.Sticks[0].YAxis;
            }
            if (profile.Sticks.Count > 1)
            {
                rightX = profile.Sticks[1].XAxis;
                rightY = profile.Sticks[1].YAxis;
            }
            if (profile.Triggers.Count > 0)
                leftTrigger = profile.Triggers[0].Axis;
            if (profile.Triggers.Count > 1)
                rightTrigger = profile.Triggers[1].Axis;

            state.Axes[leftX] = 0.5f;
            state.Axes[leftY] = 0.5f;
            state.Axes[rightX] = 0.5f;
            state.Axes[rightY] = 0.5f;
            state.Axes[leftTrigger] = 0f;
            state.Axes[rightTrigger] = 0f;
            controller.SubmitState(in state);
        }
        catch (Exception ex)
        {
            Log($"Startup failed: {ex}");
            try { writer.WriteLine($"error startup: {ex.Message}"); } catch { /* pipe already gone */ }
            return 1;
        }

        // "elevated=" is appended so the Python-side log captures the
        // same answer without needing to cross-reference this file --
        // VirtualController._start() only checks the reply starts with
        // "ok", so this is purely informational, not a protocol change.
        writer.WriteLine($"ok elevated={isElevated}");
        Log("Startup complete, entering command loop.");

        string? line;
        while ((line = reader.ReadLine()) != null)
        {
            var parts = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length == 0)
                continue;

            try
            {
                switch (parts[0])
                {
                    case "press" when parts.Length >= 2:
                        SetButton(ref state, parts[1], true);
                        controller.SubmitState(in state);
                        writer.WriteLine("ok");
                        break;

                    case "release" when parts.Length >= 2:
                        SetButton(ref state, parts[1], false);
                        controller.SubmitState(in state);
                        writer.WriteLine("ok");
                        break;

                    case "hat" when parts.Length >= 2:
                        state.Hat = ParseHat(parts[1]);
                        controller.SubmitState(in state);
                        writer.WriteLine("ok");
                        break;

                    case "axis" when parts.Length >= 4 && parts[1] == "left":
                        state.Axes![leftX] = float.Parse(parts[2]);
                        state.Axes![leftY] = float.Parse(parts[3]);
                        controller.SubmitState(in state);
                        writer.WriteLine("ok");
                        break;

                    case "axis" when parts.Length >= 4 && parts[1] == "right":
                        state.Axes![rightX] = float.Parse(parts[2]);
                        state.Axes![rightY] = float.Parse(parts[3]);
                        controller.SubmitState(in state);
                        writer.WriteLine("ok");
                        break;

                    case "trigger" when parts.Length >= 3 && parts[1] == "left":
                        state.Axes![leftTrigger] = float.Parse(parts[2]);
                        controller.SubmitState(in state);
                        writer.WriteLine("ok");
                        break;

                    case "trigger" when parts.Length >= 3 && parts[1] == "right":
                        state.Axes![rightTrigger] = float.Parse(parts[2]);
                        controller.SubmitState(in state);
                        writer.WriteLine("ok");
                        break;

                    case "quit":
                        writer.WriteLine("ok");
                        goto shutdown;

                    default:
                        writer.WriteLine($"error unrecognized command: {line}");
                        break;
                }
            }
            catch (Exception ex)
            {
                Log($"Command '{line}' failed: {ex.Message}");
                writer.WriteLine($"error {ex.Message}");
            }
        }

        Log("Pipe closed by the other end (read returned null) -- shutting down.");

    shutdown:
        Log("Shutting down.");
        controller.Dispose();
        ctx.Dispose();
        return 0;
    }

    private static void SetButton(ref HMGamepadState state, string name, bool pressed)
    {
        int index = Array.IndexOf(VaderButtons, name);
        if (index < 0)
            throw new ArgumentException($"unknown button: {name}");

        uint bit = 1u << index;
        state.Buttons = pressed
            ? (HMButton)((uint)state.Buttons | bit)
            : (HMButton)((uint)state.Buttons & ~bit);
    }

    private static HMHat ParseHat(string direction) => direction switch
    {
        "centered" => HMHat.None,
        "n" => HMHat.North,
        "ne" => HMHat.NorthEast,
        "e" => HMHat.East,
        "se" => HMHat.SouthEast,
        "s" => HMHat.South,
        "sw" => HMHat.SouthWest,
        "w" => HMHat.West,
        "nw" => HMHat.NorthWest,
        _ => HMHat.None,
    };
}
