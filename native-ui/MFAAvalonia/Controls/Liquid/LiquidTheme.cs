using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using Avalonia.Styling;
using SukiUI;
using SukiUI.Models;
using System;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// Keeps the liquid accent resources in sync with the saved colour theme and
/// exposes the motion preferences shared by all animated controls.
/// The default "Blue" colour theme maps to the prototype accent (#007AFF / #0A84FF);
/// other saved colour themes keep driving the accent so existing settings stay valid.
/// </summary>
public static class LiquidTheme
{
    private static readonly Color LightAccent = Color.Parse("#007AFF");
    private static readonly Color DarkAccent = Color.Parse("#0A84FF");
    private static bool _initialized;
    private static bool _reduceMotion;

    /// <summary>Raised when the reduce motion preference changes.</summary>
    public static event Action<bool>? ReduceMotionChanged;

    public static bool ReduceMotion
    {
        get => _reduceMotion;
        set
        {
            if (_reduceMotion == value) return;
            _reduceMotion = value;
            ApplyMotionClass();
            ReduceMotionChanged?.Invoke(value);
        }
    }

    public static void Initialize(Application app)
    {
        if (_initialized) return;
        _initialized = true;
        var theme = SukiTheme.GetInstance(app);
        theme.OnColorThemeChanged += _ => Apply(app);
        theme.OnBaseThemeChanged += _ => Apply(app);
        app.ActualThemeVariantChanged += (_, _) => Apply(app);
        // SukiDialogHost keeps IsDialogOpen internal; mirror it into a class for the liquid template.
        SukiUI.Controls.SukiDialogHost.IsDialogOpenProperty.Changed.AddClassHandler<SukiUI.Controls.SukiDialogHost>(
            (host, e) => host.Classes.Set("lg-open", e.NewValue is true));
        Apply(app);
        _reduceMotion = MFAAvalonia.Configuration.ConfigurationManager.Current.GetValue(MFAAvalonia.Configuration.ConfigurationKeys.LiquidReduceMotion, false);
    }

    public static bool IsDark(Application? app = null)
        => (app ?? Application.Current)?.ActualThemeVariant == ThemeVariant.Dark;

    public static void Apply(Application app)
    {
        var dark = app.ActualThemeVariant == ThemeVariant.Dark;
        var active = SukiTheme.GetInstance(app).ActiveColorTheme;
        var useDefault = active == null || string.Equals(active.DisplayName, "Blue", StringComparison.OrdinalIgnoreCase);
        var accent = useDefault ? (dark ? DarkAccent : LightAccent) : active!.Primary;
        app.Resources["LgAccentColor"] = accent;
        app.Resources["LgAccentSoftColor"] = WithAlpha(accent, dark ? 0.18 : 0.11);
        app.Resources["LgAccentRingColor"] = WithAlpha(accent, 0.14);
        app.Resources["LgAccentBorderColor"] = WithAlpha(accent, 0.34);
        app.Resources["LgAccentGlowColor"] = WithAlpha(accent, 0.45);
        app.Resources["LgPrimaryShadow"] = BoxShadows.Parse($"0 8 22 -4 {Hex(WithAlpha(accent, 0.45))}, inset 0 1.5 1 0 #9EFFFFFF, inset 0 -1 1 0 #1F000000");
        app.Resources["LgPrimaryHoverShadow"] = BoxShadows.Parse($"0 12 28 -4 {Hex(WithAlpha(accent, 0.62))}, 0 0 0 3 {Hex(WithAlpha(accent, 0.14))}, inset 0 1.5 1.5 0 #D9FFFFFF");
        app.Resources["LgButtonHoverShadow"] = BoxShadows.Parse($"0 8 20 -5 {Hex(WithAlpha(accent, 0.16))}, 0 2 6 0 #0F0F172A, inset 0 1.5 1 0 {(dark ? "#47FFFFFF" : "#F2FFFFFF")}");
        app.Resources["LgFocusShadow"] = BoxShadows.Parse($"0 0 0 3 {Hex(WithAlpha(accent, dark ? 0.18 : 0.11))}, inset 0 1 1 0 {(dark ? "#14FFFFFF" : "#99FFFFFF")}");
        app.Resources["LgSelectedTileShadow"] = BoxShadows.Parse($"0 8 20 -4 {Hex(WithAlpha(accent, 0.22))}, inset 0 1 0.5 0 {(dark ? "#47FFFFFF" : "#E0FFFFFF")}");
        app.Resources["LgSplitterGlow"] = BoxShadows.Parse($"0 0 10 0 {Hex(WithAlpha(accent, 0.45))}");
        if (useDefault)
        {
            // Legacy Suki styled surfaces share the same blue so nothing looks stitched together.
            foreach (var (suffix, alpha) in new (string, double)[] { ("", 1), ("75", .75), ("50", .5), ("25", .25), ("20", .2), ("15", .15), ("10", .1), ("7", .07), ("5", .05), ("3", .03) })
                app.Resources["SukiPrimaryColor" + suffix] = WithAlpha(accent, alpha);
        }
    }

    private static Color WithAlpha(Color c, double alpha) => Color.FromArgb((byte)Math.Round(255 * alpha), c.R, c.G, c.B);
    private static string Hex(Color c) => $"#{c.A:X2}{c.R:X2}{c.G:X2}{c.B:X2}";

    private static void ApplyMotionClass()
    {
        if (Application.Current?.ApplicationLifetime is Avalonia.Controls.ApplicationLifetimes.IClassicDesktopStyleApplicationLifetime desktop)
        {
            foreach (var window in desktop.Windows)
                window.Classes.Set("reduced-motion", _reduceMotion);
        }
    }

    /// <summary>Applies the current motion preference to a newly created window.</summary>
    public static void Attach(Window window) => window.Classes.Set("reduced-motion", _reduceMotion);
}
