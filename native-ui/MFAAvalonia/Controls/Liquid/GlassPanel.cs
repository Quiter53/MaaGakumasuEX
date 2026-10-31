using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// Glass surface used for panels (class <c>panel</c>, the default), grouped boxes
/// (<c>box</c>) and interactive tiles (<c>interactive</c>). The template draws the
/// fill, specular rim and elevation in one Border and a pointer sheen above it.
/// </summary>
public class GlassPanel : ContentControl
{
    public static readonly StyledProperty<BoxShadows> BoxShadowProperty =
        Border.BoxShadowProperty.AddOwner<GlassPanel>();

    public BoxShadows BoxShadow
    {
        get => GetValue(BoxShadowProperty);
        set => SetValue(BoxShadowProperty, value);
    }

    public static readonly StyledProperty<double> BlurRadiusProperty =
        AvaloniaProperty.Register<GlassPanel, double>(nameof(BlurRadius));

    /// <summary>Backdrop blur radius (0 = none). Used by dialogs and floating surfaces.</summary>
    public double BlurRadius
    {
        get => GetValue(BlurRadiusProperty);
        set => SetValue(BlurRadiusProperty, value);
    }

    public static readonly StyledProperty<bool> ClipContentProperty =
        AvaloniaProperty.Register<GlassPanel, bool>(nameof(ClipContent));

    /// <summary>Clips the content to the rounded outline (used by scrolling panels).</summary>
    public bool ClipContent
    {
        get => GetValue(ClipContentProperty);
        set => SetValue(ClipContentProperty, value);
    }
}
