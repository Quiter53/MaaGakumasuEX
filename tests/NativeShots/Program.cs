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
        var runtime = Path.GetFullPath(args[0]);
        var output = Path.GetFullPath(args[1]);
        Directory.CreateDirectory(output);
        Directory.SetCurrentDirectory(runtime);
        LoggerHelper.InitializeLogger();
        AppRuntime.Initialize(Array.Empty<string>(), "NativeShots_" + Guid.NewGuid().ToString("N"));
        var lifetime = new ClassicDesktopStyleApplicationLifetime { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        AppBuilder.Configure<App>()
            .UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing = false })
            .UseSkia()
            .WithInterFont()
            .SetupWithLifetime(lifetime);
        var window = lifetime.MainWindow ?? throw new InvalidOperationException("no main window");
        window.Width = 1600; window.Height = 1000;
        window.Show();
        Pump(2500);
        Shots.Run(window, output, args.Length > 2 ? args[2] : "basic");
        return 0;
    }

    public static void Pump(int ms)
    {
        var end = DateTime.UtcNow.AddMilliseconds(ms);
        while (DateTime.UtcNow < end)
        {
            Dispatcher.UIThread.RunJobs();
            AvaloniaHeadlessPlatform.ForceRenderTimerTick();
            Thread.Sleep(16);
        }
    }

    public static void Save(Window w, string path)
    {
        Pump(150);
        using var frame = w.CaptureRenderedFrame();
        frame?.Save(path);
        Console.WriteLine("SHOT " + path);
    }
}
