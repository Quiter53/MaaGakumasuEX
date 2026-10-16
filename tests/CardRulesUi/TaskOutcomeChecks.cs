using System.Diagnostics;
using System.Reflection;
using System.Runtime.CompilerServices;
using MFAAvalonia.Extensions.MaaFW;

internal static class TaskOutcomeChecks
{
    public static void Run(Action<bool, string> check)
    {
        // Test the production notification gate without starting devices or
        // constructing notification settings. No network requests are made.
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
        var type = typeof(MaaProcessor);
        var processor = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(type);
        var manual = type.GetField("_manualStopRequested", flags)!;
        var gate = type.GetMethod("ShouldSendTaskOutcome", flags)!;
        var send = type.GetMethod("SendTaskOutcomeAsync", flags)!;
        foreach (bool cancelled in new[] { false, true })
        foreach (string outcome in new[] { "success", "failed", "stopped" })
        {
            manual.SetValue(processor, cancelled);
            bool expected = !cancelled && outcome != "stopped";
            check((bool)gate.Invoke(processor, [outcome])! == expected, $"notify {outcome}, manual={cancelled}");
            if (!expected)
            {
                ((Task)send.Invoke(processor, [outcome])!).GetAwaiter().GetResult();
                check(true, "suppressed result returns before settings/network access");
            }
        }
        manual.SetValue(processor, true);
        type.GetField("_taskClock", flags)!.SetValue(processor, new Stopwatch());
        type.GetMethod("ResetTaskOutcome", flags)!.Invoke(processor, null);
        check(!(bool)manual.GetValue(processor)!, "new run clears manual cancellation");
        check((bool)gate.Invoke(processor, ["success"])!, "next automatic success notifies");
        type.GetField("_watchdogStopped", flags)!.SetValue(processor, true);
        check((bool)gate.Invoke(processor, ["failed"])!, "automatic watchdog failure still notifies");
    }
}
