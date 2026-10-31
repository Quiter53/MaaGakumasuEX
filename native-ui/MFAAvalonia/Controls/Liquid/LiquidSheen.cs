using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Input;
using Avalonia.Media;
using Avalonia.Styling;
using Avalonia.VisualTree;
using System;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// Pointer-following specular highlight. Set <c>Track="True"</c> on a non hit-testable
/// layer inside a control template; the layer listens only to its own templated
/// parent, so moving the pointer never walks the rest of the window.
/// </summary>
public static class LiquidSheen
{
    public static readonly AttachedProperty<bool> TrackProperty =
        AvaloniaProperty.RegisterAttached<Control, bool>("Track", typeof(LiquidSheen));

    /// <summary>Radius of the highlight circle in device independent pixels.</summary>
    public static readonly AttachedProperty<double> RadiusProperty =
        AvaloniaProperty.RegisterAttached<Control, double>("Radius", typeof(LiquidSheen), 95);

    /// <summary>Use the softer panel colour instead of the control highlight colour.</summary>
    public static readonly AttachedProperty<bool> PanelProperty =
        AvaloniaProperty.RegisterAttached<Control, bool>("Panel", typeof(LiquidSheen));

    private static readonly AttachedProperty<SheenState?> StateProperty =
        AvaloniaProperty.RegisterAttached<Control, SheenState?>("State", typeof(LiquidSheen));

    public static void SetTrack(Control element, bool value) => element.SetValue(TrackProperty, value);
    public static bool GetTrack(Control element) => element.GetValue(TrackProperty);
    public static void SetRadius(Control element, double value) => element.SetValue(RadiusProperty, value);
    public static double GetRadius(Control element) => element.GetValue(RadiusProperty);
    public static void SetPanel(Control element, bool value) => element.SetValue(PanelProperty, value);
    public static bool GetPanel(Control element) => element.GetValue(PanelProperty);

    static LiquidSheen()
    {
        TrackProperty.Changed.AddClassHandler<Control>((layer, e) =>
        {
            if (e.NewValue is true)
            {
                layer.AttachedToVisualTree += OnAttached;
                layer.DetachedFromVisualTree += OnDetached;
                if (layer.IsAttachedToVisualTree()) Attach(layer);
            }
            else
            {
                layer.AttachedToVisualTree -= OnAttached;
                layer.DetachedFromVisualTree -= OnDetached;
                Detach(layer);
            }
        });
    }

    private static void OnAttached(object? sender, VisualTreeAttachmentEventArgs e)
    {
        if (sender is Control layer) Attach(layer);
    }

    private static void OnDetached(object? sender, VisualTreeAttachmentEventArgs e)
    {
        if (sender is Control layer) Detach(layer);
    }

    private static void Attach(Control layer)
    {
        Detach(layer);
        var host = layer.TemplatedParent as InputElement ?? layer.GetVisualParent() as InputElement;
        if (host == null) return;
        var state = new SheenState(layer, host);
        layer.SetValue(StateProperty, state);
        state.Subscribe();
    }

    private static void Detach(Control layer)
    {
        if (layer.GetValue(StateProperty) is { } state)
        {
            state.Dispose();
            layer.ClearValue(StateProperty);
        }
    }

    private sealed class SheenState : IDisposable
    {
        private readonly Control _layer;
        private readonly InputElement _host;
        private readonly RadialGradientBrush _brush = new();
        private readonly GradientStop _core = new() { Offset = 0 };
        private readonly GradientStop _mid = new() { Offset = 0.45 };
        private readonly GradientStop _edge = new() { Offset = 1, Color = Colors.Transparent };
        private IDisposable? _coreSubscription, _midSubscription;

        public SheenState(Control layer, InputElement host)
        {
            _layer = layer;
            _host = host;
            _brush.GradientStops.Add(_core);
            _brush.GradientStops.Add(_mid);
            _brush.GradientStops.Add(_edge);
            var radius = GetRadius(layer);
            _brush.RadiusX = new RelativeScalar(radius, RelativeUnit.Absolute);
            _brush.RadiusY = new RelativeScalar(radius, RelativeUnit.Absolute);
        }

        public void Subscribe()
        {
            var panel = GetPanel(_layer);
            _coreSubscription = _layer.GetResourceObservable(panel ? "LgPanelSheenColor" : "LgSheenCoreColor").Subscribe(new Avalonia.Reactive.AnonymousObserver<object?>(v =>
            {
                if (v is Color c) _core.Color = c;
            }));
            _midSubscription = _layer.GetResourceObservable(panel ? "LgPanelSheenColor" : "LgSheenMidColor").Subscribe(new Avalonia.Reactive.AnonymousObserver<object?>(v =>
            {
                if (v is Color c) _mid.Color = panel ? Color.FromArgb((byte)(c.A / 3), c.R, c.G, c.B) : c;
            }));
            _host.PointerMoved += OnMoved;
            _host.PointerEntered += OnMoved;
            if (_layer is Border b) b.Background = _brush;
            else if (_layer is Panel p) p.Background = _brush;
        }

        private void OnMoved(object? sender, PointerEventArgs e)
        {
            var size = _layer.Bounds.Size;
            if (size.Width <= 0 || size.Height <= 0) return;
            var point = e.GetPosition(_layer);
            var relative = new RelativePoint(point.X, point.Y, RelativeUnit.Absolute);
            _brush.Center = relative;
            _brush.GradientOrigin = relative;
        }

        public void Dispose()
        {
            _host.PointerMoved -= OnMoved;
            _host.PointerEntered -= OnMoved;
            _coreSubscription?.Dispose();
            _midSubscription?.Dispose();
        }
    }
}
