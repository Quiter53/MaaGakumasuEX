using Avalonia;
using Avalonia.Controls;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// One preference row: title and description on the left, the control on the right
/// (prototype settings layout). Content is the control.
/// </summary>
public class SettingRow : ContentControl
{
    public static readonly StyledProperty<string?> TitleProperty = AvaloniaProperty.Register<SettingRow, string?>(nameof(Title));
    public static readonly StyledProperty<string?> DescriptionProperty = AvaloniaProperty.Register<SettingRow, string?>(nameof(Description));
    public static readonly StyledProperty<object?> FooterProperty = AvaloniaProperty.Register<SettingRow, object?>(nameof(Footer));

    public string? Title { get => GetValue(TitleProperty); set => SetValue(TitleProperty, value); }
    public string? Description { get => GetValue(DescriptionProperty); set => SetValue(DescriptionProperty, value); }
    /// <summary>Optional full width content below the row (e.g. a slider).</summary>
    public object? Footer { get => GetValue(FooterProperty); set => SetValue(FooterProperty, value); }
}
