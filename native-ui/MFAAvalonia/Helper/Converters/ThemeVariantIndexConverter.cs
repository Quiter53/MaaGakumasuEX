using Avalonia.Data.Converters;
using Avalonia.Data;
using Avalonia.Styling;
using System;
using System.Globalization;

namespace MFAAvalonia.Helper.Converters;

/// <summary>Maps Light/Dark to the index of the theme segmented control (0 = 浅色, 1 = 深色).</summary>
public sealed class ThemeVariantIndexConverter : IValueConverter
{
    public static readonly ThemeVariantIndexConverter Instance = new();
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) => Equals(value, ThemeVariant.Dark) ? 1 : 0;
    public object? ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) => value switch
    {
        0 => ThemeVariant.Light,
        1 => ThemeVariant.Dark,
        _ => BindingOperations.DoNothing,
    };
}
