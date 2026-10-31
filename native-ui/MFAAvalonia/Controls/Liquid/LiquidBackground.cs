using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using Avalonia.VisualTree;
using System;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// Ambient window background: canvas colour, four slowly drifting colour orbs and an
/// optional user background image. Orbs are drawn as soft radial gradients; the drift
/// runs on a low frequency timer (the motion is a few pixels per second) and stops
/// entirely when motion is reduced or the ambient light is turned off.
/// </summary>
public class LiquidBackground : Control
{
    public static readonly StyledProperty<bool> AmbientProperty =
        AvaloniaProperty.Register<LiquidBackground, bool>(nameof(Ambient), true);
    public static readonly StyledProperty<bool> AnimateProperty =
        AvaloniaProperty.Register<LiquidBackground, bool>(nameof(Animate), true);
    public static readonly StyledProperty<IImage?> ImageProperty =
        AvaloniaProperty.Register<LiquidBackground, IImage?>(nameof(Image));
    public static readonly StyledProperty<double> ImageOpacityProperty =
        AvaloniaProperty.Register<LiquidBackground, double>(nameof(ImageOpacity), 0.2);

    public bool Ambient { get => GetValue(AmbientProperty); set => SetValue(AmbientProperty, value); }
    public bool Animate { get => GetValue(AnimateProperty); set => SetValue(AnimateProperty, value); }
    public IImage? Image { get => GetValue(ImageProperty); set => SetValue(ImageProperty, value); }
    public double ImageOpacity { get => GetValue(ImageOpacityProperty); set => SetValue(ImageOpacityProperty, value); }

    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromMilliseconds(110) };
    private readonly DateTime _start = DateTime.UtcNow;
    private Color _canvas = Color.Parse("#E9EEF6");
    private readonly Color[] _orbs = new Color[4];
    private double _orbOpacity = 0.58;
    private readonly IDisposable?[] _subscriptions = new IDisposable?[6];

    // size, anchor (fraction of width/height), anchor offset, drift vector, scale target, period seconds, reverse
    private static readonly (double Size, double Ax, double Ay, double Ox, double Oy, double Dx, double Dy, double Scale, double Period, bool Reverse)[] Blobs =
    [
        (620, 0, 0, -120, -200, 65, 45, 1.10, 24, false),
        (560, 1, 1, -460, -380, -55, -40, 1.08, 28, false),
        (460, 0.8, 0.2, -460, 0, -45, 55, 0.94, 22, false),
        (440, 0.22, 0.88, 0, -440, 65, 45, 1.10, 26, true),
    ];

    static LiquidBackground()
    {
        AffectsRender<LiquidBackground>(AmbientProperty, ImageProperty, ImageOpacityProperty);
    }

    public LiquidBackground()
    {
        IsHitTestVisible = false;
        _timer.Tick += (_, _) => InvalidateVisual();
    }

    protected override void OnAttachedToVisualTree(VisualTreeAttachmentEventArgs e)
    {
        base.OnAttachedToVisualTree(e);
        string[] keys = ["LgCanvasColor", "LgOrb1Color", "LgOrb2Color", "LgOrb3Color", "LgOrb4Color", "LgOrbOpacity"];
        for (int i = 0; i < keys.Length; i++)
        {
            int index = i;
            _subscriptions[i] = this.GetResourceObservable(keys[i]).Subscribe(new Avalonia.Reactive.AnonymousObserver<object?>(v =>
            {
                if (index == 0 && v is Color c) _canvas = c;
                else if (index is > 0 and < 5 && v is Color o) _orbs[index - 1] = o;
                else if (index == 5 && v is double d) _orbOpacity = d;
                InvalidateVisual();
            }));
        }
        LiquidTheme.ReduceMotionChanged += OnMotionChanged;
        UpdateTimer();
    }

    protected override void OnDetachedFromVisualTree(VisualTreeAttachmentEventArgs e)
    {
        base.OnDetachedFromVisualTree(e);
        foreach (var s in _subscriptions) s?.Dispose();
        LiquidTheme.ReduceMotionChanged -= OnMotionChanged;
        _timer.Stop();
    }

    private void OnMotionChanged(bool _) => UpdateTimer();

    protected override void OnPropertyChanged(AvaloniaPropertyChangedEventArgs change)
    {
        base.OnPropertyChanged(change);
        if (change.Property == AmbientProperty || change.Property == AnimateProperty || change.Property == IsVisibleProperty)
            UpdateTimer();
    }

    private void UpdateTimer()
    {
        var run = Ambient && Animate && !LiquidTheme.ReduceMotion && IsVisible && this.IsAttachedToVisualTree();
        if (run && !_timer.IsEnabled) _timer.Start();
        else if (!run && _timer.IsEnabled) _timer.Stop();
    }

    public override void Render(DrawingContext context)
    {
        var bounds = new Rect(Bounds.Size);
        context.FillRectangle(new SolidColorBrush(_canvas), bounds);
        if (Ambient)
        {
            var seconds = (Animate && !LiquidTheme.ReduceMotion) ? (DateTime.UtcNow - _start).TotalSeconds : 0;
            for (int i = 0; i < Blobs.Length; i++)
            {
                var b = Blobs[i];
                // ease-in-out alternate, matching the CSS keyframes
                var phase = (seconds / b.Period) % 2;
                var t = phase <= 1 ? phase : 2 - phase;
                if (b.Reverse) t = 1 - t;
                t = 0.5 - Math.Cos(Math.PI * t) / 2;
                var scale = 1 + (b.Scale - 1) * t;
                var size = b.Size * scale;
                var x = b.Ax * bounds.Width + b.Ox + b.Dx * t - (size - b.Size) / 2;
                var y = b.Ay * bounds.Height + b.Oy + b.Dy * t - (size - b.Size) / 2;
                var color = _orbs[i];
                var alpha = _orbOpacity;
                var brush = new RadialGradientBrush
                {
                    Center = RelativePoint.Center,
                    GradientOrigin = RelativePoint.Center,
                    RadiusX = new RelativeScalar(0.5, RelativeUnit.Relative),
                    RadiusY = new RelativeScalar(0.5, RelativeUnit.Relative),
                    GradientStops =
                    {
                        new GradientStop(Color.FromArgb((byte)(255 * alpha), color.R, color.G, color.B), 0),
                        new GradientStop(Color.FromArgb((byte)(255 * alpha * 0.62), color.R, color.G, color.B), 0.32),
                        new GradientStop(Color.FromArgb((byte)(255 * alpha * 0.22), color.R, color.G, color.B), 0.56),
                        new GradientStop(Color.FromArgb(0, color.R, color.G, color.B), 0.74),
                    }
                };
                context.DrawEllipse(brush, null, new Rect(x, y, size, size));
            }
        }
        if (Image is { } image && ImageOpacity > 0)
        {
            var source = image.Size;
            if (source.Width > 0 && source.Height > 0)
            {
                var scale = Math.Max(bounds.Width / source.Width, bounds.Height / source.Height);
                var w = source.Width * scale; var h = source.Height * scale;
                using (context.PushOpacity(ImageOpacity))
                    context.DrawImage(image, new Rect(0, 0, source.Width, source.Height), new Rect((bounds.Width - w) / 2, (bounds.Height - h) / 2, w, h));
            }
        }
    }
}
