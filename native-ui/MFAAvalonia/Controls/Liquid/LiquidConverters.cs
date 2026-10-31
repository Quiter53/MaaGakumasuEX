using Avalonia.Data.Converters;

namespace MFAAvalonia.Controls.Liquid;

public static class LiquidConverters
{
    public static readonly IValueConverter Positive = new FuncValueConverter<double, bool>(v => v > 0);
}
