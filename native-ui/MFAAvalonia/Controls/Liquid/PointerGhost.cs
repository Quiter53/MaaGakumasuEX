using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Layout;
using System;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>Pointer coordinates and ghost placement always share the same overlay coordinate space.</summary>
public sealed class PointerGhost : IDisposable
{
    public OverlayLayer Layer { get; }
    private readonly Canvas _placement;
    private readonly Point _anchor;
    public PointerGhost(Control source, Control content, Point anchor)
    {
        Layer = OverlayLayer.GetOverlayLayer(source) ?? throw new InvalidOperationException("Drag source has no overlay");
        _anchor = anchor;
        _placement = new Canvas { Width = content.Width, Height = content.Height, IsHitTestVisible = false,
            HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Top, Opacity = 0.94 };
        _placement.Children.Add(content);
        Layer.Children.Add(_placement);
    }
    public void Move(Point point) => _placement.RenderTransform = LiquidTransforms.Translate(point.X - _anchor.X, point.Y - _anchor.Y);
    public void Dispose() => Layer.Children.Remove(_placement);
}
