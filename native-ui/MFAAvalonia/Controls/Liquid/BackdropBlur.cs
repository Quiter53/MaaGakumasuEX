using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Platform;
using Avalonia.Rendering.SceneGraph;
using Avalonia.Skia;
using Avalonia.Threading;
using Avalonia.VisualTree;
using SkiaSharp;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// Native backdrop blur for overlays (CSS backdrop-filter equivalent inside the window).
/// <para>
/// The page content of the window (Window.Content: no overlay layer, no dialog hosts) is captured
/// once into a bitmap when the first blur surface appears, and every blur surface paints its
/// blurred, saturated region of that capture clipped to its rounded bounds. Sampling the live
/// render surface instead is unsound with partial (dirty-rect) redraws: a small repaint behind
/// the overlay (status dot, entrance animation) made the blur sample a surface holding part of the
/// previous, already blurred frame, which showed as flashing lines, squares and offset blocks.
/// A captured backdrop repaints identically for any dirty region.
/// </para>
/// The capture is refreshed when the window is resized, the theme changes, or a new overlay opens
/// after all previous ones closed. Page content behind an open dialog is therefore frozen while
/// it is open, which is not noticeable under the blur. Without Skia nothing is drawn and the glass
/// keeps its translucent fill.
/// </summary>
public class BackdropBlur : Control
{
    public static readonly StyledProperty<double> RadiusProperty = AvaloniaProperty.Register<BackdropBlur, double>(nameof(Radius), 20);
    public static readonly StyledProperty<double> SaturationProperty = AvaloniaProperty.Register<BackdropBlur, double>(nameof(Saturation), 1.8);
    public static readonly StyledProperty<CornerRadius> CornerRadiusProperty = Border.CornerRadiusProperty.AddOwner<BackdropBlur>();

    public double Radius { get => GetValue(RadiusProperty); set => SetValue(RadiusProperty, value); }
    public double Saturation { get => GetValue(SaturationProperty); set => SetValue(SaturationProperty, value); }
    public CornerRadius CornerRadius { get => GetValue(CornerRadiusProperty); set => SetValue(CornerRadiusProperty, value); }

    /// <summary>Global switch.</summary>
    public static bool Enabled { get; set; } = true;

    /// <summary>Set by the last blur draw: true when a captured backdrop was painted through Skia.</summary>
    public static bool? LastFrameSupported { get; private set; }

    static BackdropBlur()
    {
        AffectsRender<BackdropBlur>(RadiusProperty, SaturationProperty, CornerRadiusProperty);
    }

    public BackdropBlur() { IsHitTestVisible = false; }

    private Backdrop? _backdrop;

    protected override void OnAttachedToVisualTree(VisualTreeAttachmentEventArgs e)
    {
        base.OnAttachedToVisualTree(e);
        if (TopLevel.GetTopLevel(this) is Window window)
        {
            _backdrop = Backdrop.For(window);
            _backdrop.Add(this);
        }
    }

    protected override void OnDetachedFromVisualTree(VisualTreeAttachmentEventArgs e)
    {
        base.OnDetachedFromVisualTree(e);
        _backdrop?.Remove(this);
        _backdrop = null;
    }

    public override void Render(DrawingContext context)
    {
        if (!Enabled || Radius <= 0 || Bounds.Width < 1 || Bounds.Height < 1) return;
        var image = _backdrop?.Image;
        if (image == null || TopLevel.GetTopLevel(this) is not { } top) return;
        // Where this control's bounds land in window coordinates (render transforms included, e.g. the pop-in scale).
        if (this.TransformToVisual(top) is not { } toWindow) return;
        var windowRect = new Rect(Bounds.Size).TransformToAABB(toWindow);
        context.Custom(new BlurOperation(new Rect(Bounds.Size), windowRect, image, _backdrop!.Scale, Radius, Saturation, CornerRadius));
    }

    /// <summary>One captured page backdrop per window, shared by all its blur surfaces.</summary>
    private sealed class Backdrop
    {
        private static readonly ConditionalWeakTable<Window, Backdrop> ByWindow = new();
        private readonly Window _window;
        private readonly List<BackdropBlur> _users = new();
        private bool _queued;

        public SKImage? Image { get; private set; }
        public double Scale { get; private set; } = 1;

        private Backdrop(Window window)
        {
            _window = window;
            window.PropertyChanged += (_, e) =>
            {
                if (e.Property == Visual.BoundsProperty || e.Property == ThemeVariantScope.ActualThemeVariantProperty) Invalidate();
            };
        }

        public static Backdrop For(Window window) => ByWindow.GetValue(window, w => new Backdrop(w));

        public void Add(BackdropBlur user)
        {
            // A new overlay over a backdrop nobody uses any more (all overlays closed) gets a fresh capture. The last
            // capture keeps being drawn until then, so a dialog reopened right after closing never shows over the
            // unblurred page; without any capture yet, the first one is taken before the next frame.
            var first = _users.Count == 0;
            _users.Add(user);
            if (first) Invalidate(Image == null ? DispatcherPriority.Render : DispatcherPriority.Background);
        }

        public void Remove(BackdropBlur user) => _users.Remove(user);

        private void Invalidate() => Invalidate(DispatcherPriority.Background);

        private void Invalidate(DispatcherPriority priority)
        {
            if (_queued) return;
            _queued = true;
            Dispatcher.UIThread.Post(Capture, priority);
        }

        private void Capture()
        {
            _queued = false;
            if (_users.Count == 0) return;
            var scale = _window.RenderScaling;
            var width = (int)Math.Ceiling(_window.ClientSize.Width * scale);
            var height = (int)Math.Ceiling(_window.ClientSize.Height * scale);
            if (width < 1 || height < 1) return;
            // Render the window without its overlay surfaces (dialogs, toasts, tutorial): a blur must
            // never sample itself. Opacity is restored before the frame is committed, so nothing flickers.
            var hidden = _window.GetVisualDescendants().OfType<Visual>()
                .Where(v => v is OverlayLayer or SukiUI.Controls.SukiDialogHost or SukiUI.Controls.SukiToastHost
                            || v.GetType().Name == "TeachingTipOverlay")
                .Select(v => (v, v.Opacity)).ToList();
            using var target = new RenderTargetBitmap(new PixelSize(width, height), new Vector(96 * scale, 96 * scale));
            try
            {
                foreach (var (v, _) in hidden) v.Opacity = 0;
                target.Render(_window);
            }
            finally
            {
                foreach (var (v, o) in hidden) v.Opacity = o;
            }
            var info = new SKImageInfo(width, height, SKColorType.Bgra8888, SKAlphaType.Premul);
            var pixels = new byte[info.BytesSize];
            unsafe
            {
                fixed (byte* p = pixels)
                    target.CopyPixels(new PixelRect(0, 0, width, height), (IntPtr)p, pixels.Length, info.RowBytes);
            }
            Image?.Dispose();
            Image = SKImage.FromPixelCopy(info, pixels);
            Scale = scale;
            foreach (var user in _users.ToList()) user.InvalidateVisual();
        }
    }

    private sealed class BlurOperation(Rect local, Rect windowRect, SKImage image, double imageScale, double radius, double saturation, CornerRadius corners) : ICustomDrawOperation
    {
        public Rect Bounds => local;
        public bool HitTest(Point p) => false;
        public bool Equals(ICustomDrawOperation? other) => false;
        public void Dispose() { }

        public void Render(ImmediateDrawingContext context)
        {
            var feature = context.TryGetFeature<ISkiaSharpApiLeaseFeature>();
            if (feature == null) { LastFrameSupported = false; return; }
            using var lease = feature.Lease();
            var canvas = lease.SkCanvas;

            // Source: this control's window area in the captured image, padded so the blur has context.
            var pad = radius * 2;
            var src = new SKRect((float)((windowRect.X - pad) * imageScale), (float)((windowRect.Y - pad) * imageScale),
                (float)((windowRect.Right + pad) * imageScale), (float)((windowRect.Bottom + pad) * imageScale));
            var clamped = SKRect.Intersect(src, new SKRect(0, 0, image.Width, image.Height));
            if (clamped.Width < 1 || clamped.Height < 1) return;
            // Destination in local coordinates: same padded area mapped back from the window rectangle.
            float sx = (float)(local.Width / windowRect.Width), sy = (float)(local.Height / windowRect.Height);
            var dst = new SKRect(
                (float)((clamped.Left / imageScale - windowRect.X) * sx), (float)((clamped.Top / imageScale - windowRect.Y) * sy),
                (float)((clamped.Right / imageScale - windowRect.X) * sx), (float)((clamped.Bottom / imageScale - windowRect.Y) * sy));

            var s = (float)saturation;
            float lr = 0.2126f, lg = 0.7152f, lb = 0.0722f;
            float[] sat =
            [
                lr * (1 - s) + s, lg * (1 - s), lb * (1 - s), 0, 0,
                lr * (1 - s), lg * (1 - s) + s, lb * (1 - s), 0, 0,
                lr * (1 - s), lg * (1 - s), lb * (1 - s) + s, 0, 0,
                0, 0, 0, 1, 0,
            ];
            using var blur = SKImageFilter.CreateBlur((float)(radius / 2), (float)(radius / 2), SKShaderTileMode.Clamp);
            using var color = SKColorFilter.CreateColorMatrix(sat);
            using var paint = new SKPaint { ImageFilter = blur, ColorFilter = color, IsAntialias = true };

            canvas.Save();
            using (var rr = new SKRoundRect(new SKRect(0, 0, (float)local.Width, (float)local.Height), (float)corners.TopLeft, (float)corners.TopLeft))
                canvas.ClipRoundRect(rr, antialias: true);
            canvas.DrawImage(image, clamped, dst, new SKSamplingOptions(SKFilterMode.Linear), paint);
            canvas.Restore();
            LastFrameSupported = true;
        }
    }
}
