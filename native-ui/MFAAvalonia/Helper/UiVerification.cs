using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Logging;
using Avalonia.Media.Imaging;
using Avalonia.Platform;
using Avalonia.Styling;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.Views.Pages;
using Newtonsoft.Json;
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading.Tasks;

namespace MFAAvalonia.Helper;

/// <summary>
/// Opt-in release verification: <c>--ui-verify &lt;dir&gt; [--ui-verify-exit] [--ui-verify-inject-failure]</c>.
/// Walks themes, window sizes, pages, settings sections and drop-downs of the real window, captures the
/// native window (Win32 PrintWindow) and asserts what each page actually renders. Binding errors,
/// unhandled exceptions, failed captures and failed assertions make the run fail: the report says
/// <c>"result": "fail"</c> and the process exit code is 1 (set via Environment.ExitCode, because the
/// desktop entry point returns void). Fixed settle waits are kept apart from the measured
/// navigate-to-rendered-frame time. Inert without the argument; only the theme is restored afterwards.
/// </summary>
public static class UiVerification
{
    public static string? OutputDirectory
    {
        get
        {
            var args = Environment.GetCommandLineArgs().ToList();
            var i = args.IndexOf("--ui-verify");
            return i >= 0 && i + 1 < args.Count ? args[i + 1] : null;
        }
    }

    private static bool HasArg(string name) => Environment.GetCommandLineArgs().Contains(name);

    private static readonly ConcurrentQueue<string> Errors = new();
    private static readonly List<object> Checks = new();
    private static bool _prepared;

    /// <summary>Installs error capture as early as possible (called from App.Initialize).</summary>
    public static void Prepare()
    {
        if (_prepared || OutputDirectory == null) return;
        _prepared = true;
        Avalonia.Logging.Logger.Sink = new CapturingSink(Avalonia.Logging.Logger.Sink);
        AppDomain.CurrentDomain.UnhandledException += (_, e) => Errors.Enqueue("unhandled: " + e.ExceptionObject);
        TaskScheduler.UnobservedTaskException += (_, e) =>
        {
            var root = e.Exception?.GetBaseException();
            // Headless Linux hosts have no X display for the global hotkey hook; Windows keeps this fatal.
            if (!OperatingSystem.IsWindows() && root?.Message.Contains("ErrorXOpenDisplay") == true) return;
            Errors.Enqueue("unobserved task: " + root);
        };
        Dispatcher.UIThread.UnhandledException += (_, e) => Errors.Enqueue("dispatcher: " + e.Exception);
    }

    /// <summary>Records binding and layout errors reported through Avalonia's logger.</summary>
    private sealed class CapturingSink(ILogSink? inner) : ILogSink
    {
        public bool IsEnabled(LogEventLevel level, string area) =>
            level >= LogEventLevel.Warning && area is LogArea.Binding or LogArea.Property or LogArea.Layout or LogArea.Control
            || (inner?.IsEnabled(level, area) ?? false);

        public void Log(LogEventLevel level, string area, object? source, string messageTemplate) => Log(level, area, source, messageTemplate, []);

        public void Log(LogEventLevel level, string area, object? source, string messageTemplate, params object?[] propertyValues)
        {
            if (level >= LogEventLevel.Warning && area is LogArea.Binding or LogArea.Property or LogArea.Layout or LogArea.Control)
            {
                var text = messageTemplate;
                for (var i = 0; i < propertyValues.Length; i++)
                {
                    var start = text.IndexOf('{');
                    var end = start < 0 ? -1 : text.IndexOf('}', start);
                    if (end < 0) break;
                    text = text[..start] + propertyValues[i] + text[(end + 1)..];
                }
                Errors.Enqueue($"{area} {level}: {text} (source {source?.GetType().Name})");
            }
            if (inner?.IsEnabled(level, area) == true) inner.Log(level, area, source, messageTemplate, propertyValues);
        }
    }

    private static void Check(bool ok, string name, string? detail = null)
    {
        lock (Checks) Checks.Add(new { name, ok, detail });
    }

    public static void StartIfRequested(Window window)
    {
        var dir = OutputDirectory;
        if (dir == null) return;
        Prepare();
        Directory.CreateDirectory(dir);
        Dispatcher.UIThread.Post(async () =>
        {
            var report = new Dictionary<string, object?>();
            var pass = false;
            try
            {
                try { await Run(window, dir, report); }
                catch (Exception ex) { report["fatal"] = ex.ToString(); }
                List<object> checks;
                lock (Checks) checks = Checks.ToList();
                var errors = Errors.ToList();
                var failed = checks.Where(c => !(bool)c.GetType().GetProperty("ok")!.GetValue(c)!).ToList();
                pass = !report.ContainsKey("fatal") && errors.Count == 0 && failed.Count == 0 && checks.Count > 0;
                report["result"] = pass ? "pass" : "fail";
                report["errors"] = errors;
                report["failedChecks"] = failed;
                report["checks"] = checks;
                File.WriteAllText(Path.Combine(dir, "report.json"), JsonConvert.SerializeObject(report, Formatting.Indented));
            }
            catch (Exception ex)
            {
                // Reporting itself failed: still leave a failing report behind and fail the process.
                pass = false;
                try { File.WriteAllText(Path.Combine(dir, "report.json"), JsonConvert.SerializeObject(new { result = "fail", fatal = ex.ToString() }, Formatting.Indented)); }
                catch (IOException) { }
            }
            Environment.ExitCode = pass ? 0 : 1;
            if (HasArg("--ui-verify-exit"))
                (Application.Current?.ApplicationLifetime as IClassicDesktopStyleApplicationLifetime)?.Shutdown(Environment.ExitCode);
        }, DispatcherPriority.Background);
    }

    private static async Task Delay(int ms)
    {
        var end = DateTime.UtcNow.AddMilliseconds(ms);
        while (DateTime.UtcNow < end) await Task.Delay(30);
    }

    /// <summary>Time from a UI change until layout has run and the next frame was rendered.</summary>
    private static async Task<double> UntilRendered(Window window, Action change)
    {
        var sw = Stopwatch.StartNew();
        change();
        var frame = new TaskCompletionSource();
        Dispatcher.UIThread.Post(() => window.RequestAnimationFrame(_ => frame.TrySetResult()), DispatcherPriority.Render);
        var done = await Task.WhenAny(frame.Task, Task.Delay(10000));
        Check(done == frame.Task, "frame rendered after change");
        return sw.Elapsed.TotalMilliseconds;
    }

    private static readonly string[] Pages =
        [LiquidPages.Tasks, LiquidPages.CardRules, LiquidPages.Drinks, LiquidPages.CardList, LiquidPages.Monitor, LiquidPages.Screenshot, LiquidPages.Settings];

    private static readonly string[] SettingSections = ["gui", "config", "timer", "perf", "game", "connect", "start", "notify", "hotkey", "update", "about"];

    private static async Task Run(Window window, string dir, Dictionary<string, object?> report)
    {
        await Delay(4000);
        report["renderScaling"] = window.RenderScaling;
        report["os"] = RuntimeInformation.OSDescription;
        report["version"] = typeof(UiVerification).Assembly.GetName().Version?.ToString();
        var screen = window.Screens.ScreenFromWindow(window) ?? window.Screens.Primary;
        if (screen != null)
            report["screen"] = new { bounds = screen.Bounds.ToString(), workingArea = screen.WorkingArea.ToString(), screen.Scaling };
        if (HasArg("--ui-verify-inject-failure"))
        {
            // Self-test of the failure path: the run must end with result "fail" and exit code 1.
            Check(false, "injected failure (--ui-verify-inject-failure)");
            return;
        }

        var shots = new List<string>();
        var renderMs = new Dictionary<string, double>();
        var actualSizes = new Dictionary<string, string>();
        var blur = new Dictionary<string, bool?>();
        var original = Instances.GuiSettingsUserControlModel.BaseTheme;
        foreach (var (w, h) in new[] { (1280, 800), (1600, 1000) })
        {
            window.WindowState = WindowState.Normal;
            window.Width = w; window.Height = h;
            await Delay(800);
            actualSizes[$"{w}x{h}"] = $"{window.ClientSize.Width:0}x{window.ClientSize.Height:0}";
            Check(Math.Abs(window.ClientSize.Width - w) < 1 && Math.Abs(window.ClientSize.Height - h) < 1, $"window {w}x{h} not clamped", actualSizes[$"{w}x{h}"]);
            foreach (var theme in new[] { ThemeVariant.Light, ThemeVariant.Dark })
            {
                Instances.GuiSettingsUserControlModel.BaseTheme = theme;
                await Delay(900);
                var prefix = $"{w}x{h}-{theme}";
                foreach (var page in Pages)
                {
                    renderMs[$"{prefix}-{page}"] = await UntilRendered(window, () => LiquidNavigation.Navigate(page));
                    await Delay(900); // settle (thumbnails, transitions); not part of the measurement
                    AssertPage(window, page, prefix);
                    shots.Add(Capture(window, Path.Combine(dir, $"{prefix}-{page}.png")));
                    if (w == 1600 && theme == ThemeVariant.Light)
                        await CheckDropDowns(window, dir, $"{prefix}-{page}", shots);
                }
                if (w == 1280)
                {
                    foreach (var section in SettingSections)
                    {
                        LiquidNavigation.NavigateSettings(section);
                        await Delay(500);
                        AssertSettingsSection(window, section, prefix);
                        shots.Add(Capture(window, Path.Combine(dir, $"{prefix}-settings-{section}.png")));
                    }
                }
                LiquidNavigation.Navigate(LiquidPages.Tasks);
                await Delay(600);
                // A dialog exercises the backdrop blur (and the dialog look) on the real compositor.
                LiquidModal? modal = null;
                modal = LiquidModal.Show(window, LiquidModal.Layout(ExText.T("界面检查"), null,
                    new TextBlock { Text = ExText.T("背景模糊与弹窗样式"), TextWrapping = Avalonia.Media.TextWrapping.Wrap }, null, () => modal?.Close()), 420);
                await Delay(900);
                shots.Add(Capture(window, Path.Combine(dir, $"{prefix}-dialog.png")));
                blur[prefix] = BackdropBlur.LastFrameSupported;
                Check(BackdropBlur.LastFrameSupported == true, $"{prefix} backdrop blur rendered");
                modal?.Close();
                await Delay(500);
            }
        }
        window.WindowState = WindowState.Maximized;
        await Delay(1200);
        shots.Add(Capture(window, Path.Combine(dir, "maximized-tasks.png")));
        window.WindowState = WindowState.Normal;
        Instances.GuiSettingsUserControlModel.BaseTheme = original;
        await Delay(500);
        report["shots"] = shots;
        report["navigateToRenderedFrameMs"] = renderMs;
        report["actualClientSizes"] = actualSizes;
        report["backdropBlurByDialog"] = blur;
        report["managedMemoryMB"] = GC.GetTotalMemory(false) / 1048576.0;
        report["workingSetMB"] = Process.GetCurrentProcess().WorkingSet64 / 1048576.0;
    }

    private static Control? VisiblePage(Window window, Type type) =>
        window.GetVisualDescendants().OfType<Control>().FirstOrDefault(c => type.IsInstanceOfType(c) && c.IsEffectivelyVisible && c.Bounds.Width > 0);

    private static bool Shown(Control? c) => c != null && c.IsEffectivelyVisible && c.Bounds.Width >= 1 && c.Bounds.Height >= 1;

    private static void AssertPage(Window window, string page, string prefix)
    {
        var name = $"{prefix}-{page}";
        switch (page)
        {
            case LiquidPages.Tasks:
            {
                var tq = VisiblePage(window, typeof(TaskQueueView));
                Check(tq != null, name + " page shown");
                if (tq == null) return;
                var panels = tq.GetVisualDescendants().OfType<BoardPanel>().ToList();
                foreach (var id in new[] { "task_list", "task_desc", "task_settings", "log" })
                    Check(Shown(panels.FirstOrDefault(p => p.PanelId == id)), $"{name} panel {id} visible");
                break;
            }
            case LiquidPages.CardRules:
            {
                var view = VisiblePage(window, typeof(CardRulesView));
                Check(view != null, name + " page shown");
                if (view == null) return;
                Check(view.GetVisualDescendants().OfType<CardRuleCurve>().Any(Shown), name + " curve visible");
                Check(view.GetVisualDescendants().OfType<Button>().Any(b => b.Classes.Contains("lg-primary") && Shown(b)), name + " save button visible");
                break;
            }
            case LiquidPages.Drinks:
                Check(VisiblePage(window, typeof(DrinkSettingsView)) is { } d && d.GetVisualDescendants().OfType<ComboBox>().Any(Shown), name + " drink rows visible");
                break;
            case LiquidPages.CardList:
                Check(VisiblePage(window, typeof(CardListSettingsView)) is { } l && l.GetVisualDescendants().OfType<Button>().Count(b => b.Classes.Contains("lg-catalog-card") && Shown(b)) > 0,
                    name + " catalog tiles visible");
                break;
            case LiquidPages.Monitor:
                Check(VisiblePage(window, typeof(MonitorView)) != null, name + " page shown");
                break;
            case LiquidPages.Screenshot:
                Check(VisiblePage(window, typeof(ScreenshotView)) != null, name + " page shown");
                break;
            case LiquidPages.Settings:
                Check(VisiblePage(window, typeof(SettingsView)) != null, name + " page shown");
                break;
        }
        AssertNoEmptyRows(window, name);
    }

    /// <summary>Every visible setting row must show its control (or footer) with a real size.</summary>
    private static void AssertNoEmptyRows(Window window, string name)
    {
        foreach (var row in window.GetVisualDescendants().OfType<SettingRow>().Where(Shown))
        {
            var content = row.Content as Control;
            var footer = row.Footer as Control;
            // A part explicitly hidden by its own IsVisible (e.g. the opacity slider without an image) is intentional;
            // a part that claims to be visible but renders with no size is not.
            bool PartOk(Control? part) => part == null || !part.IsVisible || Shown(part);
            var ok = PartOk(content) && PartOk(footer) && (content != null || footer != null || row.Content is string);
            Check(ok, $"{name} setting row '{row.Title}' shows its control");
        }
    }

    private static void AssertSettingsSection(Window window, string section, string prefix)
    {
        var view = VisiblePage(window, typeof(SettingsView));
        var host = view?.FindControl<Panel>("SectionHost");
        Check(host != null && host.Children.OfType<Control>().Any(Shown), $"{prefix} settings {section} content visible");
        AssertNoEmptyRows(window, $"{prefix}-settings-{section}");
    }

    /// <summary>Opens each visible drop-down: every item must get a visible container inside an opaque popup.</summary>
    private static async Task CheckDropDowns(Window window, string dir, string name, List<string> shots)
    {
        var combos = window.GetVisualDescendants().OfType<ComboBox>().Where(c => Shown(c) && c.IsEnabled).Take(6).ToList();
        var index = 0;
        foreach (var combo in combos)
        {
            var label = Avalonia.Automation.AutomationProperties.GetName(combo) ?? combo.Name ?? $"#{index}";
            var count = combo.ItemCount;
            combo.IsDropDownOpen = true;
            await Delay(450);
            var containers = Enumerable.Range(0, count).Select(combo.ContainerFromIndex).OfType<Control>().ToList();
            var visible = containers.Count(Shown);
            Check(combo.IsDropDownOpen, $"{name} drop-down '{label}' opens");
            Check(count == 0 || visible >= Math.Min(count, 3), $"{name} drop-down '{label}' shows items", $"items={count} visibleContainers={visible}");
            if (index == 0) shots.Add(Capture(window, Path.Combine(dir, $"{name}-dropdown.png")));
            combo.IsDropDownOpen = false;
            await Delay(250);
            index++;
        }
    }

    /// <summary>Captures the native window. On Windows uses PrintWindow(PW_RENDERFULLCONTENT).</summary>
    private static string Capture(Window window, string path)
    {
        if (OperatingSystem.IsWindows() && window.TryGetPlatformHandle()?.Handle is { } hwnd && hwnd != IntPtr.Zero)
        {
            try { CaptureWin32(hwnd, path); return Path.GetFileName(path); }
            catch (Exception ex) { Check(false, "capture " + Path.GetFileName(path), ex.Message); }
        }
        try
        {
            var scale = window.RenderScaling;
            using var bitmap = new RenderTargetBitmap(new PixelSize((int)(window.Bounds.Width * scale), (int)(window.Bounds.Height * scale)), new Vector(96 * scale, 96 * scale));
            bitmap.Render(window);
            bitmap.Save(path);
            if (OperatingSystem.IsWindows()) Check(false, "capture " + Path.GetFileName(path), "PrintWindow unavailable, fell back to render");
            return Path.GetFileName(path) + " (render)";
        }
        catch (Exception ex)
        {
            Check(false, "capture " + Path.GetFileName(path), ex.Message);
            return Path.GetFileName(path) + " (failed)";
        }
    }

    [StructLayout(LayoutKind.Sequential)] private struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)]
    private struct BITMAPINFOHEADER
    {
        public uint biSize; public int biWidth, biHeight; public ushort biPlanes, biBitCount; public uint biCompression, biSizeImage;
        public int biXPelsPerMeter, biYPelsPerMeter; public uint biClrUsed, biClrImportant;
    }
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] private static extern bool PrintWindow(IntPtr hWnd, IntPtr hdc, uint flags);
    [DllImport("user32.dll")] private static extern IntPtr GetDC(IntPtr hWnd);
    [DllImport("user32.dll")] private static extern int ReleaseDC(IntPtr hWnd, IntPtr hdc);
    [DllImport("gdi32.dll")] private static extern IntPtr CreateCompatibleDC(IntPtr hdc);
    [DllImport("gdi32.dll")] private static extern IntPtr CreateDIBSection(IntPtr hdc, ref BITMAPINFOHEADER bmi, uint usage, out IntPtr bits, IntPtr section, uint offset);
    [DllImport("gdi32.dll")] private static extern IntPtr SelectObject(IntPtr hdc, IntPtr obj);
    [DllImport("gdi32.dll")] private static extern bool DeleteObject(IntPtr obj);
    [DllImport("gdi32.dll")] private static extern bool DeleteDC(IntPtr hdc);

    private static void CaptureWin32(IntPtr hwnd, string path)
    {
        GetWindowRect(hwnd, out var r);
        int w = r.Right - r.Left, h = r.Bottom - r.Top;
        if (w <= 0 || h <= 0) throw new InvalidOperationException("empty window rect");
        var screen = GetDC(IntPtr.Zero);
        var mem = CreateCompatibleDC(screen);
        var bmi = new BITMAPINFOHEADER { biSize = (uint)Marshal.SizeOf<BITMAPINFOHEADER>(), biWidth = w, biHeight = -h, biPlanes = 1, biBitCount = 32 };
        var dib = CreateDIBSection(mem, ref bmi, 0, out var bits, IntPtr.Zero, 0);
        var old = SelectObject(mem, dib);
        try
        {
            if (!PrintWindow(hwnd, mem, 2)) throw new InvalidOperationException("PrintWindow failed");
            using var wb = new WriteableBitmap(new PixelSize(w, h), new Vector(96, 96), PixelFormat.Bgra8888, AlphaFormat.Opaque);
            using (var fb = wb.Lock())
            {
                var row = w * 4;
                var buffer = new byte[row];
                for (int y = 0; y < h; y++)
                {
                    Marshal.Copy(bits + y * row, buffer, 0, row);
                    for (int x = 3; x < row; x += 4) buffer[x] = 255;
                    Marshal.Copy(buffer, 0, fb.Address + y * fb.RowBytes, row);
                }
            }
            wb.Save(path);
        }
        finally
        {
            SelectObject(mem, old);
            DeleteObject(dib);
            DeleteDC(mem);
            ReleaseDC(IntPtr.Zero, screen);
        }
    }
}
