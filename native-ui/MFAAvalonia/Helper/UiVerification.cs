using Avalonia;
using Avalonia.Controls;
using Avalonia.Media.Imaging;
using Avalonia.Platform;
using Avalonia.Styling;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Controls.Liquid;
using Newtonsoft.Json;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading.Tasks;

namespace MFAAvalonia.Helper;

/// <summary>
/// Opt-in self check for release verification: <c>--ui-verify &lt;dir&gt;</c> walks the
/// themes, window sizes and pages of the real window and saves captures of the native
/// window (Win32 PrintWindow, i.e. what the compositor shows), plus a JSON report with
/// render scaling, frame timings and any binding/runtime errors. It never touches user
/// configuration beyond the theme it restores at the end, and is inert without the argument.
/// </summary>
public static class UiVerification
{
    public static string? OutputDirectory
    {
        get
        {
            var args = AppRuntime.Args.ToList();
            var i = args.IndexOf("--ui-verify");
            return i >= 0 && i + 1 < args.Count ? args[i + 1] : null;
        }
    }

    public static void StartIfRequested(Window window)
    {
        var dir = OutputDirectory;
        if (dir == null) return;
        Directory.CreateDirectory(dir);
        Dispatcher.UIThread.Post(async () =>
        {
            var report = new Dictionary<string, object?>();
            try { await Run(window, dir, report); }
            catch (Exception ex) { report["fatal"] = ex.ToString(); }
            File.WriteAllText(Path.Combine(dir, "report.json"), JsonConvert.SerializeObject(report, Formatting.Indented));
            if (AppRuntime.Args.Contains("--ui-verify-exit"))
                (Application.Current?.ApplicationLifetime as Avalonia.Controls.ApplicationLifetimes.IClassicDesktopStyleApplicationLifetime)?.Shutdown();
        }, DispatcherPriority.Background);
    }

    private static async Task Delay(int ms)
    {
        var end = DateTime.UtcNow.AddMilliseconds(ms);
        while (DateTime.UtcNow < end) await Task.Delay(30);
    }

    private static async Task Run(Window window, string dir, Dictionary<string, object?> report)
    {
        await Delay(4000);
        report["renderScaling"] = window.RenderScaling;
        report["os"] = RuntimeInformation.OSDescription;
        report["version"] = typeof(UiVerification).Assembly.GetName().Version?.ToString();
        report["backdropBlurSupported"] = BackdropBlur.LastFrameSupported;
        var shots = new List<string>();
        var timings = new Dictionary<string, double>();
        var original = Instances.GuiSettingsUserControlModel.BaseTheme;
        var sizes = new[] { (1280, 800), (1600, 1000) };
        string[] pages = [LiquidPages.Tasks, LiquidPages.CardRules, LiquidPages.Drinks, LiquidPages.CardList, LiquidPages.Monitor, LiquidPages.Screenshot, LiquidPages.Settings];
        foreach (var (w, h) in sizes)
        {
            window.WindowState = WindowState.Normal;
            window.Width = w; window.Height = h;
            await Delay(800);
            foreach (var theme in new[] { ThemeVariant.Light, ThemeVariant.Dark })
            {
                Instances.GuiSettingsUserControlModel.BaseTheme = theme;
                await Delay(900);
                foreach (var page in pages)
                {
                    var sw = Stopwatch.StartNew();
                    LiquidNavigation.Navigate(page);
                    await Delay(1200);
                    timings[$"{w}x{h}-{theme}-{page}"] = sw.Elapsed.TotalMilliseconds;
                    shots.Add(Capture(window, Path.Combine(dir, $"{w}x{h}-{theme}-{page}.png")));
                }
                LiquidNavigation.Navigate(LiquidPages.Tasks);
                await Delay(600);
            }
        }
        // Maximized state and collapsed sidebar at the larger size.
        window.WindowState = WindowState.Maximized;
        await Delay(1200);
        shots.Add(Capture(window, Path.Combine(dir, "maximized-tasks.png")));
        window.WindowState = WindowState.Normal;
        Instances.GuiSettingsUserControlModel.BaseTheme = original;
        await Delay(500);
        report["backdropBlurSupportedAfterTour"] = BackdropBlur.LastFrameSupported;
        report["shots"] = shots;
        report["navigateMs"] = timings;
        report["managedMemoryMB"] = GC.GetTotalMemory(false) / 1048576.0;
        report["workingSetMB"] = Process.GetCurrentProcess().WorkingSet64 / 1048576.0;
    }

    /// <summary>Captures the native window. On Windows uses PrintWindow(PW_RENDERFULLCONTENT).</summary>
    private static string Capture(Window window, string path)
    {
        if (OperatingSystem.IsWindows() && window.TryGetPlatformHandle()?.Handle is { } hwnd && hwnd != IntPtr.Zero)
        {
            try { CaptureWin32(hwnd, path); return Path.GetFileName(path); }
            catch (Exception ex) { LoggerHelper.Warning($"PrintWindow 截图失败，改用渲染截图：{ex.Message}"); }
        }
        var scale = window.RenderScaling;
        using var bitmap = new RenderTargetBitmap(new PixelSize((int)(window.Bounds.Width * scale), (int)(window.Bounds.Height * scale)), new Vector(96 * scale, 96 * scale));
        bitmap.Render(window);
        bitmap.Save(path);
        return Path.GetFileName(path) + " (render)";
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
