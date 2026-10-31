using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using System;
using System.Collections.Generic;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// Stroke icons from the modern prototype (24×24 view box, round caps). Rendered as
/// native geometry so they follow the foreground colour in both themes and stay crisp at any DPI.
/// </summary>
public class LiquidIcon : Control
{
    public static readonly StyledProperty<string?> KindProperty = AvaloniaProperty.Register<LiquidIcon, string?>(nameof(Kind));
    public static readonly StyledProperty<IBrush?> ForegroundProperty = Avalonia.Controls.Documents.TextElement.ForegroundProperty.AddOwner<LiquidIcon>();
    public static readonly StyledProperty<double> SizeProperty = AvaloniaProperty.Register<LiquidIcon, double>(nameof(Size), 16);

    public string? Kind { get => GetValue(KindProperty); set => SetValue(KindProperty, value); }
    public IBrush? Foreground { get => GetValue(ForegroundProperty); set => SetValue(ForegroundProperty, value); }
    public double Size { get => GetValue(SizeProperty); set => SetValue(SizeProperty, value); }

    private static readonly Dictionary<string, (Geometry Geometry, bool Fill, double Stroke)> Cache = new();

    static LiquidIcon()
    {
        AffectsRender<LiquidIcon>(KindProperty, ForegroundProperty, SizeProperty);
        AffectsMeasure<LiquidIcon>(SizeProperty);
    }

    public LiquidIcon() { IsHitTestVisible = false; }

    protected override Size MeasureOverride(Size availableSize) => new(Size, Size);

    public static bool Exists(string? kind) => kind != null && Paths.ContainsKey(kind);

    private static (Geometry Geometry, bool Fill, double Stroke)? Resolve(string? kind)
    {
        if (kind == null || !Paths.TryGetValue(kind, out var def)) return null;
        if (!Cache.TryGetValue(kind, out var cached))
        {
            cached = (Geometry.Parse(def.Data), def.Fill, def.Stroke);
            Cache[kind] = cached;
        }
        return cached;
    }

    public override void Render(DrawingContext context)
    {
        if (Resolve(Kind) is not { } icon || Foreground is not { } brush) return;
        var scale = Math.Min(Bounds.Width, Bounds.Height) / 24.0;
        if (scale <= 0) return;
        var offsetX = (Bounds.Width - 24 * scale) / 2;
        var offsetY = (Bounds.Height - 24 * scale) / 2;
        using (context.PushTransform(Matrix.CreateScale(scale, scale) * Matrix.CreateTranslation(offsetX, offsetY)))
        {
            var pen = icon.Stroke > 0 ? new Pen(brush, icon.Stroke, lineCap: PenLineCap.Round, lineJoin: PenLineJoin.Round) : null;
            context.DrawGeometry(icon.Fill ? brush : null, pen, icon.Geometry);
        }
    }

    private static readonly Dictionary<string, (string Data, bool Fill, double Stroke)> Paths = new()
    {
        ["tasks"] = ("M7 4H17A4 4 0 0 1 21 8V16A4 4 0 0 1 17 20H7A4 4 0 0 1 3 16V8A4 4 0 0 1 7 4Z M8 9h8M8 13h6M8 17h4", false, 2.0),
        ["monitor"] = ("M5 3H8A2 2 0 0 1 10 5V8A2 2 0 0 1 8 10H5A2 2 0 0 1 3 8V5A2 2 0 0 1 5 3Z M16 3H19A2 2 0 0 1 21 5V8A2 2 0 0 1 19 10H16A2 2 0 0 1 14 8V5A2 2 0 0 1 16 3Z M5 14H8A2 2 0 0 1 10 16V19A2 2 0 0 1 8 21H5A2 2 0 0 1 3 19V16A2 2 0 0 1 5 14Z M16 14H19A2 2 0 0 1 21 16V19A2 2 0 0 1 19 21H16A2 2 0 0 1 14 19V16A2 2 0 0 1 16 14Z", false, 2.0),
        ["camera"] = ("M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z M8 13A4 4 0 1 1 16 13A4 4 0 1 1 8 13Z", false, 2.0),
        ["cardList"] = ("M5.5 4H13.5A2.5 2.5 0 0 1 16 6.5V18.5A2.5 2.5 0 0 1 13.5 21H5.5A2.5 2.5 0 0 1 3 18.5V6.5A2.5 2.5 0 0 1 5.5 4Z M8 2h11a2 2 0 0 1 2 2v14 M7 9h5M7 13h5", false, 2.0),
        ["drink"] = ("M17 8h1a4 4 0 1 1 0 8h-1 M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4Z", false, 2.0),
        ["rules"] = ("M22 12 L18 12 L15 21 L9 3 L6 12 L2 12", false, 2.0),
        ["settings"] = ("M9 12A3 3 0 1 1 15 12A3 3 0 1 1 9 12Z M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z", false, 2.0),
        ["play"] = ("M6 4 L20 12 L6 20 L6 4Z", true, 0),
        ["stop"] = ("M7.5 5H16.5A2.5 2.5 0 0 1 19 7.5V16.5A2.5 2.5 0 0 1 16.5 19H7.5A2.5 2.5 0 0 1 5 16.5V7.5A2.5 2.5 0 0 1 7.5 5Z", true, 0),
        ["refresh"] = ("M21 12a9 9 0 1 1-2.64-6.36 M21 3 L21 9 L15 9", false, 2.0),
        ["plus"] = ("M12 5L12 19 M5 12L19 12", false, 2.2),
        ["sidebarLeft"] = ("M6 3H18A3 3 0 0 1 21 6V18A3 3 0 0 1 18 21H6A3 3 0 0 1 3 18V6A3 3 0 0 1 6 3Z M9 3L9 21", false, 2.0),
        ["eye"] = ("M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z M9 12A3 3 0 1 1 15 12A3 3 0 1 1 9 12Z", false, 2.0),
        ["eyeOff"] = ("M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24 M1 1L23 23", false, 2.0),
        ["expand"] = ("M15 3 L21 3 L21 9 M9 21 L3 21 L3 15 M21 3L14 10 M3 21L10 14", false, 2.0),
        ["folder"] = ("M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z", false, 2.0),
        ["trash"] = ("M3 6 L5 6 L21 6 M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2", false, 2.0),
        ["chevronDown"] = ("M6 9 L12 15 L18 9", false, 2.2),
        ["check"] = ("M20 6 L9 17 L4 12", false, 2.5),
        ["themeAdaptive"] = ("M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z", true, 1.6),
        ["sunAdaptive"] = ("M7.5 12A4.5 4.5 0 1 1 16.5 12A4.5 4.5 0 1 1 7.5 12Z M12 2L12 4.5 M12 19.5L12 22 M4.93 4.93L6.7 6.7 M17.3 17.3L19.07 19.07 M2 12L4.5 12 M19.5 12L22 12 M4.93 19.07L6.7 17.3 M17.3 6.7L19.07 4.93", true, 1.8),
        ["winMin"] = ("M5 12L19 12", false, 2.0),
        ["winMax"] = ("M7 5H17A2 2 0 0 1 19 7V17A2 2 0 0 1 17 19H7A2 2 0 0 1 5 17V7A2 2 0 0 1 7 5Z", false, 2.0),
        ["winClose"] = ("M6 6L18 18 M6 18L18 6", false, 2.0),
        ["playSmall"] = ("M8 5 L19 12 L8 19 L8 5Z", true, 0),
        ["playSequence"] = ("M13 17 L18 12 L13 7 M6 17 L11 12 L6 7", false, 2.0),
        ["copy"] = ("M11 9H20A2 2 0 0 1 22 11V20A2 2 0 0 1 20 22H11A2 2 0 0 1 9 20V11A2 2 0 0 1 11 9Z M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1", false, 2.0),
        ["paste"] = ("M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2 M9 2H15A1 1 0 0 1 16 3V5A1 1 0 0 1 15 6H9A1 1 0 0 1 8 5V3A1 1 0 0 1 9 2Z", false, 2.0),
        ["edit"] = ("M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7 M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z", false, 2.0),
        ["search"] = ("M11 4A7 7 0 1 1 11 18A7 7 0 1 1 11 4Z M21 21l-4.35-4.35", false, 2),
        ["chevronRight"] = ("M9 6l6 6-6 6", false, 2),
        ["chevronLeft"] = ("M15 6l-6 6 6 6", false, 2),
        ["chevronUp"] = ("M6 15l6-6 6 6", false, 2.2),
        ["x"] = ("M18 6L6 18M6 6l12 12", false, 2),
        ["link"] = ("M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71 M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71", false, 2),
        ["unlink"] = ("M18.84 12.25l1.72-1.71a5 5 0 0 0-7.07-7.07l-1.72 1.71 M5.17 11.75l-1.71 1.71a5 5 0 0 0 7.07 7.07l1.71-1.71 M8 2v3 M2 8h3 M16 22v-3 M22 16h-3", false, 2),
        ["plug"] = ("M12 22v-5 M9 8V2 M15 8V2 M18 8v5a6 6 0 0 1-12 0V8z", false, 2),
        ["settings2"] = ("M20 7h-9 M14 17H5 M17 14A3 3 0 1 1 17 20A3 3 0 1 1 17 14Z M7 4A3 3 0 1 1 7 10A3 3 0 1 1 7 4Z", false, 2),
        ["layout"] = ("M5 3H19A2 2 0 0 1 21 5V19A2 2 0 0 1 19 21H5A2 2 0 0 1 3 19V5A2 2 0 0 1 5 3Z M3 9h18 M9 21V9", false, 2),
        ["download"] = ("M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4 M7 10l5 5 5-5 M12 15V3", false, 2),
        ["upload"] = ("M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4 M17 8l-5-5-5 5 M12 3v12", false, 2),
        ["pin"] = ("M12 17v5 M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z", false, 2),
        ["info"] = ("M2 12A10 10 0 1 1 22 12A10 10 0 1 1 2 12Z M12 16v-4 M12 8h.01", false, 2),
        ["alert"] = ("M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z M12 9v4 M12 17h.01", false, 2),
        ["checkCircle"] = ("M22 11.08V12a10 10 0 1 1-5.93-9.14 M22 4L12 14.01l-3-3", false, 2),
        ["clock"] = ("M2 12A10 10 0 1 1 22 12A10 10 0 1 1 2 12Z M12 6v6l4 2", false, 2),
        ["bell"] = ("M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9 M13.73 21a2 2 0 0 1-3.46 0", false, 2),
        ["keyboard"] = ("M4 4H20A2 2 0 0 1 22 6V18A2 2 0 0 1 20 20H4A2 2 0 0 1 2 18V6A2 2 0 0 1 4 4Z M6 8h.01 M10 8h.01 M14 8h.01 M18 8h.01 M8 12h.01 M12 12h.01 M16 12h.01 M7 16h10", false, 2),
        ["globe"] = ("M2 12A10 10 0 1 1 22 12A10 10 0 1 1 2 12Z M2 12h20 M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z", false, 2),
        ["cpu"] = ("M6 4H18A2 2 0 0 1 20 6V18A2 2 0 0 1 18 20H6A2 2 0 0 1 4 18V6A2 2 0 0 1 6 4Z M9 9h6v6H9z M9 1v3 M15 1v3 M9 20v3 M15 20v3 M20 9h3 M20 14h3 M1 9h3 M1 14h3", false, 2),
        ["palette"] = ("M12 2a10 10 0 0 0 0 20c1.1 0 2-.9 2-2 0-.5-.2-1-.5-1.3-.3-.4-.5-.8-.5-1.3 0-1.1.9-2 2-2h2.4c3 0 5.6-2.5 5.6-5.6C23 5.4 18 2 12 2z M7.5 10.5h.01 M10.5 6.5h.01 M15.5 6.5h.01", false, 2),
        ["layers"] = ("M12 2L2 7l10 5 10-5-10-5z M2 17l10 5 10-5 M2 12l10 5 10-5", false, 2),
        ["power"] = ("M18.36 6.64a9 9 0 1 1-12.73 0 M12 2v10", false, 2),
        ["sparkles"] = ("M12 3l1.9 5.8L20 10.7l-6.1 1.9L12 18.5l-1.9-5.9L4 10.7l6.1-1.9z M5 3v4 M3 5h4 M19 17v4 M17 19h4", false, 2),
        ["image"] = ("M5 3H19A2 2 0 0 1 21 5V19A2 2 0 0 1 19 21H5A2 2 0 0 1 3 19V5A2 2 0 0 1 5 3Z M8.5 7A1.5 1.5 0 1 1 8.5 10A1.5 1.5 0 1 1 8.5 7Z M21 15l-5-5L5 21", false, 2),
        ["save"] = ("M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z M17 21v-8H7v8 M7 3v5h8", false, 2),
        ["moreH"] = ("M11 12A1 1 0 1 1 13 12A1 1 0 1 1 11 12Z M18 12A1 1 0 1 1 20 12A1 1 0 1 1 18 12Z M4 12A1 1 0 1 1 6 12A1 1 0 1 1 4 12Z", false, 2),
        ["user"] = ("M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2 M8 7A4 4 0 1 1 16 7A4 4 0 1 1 8 7Z", false, 2),
        ["grip"] = ("M9 5A1 1 0 1 1 9 7A1 1 0 1 1 9 5Z M15 5A1 1 0 1 1 15 7A1 1 0 1 1 15 5Z M9 11A1 1 0 1 1 9 13A1 1 0 1 1 9 11Z M15 11A1 1 0 1 1 15 13A1 1 0 1 1 15 11Z M9 17A1 1 0 1 1 9 19A1 1 0 1 1 9 17Z M15 17A1 1 0 1 1 15 19A1 1 0 1 1 15 17Z", false, 2.4),
        ["collapse"] = ("M4 14h6v6 M20 10h-6V4 M14 10l7-7 M3 21l7-7", false, 2),
        ["fileText"] = ("M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z M14 2v6h6 M16 13H8 M16 17H8 M10 9H8", false, 2),
        ["minus"] = ("M5 12h14", false, 2.2),
        ["arrowUp"] = ("M12 19V5 M5 12l7-7 7 7", false, 2),
        ["arrowDown"] = ("M12 5v14 M19 12l-7 7-7-7", false, 2),
        ["externalLink"] = ("M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6 M15 3h6v6 M10 14L21 3", false, 2),
        ["github"] = ("M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22", false, 2),
    };
}
