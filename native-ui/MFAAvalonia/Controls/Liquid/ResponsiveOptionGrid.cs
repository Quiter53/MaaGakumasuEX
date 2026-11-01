using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using System;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// Measures task labels and editors in the normal layout pass. Unlike a SizeChanged callback,
/// this cannot outlive a removed task option and re-apply its templates after it loses resources.
/// </summary>
public sealed class ResponsiveOptionGrid : Grid
{
    private Control? _label, _editor;
    private HorizontalAlignment _editorAlignment;
    private bool? _stacked;

    public void Configure(Control label, Control editor)
    {
        _label = label;
        _editor = editor;
        _editorAlignment = editor.HorizontalAlignment;
        _stacked = null;
        InvalidateMeasure();
    }

    protected override Size MeasureOverride(Size availableSize)
    {
        if (_label == null || _editor == null) return base.MeasureOverride(availableSize);

        _label.Measure(new Size(double.PositiveInfinity, availableSize.Height));
        _editor.Measure(new Size(double.PositiveInfinity, availableSize.Height));
        var required = _label.DesiredSize.Width + ColumnSpacing + _editor.DesiredSize.Width;
        var stacked = availableSize.Width < required;
        if (_stacked != stacked)
        {
            _stacked = stacked;
            RowDefinitions = new RowDefinitions(stacked ? "Auto,Auto" : "Auto");
            ColumnDefinitions = new ColumnDefinitions(stacked ? "*" : "*,Auto");
            RowSpacing = stacked ? 6 : 0;
            SetRow(_label, 0);
            SetColumn(_label, 0);
            SetRow(_editor, stacked ? 1 : 0);
            SetColumn(_editor, stacked ? 0 : 1);
            _editor.HorizontalAlignment = stacked ? HorizontalAlignment.Left : _editorAlignment;
        }
        return base.MeasureOverride(availableSize);
    }
}
