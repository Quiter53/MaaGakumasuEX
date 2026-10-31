using Avalonia;
using Avalonia.Animation;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Media;
using Avalonia.Media.Transformation;
using Avalonia.Threading;
using System;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// Pill shaped single selection control. A single shared thumb follows the
/// measured bounds of the selected item, so language changes, DPI and window
/// resizing keep it aligned. Keyboard and automation come from ListBox.
/// </summary>
public class SegmentedControl : ListBox
{
    protected override Type StyleKeyOverride => typeof(SegmentedControl);

    private Border? _thumb;
    private Transitions? _thumbTransitions;
    private Panel? _host;
    private bool _placed;
    private Rect _last;

    static SegmentedControl()
    {
        SelectionModeProperty.OverrideDefaultValue<SegmentedControl>(SelectionMode.Single | SelectionMode.AlwaysSelected);
    }

    public SegmentedControl()
    {
        LiquidTheme.ReduceMotionChanged += OnReduceMotionChanged;
    }

    protected override void OnApplyTemplate(TemplateAppliedEventArgs e)
    {
        base.OnApplyTemplate(e);
        _thumb = e.NameScope.Find<Border>("PART_Thumb");
        _thumbTransitions = _thumb?.Transitions;
        _host = e.NameScope.Find<Panel>("PART_Host");
        _placed = false;
    }

    protected override void OnDetachedFromVisualTree(VisualTreeAttachmentEventArgs e)
    {
        base.OnDetachedFromVisualTree(e);
        LiquidTheme.ReduceMotionChanged -= OnReduceMotionChanged;
    }

    protected override void OnAttachedToVisualTree(VisualTreeAttachmentEventArgs e)
    {
        base.OnAttachedToVisualTree(e);
        LiquidTheme.ReduceMotionChanged -= OnReduceMotionChanged;
        LiquidTheme.ReduceMotionChanged += OnReduceMotionChanged;
        _placed = false;
    }

    private void OnReduceMotionChanged(bool reduce) => _placed = false;

    protected override void OnPropertyChanged(AvaloniaPropertyChangedEventArgs change)
    {
        base.OnPropertyChanged(change);
        if (change.Property == SelectedIndexProperty)
            Dispatcher.UIThread.Post(() => UpdateThumb(true), DispatcherPriority.Render);
    }

    protected override Size ArrangeOverride(Size finalSize)
    {
        var result = base.ArrangeOverride(finalSize);
        Dispatcher.UIThread.Post(() => UpdateThumb(_placed), DispatcherPriority.Render);
        return result;
    }

    private void UpdateThumb(bool animate)
    {
        if (_thumb == null || _host == null) return;
        var container = SelectedIndex >= 0 ? ContainerFromIndex(SelectedIndex) : null;
        if (container == null || !container.IsVisible || container.Bounds.Width <= 0)
        {
            _thumb.Opacity = 0;
            return;
        }
        var origin = container.TranslatePoint(new Point(0, 0), _host);
        if (origin == null) return;
        var rect = new Rect(origin.Value, container.Bounds.Size);
        if (_placed && rect == _last && _thumb.Opacity > 0) return;
        _last = rect;
        if (!animate || LiquidTheme.ReduceMotion) _thumb.Transitions = null;
        _thumb.Width = rect.Width;
        _thumb.Height = rect.Height;
        _thumb.RenderTransform = TransformOperations.Parse($"translate({rect.X.ToString(System.Globalization.CultureInfo.InvariantCulture)}px, {rect.Y.ToString(System.Globalization.CultureInfo.InvariantCulture)}px)");
        _thumb.Opacity = 1;
        if (!animate || LiquidTheme.ReduceMotion)
            Dispatcher.UIThread.Post(() => { if (_thumb != null) _thumb.Transitions = _thumbTransitions; }, DispatcherPriority.Background);
        _placed = true;
    }
}
