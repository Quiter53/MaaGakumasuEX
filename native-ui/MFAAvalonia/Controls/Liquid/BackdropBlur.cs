using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using Avalonia.Platform;
using Avalonia.Rendering.SceneGraph;
using Avalonia.Skia;
using SkiaSharp;
using System;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// Native backdrop blur (CSS backdrop-filter equivalent inside the window): snapshots what
/// is already drawn behind this control on the Skia surface, blurs and saturates it, and
/// paints it clipped to the rounded bounds. Place it as the first layer of a glass surface.
/// When the renderer offers no Skia surface (or blur is disabled) nothing is drawn and the
/// glass keeps its translucent fill, so the look degrades rather than breaking.
/// </summary>
public class BackdropBlur : Control
{
    public static readonly StyledProperty<double> RadiusProperty = AvaloniaProperty.Register<BackdropBlur, double>(nameof(Radius), 20);
    public static readonly StyledProperty<double> SaturationProperty = AvaloniaProperty.Register<BackdropBlur, double>(nameof(Saturation), 1.8);
    public static readonly StyledProperty<CornerRadius> CornerRadiusProperty = Border.CornerRadiusProperty.AddOwner<BackdropBlur>();

    public double Radius { get => GetValue(RadiusProperty); set => SetValue(RadiusProperty, value); }
    public double Saturation { get => GetValue(SaturationProperty); set => SetValue(SaturationProperty, value); }
    public CornerRadius CornerRadius { get => GetValue(CornerRadiusProperty); set => SetValue(CornerRadiusProperty, value); }

    /// <summary>Global switch (settings → 外观 → 背景模糊, and reduced effects).</summary>
    public static bool Enabled { get; set; } = true;

    /// <summary>Set once the first frame confirmed a Skia surface was available.</summary>
    public static bool? LastFrameSupported { get; private set; }

    static BackdropBlur()
    {
        AffectsRender<BackdropBlur>(RadiusProperty, SaturationProperty, CornerRadiusProperty);
    }

    public BackdropBlur() { IsHitTestVisible = false; }

    public override void Render(DrawingContext context)
    {
        if (!Enabled || Radius <= 0 || Bounds.Width < 1 || Bounds.Height < 1) return;
        context.Custom(new BlurOperation(new Rect(Bounds.Size), Radius, Saturation, CornerRadius));
    }

    private sealed class BlurOperation(Rect bounds, double radius, double saturation, CornerRadius corners) : ICustomDrawOperation
    {
        public Rect Bounds => bounds;
        public bool HitTest(Point p) => false;
        public bool Equals(ICustomDrawOperation? other) => false;
        public void Dispose() { }

        public void Render(ImmediateDrawingContext context)
        {
            var feature = context.TryGetFeature<ISkiaSharpApiLeaseFeature>();
            if (feature == null) { LastFrameSupported = false; return; }
            using var lease = feature.Lease();
            var canvas = lease.SkCanvas;
            var surface = lease.SkSurface;
            if (surface == null) { LastFrameSupported = false; return; }
            LastFrameSupported = true;

            var matrix = canvas.TotalMatrix;
            var local = new SKRect(0, 0, (float)bounds.Width, (float)bounds.Height);
            var device = matrix.MapRect(local);
            var scale = Math.Max(Math.Abs(matrix.ScaleX), 1e-3f);
            var pad = (int)Math.Ceiling(radius * scale * 2);
            var info = surface.Canvas.DeviceClipBounds;
            var grab = SKRectI.Intersect(SKRectI.Round(SKRect.Inflate(device, pad, pad)), new SKRectI(0, 0, Math.Max(info.Right, 1), Math.Max(info.Bottom, 1)));
            if (grab.Width < 2 || grab.Height < 2) return;
            canvas.Flush();
            using var snapshot = surface.Snapshot(grab);
            if (snapshot == null) return;

            var s = (float)saturation;
            float lr = 0.2126f, lg = 0.7152f, lb = 0.0722f;
            float[] sat =
            [
                lr * (1 - s) + s, lg * (1 - s), lb * (1 - s), 0, 0,
                lr * (1 - s), lg * (1 - s) + s, lb * (1 - s), 0, 0,
                lr * (1 - s), lg * (1 - s), lb * (1 - s) + s, 0, 0,
                0, 0, 0, 1, 0,
            ];
            using var blur = SKImageFilter.CreateBlur((float)(radius * scale / 2), (float)(radius * scale / 2), SKShaderTileMode.Clamp);
            using var color = SKColorFilter.CreateColorMatrix(sat);
            using var paint = new SKPaint { ImageFilter = blur, ColorFilter = color, IsAntialias = true };

            canvas.Save();
            using (var rr = new SKRoundRect(local, (float)corners.TopLeft, (float)corners.TopLeft))
                canvas.ClipRoundRect(rr, antialias: true);
            canvas.ResetMatrix();
            canvas.DrawImage(snapshot, grab.Left, grab.Top, paint);
            canvas.Restore();
        }
    }
}
