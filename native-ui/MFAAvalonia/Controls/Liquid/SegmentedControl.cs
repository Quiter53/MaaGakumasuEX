using Avalonia;
using Avalonia.Animation;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Media;
using Avalonia.VisualTree;
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

    // Selection comes only from the bound data. AlwaysSelected is deliberately not used: it would
    // select the first item on its own while the control is attached and overwrite saved choices.
    static SegmentedControl()
    {
        SelectionModeProperty.OverrideDefaultValue<SegmentedControl>(SelectionMode.Single);
    }

    public SegmentedControl()
    {
        LayoutUpdated += (_, _) => UpdateThumb(_placed);
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
        if (change.Property == SelectedIndexProperty) UpdateThumb(_placed);
    }

    protected override Size ArrangeOverride(Size finalSize)
    {
        var result = base.ArrangeOverride(finalSize);
        UpdateThumb(_placed);
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
        _thumb.Transitions = animate && _placed && !LiquidTheme.ReduceMotion ? _thumbTransitions : null;
        _thumb.Width = rect.Width;
        _thumb.Height = rect.Height;
        _thumb.RenderTransform = LiquidTransforms.Translate(rect.X, rect.Y);
        _thumb.Opacity = 1;
        _placed = true;
    }
}
