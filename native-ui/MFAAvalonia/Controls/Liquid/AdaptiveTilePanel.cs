using Avalonia;
using Avalonia.Controls;
using System;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>CSS auto-fill/minmax equivalent: equal-width tiles without queued size mutations.</summary>
public sealed class AdaptiveTilePanel : Panel
{
    public double MinItemWidth { get; set; } = 64;
    public double Gap { get; set; } = 10;
    public int MaxColumns { get; set; } = int.MaxValue;

    private int Columns(double width) => Math.Max(1, Math.Min(MaxColumns, double.IsFinite(width)
        ? (int)Math.Floor((width + Gap) / (MinItemWidth + Gap)) : Math.Max(1, Children.Count)));

    protected override Size MeasureOverride(Size availableSize)
    {
        if (Children.Count == 0) return default;
        var columns = Columns(availableSize.Width);
        var width = double.IsFinite(availableSize.Width) ? availableSize.Width : columns * (MinItemWidth + Gap) - Gap;
        var tileWidth = Math.Max(0, (width - (columns - 1) * Gap) / columns);
        double height = 0, rowHeight = 0;
        for (var i = 0; i < Children.Count; i++)
        {
            Children[i].Measure(new Size(tileWidth, double.PositiveInfinity));
            rowHeight = Math.Max(rowHeight, Children[i].DesiredSize.Height);
            if (i % columns == columns - 1 || i == Children.Count - 1)
            {
                height += rowHeight + (i >= columns ? Gap : 0);
                rowHeight = 0;
            }
        }
        return new Size(width, height);
    }

    protected override Size ArrangeOverride(Size finalSize)
    {
        var columns = Columns(finalSize.Width);
        var width = Math.Max(0, (finalSize.Width - (columns - 1) * Gap) / columns);
        double y = 0;
        for (var first = 0; first < Children.Count; first += columns)
        {
            var end = Math.Min(first + columns, Children.Count);
            double height = 0;
            for (var i = first; i < end; i++) height = Math.Max(height, Children[i].DesiredSize.Height);
            for (var i = first; i < end; i++) Children[i].Arrange(new Rect((i - first) * (width + Gap), y, width, height));
            y += height + Gap;
        }
        return finalSize;
    }
}
