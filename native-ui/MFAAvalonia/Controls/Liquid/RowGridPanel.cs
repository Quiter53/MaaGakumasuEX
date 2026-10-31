using Avalonia;
using Avalonia.Controls;
using System;
using System.Linq;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// Equal-width columns where each row is as tall as its tallest item (CSS grid auto rows).
/// Columns drop to one when the width is below <see cref="MinColumnWidth"/> per column.
/// </summary>
public class RowGridPanel : Panel
{
    public static readonly StyledProperty<int> ColumnsProperty = AvaloniaProperty.Register<RowGridPanel, int>(nameof(Columns), 2);
    public static readonly StyledProperty<double> SpacingProperty = AvaloniaProperty.Register<RowGridPanel, double>(nameof(Spacing), 14);
    public static readonly StyledProperty<double> MinColumnWidthProperty = AvaloniaProperty.Register<RowGridPanel, double>(nameof(MinColumnWidth), 360);

    public int Columns { get => GetValue(ColumnsProperty); set => SetValue(ColumnsProperty, value); }
    public double Spacing { get => GetValue(SpacingProperty); set => SetValue(SpacingProperty, value); }
    public double MinColumnWidth { get => GetValue(MinColumnWidthProperty); set => SetValue(MinColumnWidthProperty, value); }

    static RowGridPanel() => AffectsMeasure<RowGridPanel>(ColumnsProperty, SpacingProperty, MinColumnWidthProperty);

    private int EffectiveColumns(double width) =>
        double.IsInfinity(width) ? Math.Max(1, Columns) : Math.Clamp((int)((width + Spacing) / (MinColumnWidth + Spacing)), 1, Math.Max(1, Columns));

    protected override Size MeasureOverride(Size availableSize)
    {
        var cols = EffectiveColumns(availableSize.Width);
        var colWidth = double.IsInfinity(availableSize.Width) ? double.PositiveInfinity : Math.Max(0, (availableSize.Width - Spacing * (cols - 1)) / cols);
        var visible = Children.Where(c => c.IsVisible).ToList();
        double height = 0, width = 0;
        for (int i = 0; i < visible.Count; i += cols)
        {
            double row = 0;
            foreach (var child in visible.Skip(i).Take(cols))
            {
                child.Measure(new Size(colWidth, double.PositiveInfinity));
                row = Math.Max(row, child.DesiredSize.Height);
                width = Math.Max(width, child.DesiredSize.Width);
            }
            height += row + (i > 0 ? Spacing : 0);
        }
        return new Size(double.IsInfinity(availableSize.Width) ? width * cols + Spacing * (cols - 1) : availableSize.Width, height);
    }

    protected override Size ArrangeOverride(Size finalSize)
    {
        var cols = EffectiveColumns(finalSize.Width);
        var colWidth = Math.Max(0, (finalSize.Width - Spacing * (cols - 1)) / cols);
        var visible = Children.Where(c => c.IsVisible).ToList();
        double y = 0;
        for (int i = 0; i < visible.Count; i += cols)
        {
            var rowItems = visible.Skip(i).Take(cols).ToList();
            var row = rowItems.Max(c => c.DesiredSize.Height);
            for (int j = 0; j < rowItems.Count; j++)
                rowItems[j].Arrange(new Rect(j * (colWidth + Spacing), y, colWidth, row));
            y += row + Spacing;
        }
        return finalSize;
    }
}
