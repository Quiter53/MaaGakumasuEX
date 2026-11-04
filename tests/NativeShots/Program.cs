using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Headless;
using Avalonia.Threading;
using MFAAvalonia;
using MFAAvalonia.Helper;

// Renders the real application window with the headless Skia backend.
// Usage: NativeShots <runtimeDir> <outputDir> [script]
internal static class Program
{
    [STAThread]
    public static int Main(string[] args)
    {
        Console.OutputEncoding = System.Text.Encoding.UTF8;
        if (args.Length < 2 || (args.Length > 2 && !Shots.IsSupportedScript(args[2])))
        {
            Console.Error.WriteLine("Usage: NativeShots <runtimeDir> <outputDir> [basic|all|sizes|small|taskopts|round4|round5|audit|tutorial|button|modal-frames|probe|verify|lang:<culture>]");
            return 2;
        }
        var runtime = Path.GetFullPath(args[0]);
        var output = Path.GetFullPath(args[1]);
        Directory.CreateDirectory(output);
        using var diagnostics = new AuditDiagnostics();
        Directory.SetCurrentDirectory(runtime);
        LoggerHelper.InitializeLogger();
        AppRuntime.Initialize(["--ui-test-offline"], "NativeShots_" + Guid.NewGuid().ToString("N"));
        var lifetime = new ClassicDesktopStyleApplicationLifetime { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        AppBuilder.Configure<App>()
            .UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing = false })
            .UseSkia()
            .WithInterFont()
            .SetupWithLifetime(lifetime);
        diagnostics.AttachDispatcher();
        AvaloniaSynchronizationContext.InstallIfNeeded();
        var window = lifetime.MainWindow ?? throw new InvalidOperationException("no main window");
        window.Width = 1600; window.Height = 1000;
        window.Show();
        Pump(2500);
        Shots.Run(window, output, args.Length > 2 ? args[2] : "basic");
        if (!diagnostics.Save(output)) Environment.ExitCode = 1;
        var result = Environment.ExitCode;
        lifetime.Shutdown(result);
        Environment.Exit(result);
        return result;
    }

    public static void Pump(int ms)
    {
        var frame = new DispatcherFrame();
        using var stop = DispatcherTimer.RunOnce(() => frame.Continue = false, TimeSpan.FromMilliseconds(ms));
        using var render = DispatcherTimer.Run(() =>
        {
            AvaloniaHeadlessPlatform.ForceRenderTimerTick();
            return true;
        }, TimeSpan.FromMilliseconds(16));
        // RunJobs only drains queued operations and leaves platform timer events pending.
        Dispatcher.UIThread.PushFrame(frame);
        Dispatcher.UIThread.RunJobs();
        AvaloniaHeadlessPlatform.ForceRenderTimerTick();
    }

    public static void Save(Window w, string path)
    {
        Pump(150);
        using var frame = w.CaptureRenderedFrame() ?? throw new InvalidOperationException("No rendered frame for " + path);
        frame.Save(path);
        Console.WriteLine("SHOT " + path);
    }
}
