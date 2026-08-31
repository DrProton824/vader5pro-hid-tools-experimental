// bridge/HMBridge/Program.cs
//
// Line-oriented stdin/stdout bridge between the Vader Remapper Python
// service and the HIDMaestro SDK. This is the ONLY place in the whole
// application that references HIDMaestro.Core directly -- see
// service/mapping/virtual_controller.py on the Python side, which knows
// nothing about HIDMaestro and only ever speaks this line protocol.
//
// Protocol
// ────────
// One command per line on stdin, one reply per line on stdout:
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
// Replies are exactly one line: "ok" or "error <message>". On successful
// startup (profile built, controller created, driver installed if
// needed) the process writes one "ok" line before reading its first
// command -- VirtualController.py waits for that line to decide whether
// the bridge is usable at all.
//
// Button layout
// ─────────────
// VaderButtons below fixes the bit order used for the raw HMButton
// bitmask. Sticks/triggers use HidDescriptorBuilder.AddStick()/
// AddTrigger(), and the actual HMAxis values are read back from
// profile.Sticks[i]/profile.Triggers[i] after CreateController() --
// the discovery pattern HIDMaestro's own doc comments recommend, so this
// file never hardcodes which HID usage code (X, Z, Rx, ...) ended up
// assigned to which stick.
//
// NOTE: written against the HIDMaestro SDK source referenced in
// bridge/README.md. Not yet compiled or run against real hardware --
// see that file for what to double check first.

using System;
using System.Collections.Generic;
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

    private static int Main()
    {
        HMContext ctx;
        HMController controller;
        HMGamepadState state = default;
        state.Axes = new Dictionary<HMAxis, float>();

        HMAxis leftX, leftY, rightX, rightY, leftTrigger, rightTrigger;

        try
        {
            ctx = new HMContext();
            if (!ctx.IsDriverInstalled)
                ctx.InstallDriver();

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

            controller = ctx.CreateController(profile);

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
            Console.WriteLine($"error startup: {ex.Message}");
            return 1;
        }

        Console.WriteLine("ok");
        Console.Out.Flush();

        string? line;
        while ((line = Console.ReadLine()) != null)
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
                        Reply("ok");
                        break;

                    case "release" when parts.Length >= 2:
                        SetButton(ref state, parts[1], false);
                        controller.SubmitState(in state);
                        Reply("ok");
                        break;

                    case "hat" when parts.Length >= 2:
                        state.Hat = ParseHat(parts[1]);
                        controller.SubmitState(in state);
                        Reply("ok");
                        break;

                    case "axis" when parts.Length >= 4 && parts[1] == "left":
                        state.Axes![leftX] = float.Parse(parts[2]);
                        state.Axes![leftY] = float.Parse(parts[3]);
                        controller.SubmitState(in state);
                        Reply("ok");
                        break;

                    case "axis" when parts.Length >= 4 && parts[1] == "right":
                        state.Axes![rightX] = float.Parse(parts[2]);
                        state.Axes![rightY] = float.Parse(parts[3]);
                        controller.SubmitState(in state);
                        Reply("ok");
                        break;

                    case "trigger" when parts.Length >= 3 && parts[1] == "left":
                        state.Axes![leftTrigger] = float.Parse(parts[2]);
                        controller.SubmitState(in state);
                        Reply("ok");
                        break;

                    case "trigger" when parts.Length >= 3 && parts[1] == "right":
                        state.Axes![rightTrigger] = float.Parse(parts[2]);
                        controller.SubmitState(in state);
                        Reply("ok");
                        break;

                    case "quit":
                        Reply("ok");
                        goto shutdown;

                    default:
                        Reply($"error unrecognized command: {line}");
                        break;
                }
            }
            catch (Exception ex)
            {
                Reply($"error {ex.Message}");
            }
        }

    shutdown:
        controller.Dispose();
        ctx.Dispose();
        return 0;
    }

    private static void Reply(string message)
    {
        Console.WriteLine(message);
        Console.Out.Flush();
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
