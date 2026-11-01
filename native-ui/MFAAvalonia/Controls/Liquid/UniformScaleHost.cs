using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using System;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>Fills the current viewport at every scale; never reuses a previous transformed desired size.</summary>
public sealed class UniformScaleHost : Decorator
{
    public static readonly StyledProperty<double> ScaleProperty =
        AvaloniaProperty.Register<UniformScaleHost, double>(nameof(Scale), 1,
            validate: value => double.IsFinite(value) && value > 0);
    public double Scale { get => GetValue(ScaleProperty); set => SetValue(ScaleProperty, value); }
    static UniformScaleHost() => AffectsMeasure<UniformScaleHost>(ScaleProperty);

    protected override Size MeasureOverride(Size availableSize)
    {
        if (Child == null) return default;
        Child.Measure(new Size(availableSize.Width / Scale, availableSize.Height / Scale));
        return new Size(Child.DesiredSize.Width * Scale, Child.DesiredSize.Height * Scale);
    }

    protected override Size ArrangeOverride(Size finalSize)
    {
        if (Child != null)
        {
            Child.RenderTransformOrigin = new RelativePoint(0, 0, RelativeUnit.Absolute);
            Child.Arrange(new Rect(0, 0, finalSize.Width / Scale, finalSize.Height / Scale));
            // Margin/alignment are layout offsets outside RenderTransform. Scale them too,
            // otherwise a centered root drifts when the slider changes its logical size.
            Child.RenderTransform = new MatrixTransform(new Matrix(Scale, 0, 0, Scale,
                Child.Bounds.X * (Scale - 1), Child.Bounds.Y * (Scale - 1)));
        }
        return finalSize;
    }
}
