using Avalonia.Media.Transformation;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// Numeric transform construction. Building transform strings from doubles is unsafe: values close
/// to zero format as "1E-05", which TransformOperations.Parse rejects ("Invalid unit: E-05px")
/// whatever the culture. The builder produces the same operation types, so transitions still apply.
/// </summary>
public static class LiquidTransforms
{
    public static TransformOperations Translate(double x, double y)
    {
        var builder = TransformOperations.CreateBuilder(1);
        builder.AppendTranslate(x, y);
        return builder.Build();
    }
}
