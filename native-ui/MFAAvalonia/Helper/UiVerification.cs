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
        // Binding, property and layout warnings fail the run. Control-area messages count only as errors: Avalonia
        // warns "PlatformImpl is null" when a click closes a popup window, which is expected.
        private static bool Captured(LogEventLevel level, string area) =>
            (level >= LogEventLevel.Warning && area is LogArea.Binding or LogArea.Property or LogArea.Layout)
            || (level >= LogEventLevel.Error && area == LogArea.Control);

        public bool IsEnabled(LogEventLevel level, string area) => Captured(level, area) || (inner?.IsEnabled(level, area) ?? false);

        public void Log(LogEventLevel level, string area, object? source, string messageTemplate) => Log(level, area, source, messageTemplate, []);

        public void Log(LogEventLevel level, string area, object? source, string messageTemplate, params object?[] propertyValues)
        {
            if (Captured(level, area))
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
        if (HasArg("--ui-verify-interactive") && OperatingSystem.IsWindows())
        {
            window.WindowState = WindowState.Normal;
            window.Width = 1600; window.Height = 1000;
            Instances.GuiSettingsUserControlModel.BaseTheme = ThemeVariant.Light;
            await Delay(900);
            await RealInput.Run(window, dir, shots);
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

    /// <summary>
    /// Opens each visible drop-down. The popup must be open, on screen next to its combo box, fully
    /// opaque once its entrance animation is over, and show its items. On Windows the popup is its own
    /// native window, so the evidence shot is taken from the screen rather than with PrintWindow.
    /// </summary>
    private static async Task CheckDropDowns(Window window, string dir, string name, List<string> shots)
    {
        var combos = window.GetVisualDescendants().OfType<ComboBox>().Where(c => Shown(c) && c.IsEnabled).Take(6).ToList();
        var index = 0;
        foreach (var combo in combos)
        {
            var label = Avalonia.Automation.AutomationProperties.GetName(combo) ?? combo.Name ?? $"#{index}";
            var count = combo.ItemCount;
            combo.IsDropDownOpen = true;
            await Delay(600);
            var popup = combo.GetVisualDescendants().OfType<Avalonia.Controls.Primitives.Popup>().FirstOrDefault(p => p.Name == "PART_Popup");
            var surface = (popup?.Child as Control)?.GetSelfAndVisualDescendants().OfType<Border>().FirstOrDefault(b => b.Name == "PopupBorder");
            var containers = Enumerable.Range(0, count).Select(combo.ContainerFromIndex).OfType<Control>().ToList();
            var visible = containers.Count(Shown);
            Check(combo.IsDropDownOpen && popup?.IsOpen == true, $"{name} drop-down '{label}' opens");
            var presenter = (popup?.Child as Control)?.GetSelfAndVisualDescendants().OfType<Avalonia.Controls.Presenters.ItemsPresenter>().FirstOrDefault();
            var panel = presenter?.Panel;
            var scroller = (popup?.Child as Control)?.GetSelfAndVisualDescendants().OfType<ScrollViewer>().FirstOrDefault();
            var diagnostics = $"items={count} visibleContainers={visible} host={popup?.Host?.GetType().Name} surface={surface?.Bounds} presenter={presenter?.Bounds} " +
                              $"panel={panel?.GetType().Name}:{panel?.Bounds} viewport={scroller?.Viewport} extent={scroller?.Extent} popupTransform={popup?.InheritsTransform}";
            Check(count == 0 || visible >= Math.Min(count, 3), $"{name} drop-down '{label}' shows items", diagnostics);
            var opacity = surface == null ? 0 : surface.GetSelfAndVisualAncestors().OfType<Visual>().Aggregate(1.0, (o, v) => o * v.Opacity);
            Check(surface != null && opacity > 0.99, $"{name} drop-down '{label}' surface is opaque after opening", $"effective opacity={opacity:0.###}");
            var comboTop = combo.PointToScreen(new Point(0, combo.Bounds.Height));
            var surfaceTop = surface?.PointToScreen(new Point(0, 0));
            var nearCombo = surfaceTop is { } st && Math.Abs(st.Y - comboTop.Y) < 80 * window.RenderScaling && Math.Abs(st.X - comboTop.X) < 80 * window.RenderScaling;
            Check(nearCombo, $"{name} drop-down '{label}' is placed at its combo box", $"combo bottom-left={comboTop} surface={surfaceTop}");
            if (index == 0)
                shots.Add(OperatingSystem.IsWindows() ? CaptureScreenOfWindow(window, Path.Combine(dir, $"{name}-dropdown.png")) : Capture(window, Path.Combine(dir, $"{name}-dropdown.png")));
            combo.IsDropDownOpen = false;
            await Delay(250);
            index++;
        }
    }

    [DllImport("gdi32.dll")] private static extern bool BitBlt(IntPtr hdcDest, int x, int y, int w, int h, IntPtr hdcSrc, int sx, int sy, uint rop);

    /// <summary>Copies the screen area of the window (includes popup windows above it).</summary>
    private static string CaptureScreenOfWindow(Window window, string path)
    {
        try
        {
            if (window.TryGetPlatformHandle()?.Handle is not { } hwnd || hwnd == IntPtr.Zero) throw new InvalidOperationException("no window handle");
            GetWindowRect(hwnd, out var r);
            int w = r.Right - r.Left, h = r.Bottom - r.Top;
            var screen = GetDC(IntPtr.Zero);
            var mem = CreateCompatibleDC(screen);
            var bmi = new BITMAPINFOHEADER { biSize = (uint)Marshal.SizeOf<BITMAPINFOHEADER>(), biWidth = w, biHeight = -h, biPlanes = 1, biBitCount = 32 };
            var dib = CreateDIBSection(mem, ref bmi, 0, out var bits, IntPtr.Zero, 0);
            var old = SelectObject(mem, dib);
            try
            {
                if (!BitBlt(mem, 0, 0, w, h, screen, r.Left, r.Top, 0x00CC0020 /* SRCCOPY */)) throw new InvalidOperationException("BitBlt failed");
                SaveBits(bits, w, h, path);
            }
            finally
            {
                SelectObject(mem, old); DeleteObject(dib); DeleteDC(mem); ReleaseDC(IntPtr.Zero, screen);
            }
            return Path.GetFileName(path) + " (screen)";
        }
        catch (Exception ex)
        {
            Check(false, "screen capture " + Path.GetFileName(path), ex.Message);
            return Path.GetFileName(path) + " (failed)";
        }
    }

    private static void SaveBits(IntPtr bits, int w, int h, string path)
    {
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

    /// <summary>
    /// Real operating-system input (Win32 SendInput): the events travel through the Windows message queue into
    /// Avalonia like a physical mouse and keyboard, so Button pointer capture, drag thresholds, popups and focus
    /// behave as for a user. Each gesture is followed by checks on the real view models.
    /// </summary>
    private static class RealInput
    {
        [StructLayout(LayoutKind.Sequential)] private struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr dwExtraInfo; }
        [StructLayout(LayoutKind.Sequential)] private struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr dwExtraInfo; }
        [StructLayout(LayoutKind.Explicit)] private struct InputUnion { [FieldOffset(0)] public MOUSEINPUT mi; [FieldOffset(0)] public KEYBDINPUT ki; }
        [StructLayout(LayoutKind.Sequential)] private struct INPUT { public uint type; public InputUnion u; }
        [DllImport("user32.dll", SetLastError = true)] private static extern uint SendInput(uint n, INPUT[] inputs, int size);
        [DllImport("user32.dll")] private static extern int GetSystemMetrics(int index);
        [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr hwnd);
        private const uint MOVE = 0x0001, LEFTDOWN = 0x0002, LEFTUP = 0x0004, ABSOLUTE = 0x8000, VIRTUALDESK = 0x4000, KEYUP = 0x0002, UNICODE = 0x0004;

        private static void Send(params INPUT[] inputs) => SendInput((uint)inputs.Length, inputs, Marshal.SizeOf<INPUT>());

        private static INPUT Mouse(uint flags, PixelPoint p)
        {
            int left = GetSystemMetrics(76), top = GetSystemMetrics(77), width = GetSystemMetrics(78), height = GetSystemMetrics(79);
            return new INPUT { type = 0, u = new InputUnion { mi = new MOUSEINPUT {
                dx = (int)Math.Round((p.X - left) * 65535.0 / Math.Max(1, width - 1)),
                dy = (int)Math.Round((p.Y - top) * 65535.0 / Math.Max(1, height - 1)),
                dwFlags = flags | MOVE | ABSOLUTE | VIRTUALDESK } } };
        }

        private static INPUT Key(ushort vk, bool up) => new() { type = 1, u = new InputUnion { ki = new KEYBDINPUT { wVk = vk, dwFlags = up ? KEYUP : 0 } } };
        private static INPUT Char(char c, bool up) => new() { type = 1, u = new InputUnion { ki = new KEYBDINPUT { wScan = c, dwFlags = UNICODE | (up ? KEYUP : 0) } } };

        private static PixelPoint Center(Visual v) => v.PointToScreen(new Point(v.Bounds.Width / 2, v.Bounds.Height / 2));

        private static async Task MoveTo(PixelPoint to, int steps = 12)
        {
            for (var i = 1; i <= steps; i++) { Send(Mouse(0, to)); await Task.Delay(12); }
        }

        private static async Task Glide(PixelPoint from, PixelPoint to, int steps = 24)
        {
            for (var i = 1; i <= steps; i++)
            {
                Send(Mouse(0, new PixelPoint(from.X + (to.X - from.X) * i / steps, from.Y + (to.Y - from.Y) * i / steps)));
                await Task.Delay(16);
            }
        }

        private static async Task Click(Visual v) { var c = Center(v); await MoveTo(c); Send(Mouse(LEFTDOWN, c)); await Task.Delay(60); Send(Mouse(LEFTUP, c)); await Delay(250); }

        private static async Task Drag(PixelPoint from, PixelPoint to, Func<Task>? beforeRelease = null)
        {
            await MoveTo(from); Send(Mouse(LEFTDOWN, from)); await Task.Delay(80);
            await Glide(from, to);
            await Task.Delay(120);
            if (beforeRelease != null) await beforeRelease();
            Send(Mouse(LEFTUP, to)); await Delay(400);
        }

        private static async Task Press(ushort vk) { Send(Key(vk, false)); await Task.Delay(30); Send(Key(vk, true)); await Delay(200); }
        private static async Task Type(string text) { foreach (var c in text) { Send(Char(c, false), Char(c, true)); await Task.Delay(30); } await Delay(300); }

        public static async Task Run(Window window, string dir, List<string> shots)
        {
            if (window.TryGetPlatformHandle()?.Handle is { } hwnd) SetForegroundWindow(hwnd);
            await Delay(500);

            // 1. Card library: real drag from the catalog to the blacklist, plain click, drop-down, keyboard search.
            LiquidNavigation.Navigate(LiquidPages.CardList);
            await Delay(1200);
            var list = VisiblePage(window, typeof(CardListSettingsView)) as CardListSettingsView;
            var vm = list?.DataContext as ViewModels.Pages.CardListSettingsViewModel;
            var firstTile = list?.GetVisualDescendants().OfType<Button>().FirstOrDefault(b => b.Classes.Contains("lg-catalog-card") && Shown(b));
            var black = list?.FindControl<GlassPanel>("BlacklistBucket");
            // Catalog rows are virtualised and rebuilt when the lists change: look the tile up again before each gesture.
            Button? TileFor(ViewModels.Pages.CardListCard c) => list?.GetVisualDescendants().OfType<Button>()
                .FirstOrDefault(b => b.Classes.Contains("lg-catalog-card") && Shown(b) && ReferenceEquals(b.DataContext, c));
            if (vm != null && firstTile?.DataContext is ViewModels.Pages.CardListCard card && black != null)
            {
                vm.RemoveFromBlacklist(card); vm.RemoveFromWhitelist(card); await Delay(500);
                var tile = TileFor(card);
                Check(tile != null, "real mouse: catalog tile found after list changes");
                if (tile == null) return;
                await Drag(Center(tile), Center(black));
                Check(vm.BlacklistCards.Contains(card), "real mouse: dragging a library card onto the blacklist adds it");
                Check(!vm.WhitelistCards.Contains(card), "real mouse: the drag does not also click-add the card to the active list");
                vm.RemoveFromBlacklist(card); await Delay(500);
                tile = TileFor(card);
                if (tile != null) await Click(tile);
                Check(vm.WhitelistCards.Contains(card), "real mouse: clicking a library card adds it to the active list");
                vm.RemoveFromWhitelist(card); await Delay(200);
            }
            else Check(false, "real mouse: card library not available");

            var rarity = list?.GetVisualDescendants().OfType<ComboBox>().FirstOrDefault(c => Shown(c));
            if (rarity != null)
            {
                await Click(rarity);
                await Delay(400);
                var item = rarity.ContainerFromIndex(2);
                Check(rarity.IsDropDownOpen && item != null && Shown(item), "real mouse: clicking a drop-down opens its list");
                shots.Add(CaptureScreenOfWindow(window, Path.Combine(dir, "real-input-dropdown.png")));
                if (item != null) await Click(item);
                Check(rarity.SelectedIndex == 2 && !rarity.IsDropDownOpen, "real mouse: clicking an item selects it and closes the list");
                rarity.SelectedIndex = 0; await Delay(300);
            }
            var search = list?.GetVisualDescendants().OfType<TextBox>().FirstOrDefault(t => Shown(t));
            if (search != null && vm != null)
            {
                var all = vm.VisibleCards.Count;
                await Click(search);
                await Type("ex");
                Check(search.Text == "ex" && vm.VisibleCards.Count < all, "real keyboard: typing in the library search filters the cards");
                Send(Key(0x11, false), Key(0x41, false), Key(0x41, true), Key(0x11, true)); await Delay(100);
                await Press(0x08);
                Check(string.IsNullOrEmpty(search.Text) && vm.VisibleCards.Count == all, "real keyboard: select all + backspace clears the search");
            }

            // 2. Window close button: hover turns it red, leaving restores it (no click).
            var close = window.GetVisualDescendants().OfType<Button>().FirstOrDefault(b => b.Name == "CloseButton");
            if (close != null)
            {
                await MoveTo(Center(close));
                await Delay(500);
                var red = close.Background is Avalonia.Media.ISolidColorBrush b && b.Color.R > 200 && b.Color.G < 120;
                Check(close.IsPointerOver && red, "real mouse: hovering the close button turns it red");
                shots.Add(CaptureScreenOfWindow(window, Path.Combine(dir, "real-input-close-hover.png")));
                await MoveTo(Center(window.GetVisualDescendants().OfType<Control>().First(c => c.Name == "TitleDragArea")));
                await Delay(500);
                Check(!close.IsPointerOver, "real mouse: leaving the close button restores it");
            }

            // 3. Workbench: drag the task list panel to the right of the log; Escape cancels another drag.
            LiquidNavigation.Navigate(LiquidPages.Tasks);
            await Delay(1200);
            var board = window.GetVisualDescendants().OfType<LiquidBoard>().FirstOrDefault(b => b.IsEffectivelyVisible);
            var panels = board?.GetVisualDescendants().OfType<BoardPanel>().ToList() ?? [];
            var listPanel = panels.FirstOrDefault(p => p.PanelId == "task_list");
            var logPanel = panels.FirstOrDefault(p => p.PanelId == "log");
            var handle = listPanel?.GetVisualDescendants().OfType<Button>().FirstOrDefault(b => b.Name == "PART_DragHandle");
            if (board != null && listPanel != null && logPanel != null && handle != null)
            {
                var before = board.Layout.ColumnOf("task_list");
                var target = logPanel.PointToScreen(new Point(logPanel.Bounds.Width - 12, logPanel.Bounds.Height / 2));
                await Drag(Center(handle), target);
                var after = board.Layout.ColumnOf("task_list");
                Check(after != before && board.Layout.Columns.Last().Items.Any(i => i.Id == "task_list"), "real mouse: dragging the task list to the log's right edge moves it into a new right column");
                shots.Add(CaptureScreenOfWindow(window, Path.Combine(dir, "real-input-board-moved.png")));
                var handle2 = listPanel.GetVisualDescendants().OfType<Button>().First(b => b.Name == "PART_DragHandle");
                string Arrangement() => string.Join(" | ", board.Layout.Columns.Select(c => string.Join(",", c.Items.Select(i => i.Id))));
                var gestureField = typeof(LiquidBoard).GetField("_gesture", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance)!;
                var arrangementBefore = Arrangement();
                bool activeBeforeEsc = false, activeAfterEsc = true;
                await Drag(Center(handle2), Center(panels.First(p => p.PanelId == "task_settings")), async () =>
                {
                    activeBeforeEsc = gestureField.GetValue(board) != null;
                    await Press(0x1B);
                    activeAfterEsc = gestureField.GetValue(board) != null;
                });
                Check(activeBeforeEsc && !activeAfterEsc && Arrangement() == arrangementBefore,
                    "real mouse/keyboard: Escape during a panel drag cancels it",
                    $"gesture before Esc={activeBeforeEsc} after Esc={activeAfterEsc} arrangement {arrangementBefore} -> {Arrangement()} focus={TopLevel.GetTopLevel(board)?.FocusManager?.GetFocusedElement()?.GetType().Name}");
                var reset = window.GetVisualDescendants().OfType<Button>().FirstOrDefault(b => b.Name == "ResetLayoutButton" && Shown(b));
                if (reset != null) await Click(reset); else BoardLayoutStore.Save(BoardLayout.Default());
                await Delay(700);
                Check(board.Layout.ColumnOf("task_list") == before, "real mouse: 恢复布局 restores the default layout");
            }
            else Check(false, "real mouse: workbench panels not available");

            // 4. Rules: drag a curve node; open the new-preset dialog, close with Escape and reopen at once.
            LiquidNavigation.Navigate(LiquidPages.CardRules);
            await Delay(1200);
            var curve = window.GetVisualDescendants().OfType<CardRuleCurve>().FirstOrDefault(c => !c.Mini && Shown(c));
            if (curve?.Rule is { Points.Count: > 0 } rule)
            {
                var node = rule.Points[0];
                var turn = node[0]; var weight = node[1];
                var from = curve.PointToScreen(curve.PointFor(turn, weight));
                var to = curve.PointToScreen(curve.PointFor(turn, Math.Min(100, weight + 20)));
                await Drag(from, to);
                Check(rule.Points.Any(p => p[0] == turn && p[1] > weight), $"real mouse: dragging the curve node at turn {turn} raises its weight");
            }
            else Check(false, "real mouse: rule curve not available");

            var newPreset = window.GetVisualDescendants().OfType<Button>().FirstOrDefault(b => Shown(b) && Avalonia.Automation.AutomationProperties.GetName(b) == ExText.T("新建预设"));
            if (newPreset != null)
            {
                await Click(newPreset);
                await Delay(150);
                for (var f = 0; f < 6; f++) { shots.Add(CaptureScreenOfWindow(window, Path.Combine(dir, $"real-input-modal-open-{f}.png"))); await Task.Delay(40); }
                await Press(0x1B);
                await Click(newPreset); // reopen while the previous dialog is still leaving
                for (var f = 0; f < 6; f++) { shots.Add(CaptureScreenOfWindow(window, Path.Combine(dir, $"real-input-modal-reopen-{f}.png"))); await Task.Delay(40); }
                await Delay(400);
                var open = window.GetVisualDescendants().OfType<GlassPanel>().Count(g => g.Classes.Contains("dialog") && Shown(g));
                Check(open == 1, $"real mouse/keyboard: quick close and reopen leaves exactly one open dialog (found {open})");
                await Press(0x1B);
                await Delay(500);
                Check(!window.GetVisualDescendants().OfType<GlassPanel>().Any(g => g.Classes.Contains("dialog") && Shown(g)), "real keyboard: Escape closes the dialog");
            }
            else Check(false, "real mouse: 新建预设 button not found");
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
            SaveBits(bits, w, h, path);
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
