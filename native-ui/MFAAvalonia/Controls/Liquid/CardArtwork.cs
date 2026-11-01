using Avalonia;
using Avalonia.Animation;
using Avalonia.Controls;
using Avalonia.Media;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>The artwork is inset inside its own clip; it cannot paint over the rarity frame's corners.</summary>
public sealed class CardArtwork : Border
{
    public static readonly StyledProperty<IImage?> SourceProperty = AvaloniaProperty.Register<CardArtwork, IImage?>(nameof(Source));
    public static readonly StyledProperty<string> RarityProperty = AvaloniaProperty.Register<CardArtwork, string>(nameof(Rarity), "R");
    public IImage? Source { get => GetValue(SourceProperty); set => SetValue(SourceProperty, value); }
    public string Rarity { get => GetValue(RarityProperty); set => SetValue(RarityProperty, value); }
    private readonly Image _image = new() { Stretch = Stretch.UniformToFill };
    private readonly Border _inner = new() { ClipToBounds = true };
    private LinearGradientBrush? _frame;

    public CardArtwork()
    {
        Padding = new Thickness(2);
        _inner.Child = _image;
        Child = _inner;
        RenderTransformOrigin = RelativePoint.Center;
        RenderTransform = LiquidTransforms.Translate(0, 0);
        Transitions = new Transitions { new TransformOperationsTransition { Property = RenderTransformProperty,
            Duration = System.TimeSpan.FromMilliseconds(160), Easing = LiquidStage.Spring } };
        PointerEntered += (_, _) => { if (!LiquidTheme.ReduceMotion) RenderTransform = Avalonia.Media.Transformation.TransformOperations.Parse("scale(1.06)"); };
        PointerExited += (_, _) => RenderTransform = LiquidTransforms.Translate(0, 0);
        PointerMoved += (_, e) =>
        {
            if (LiquidTheme.ReduceMotion || _frame == null || Bounds.Width <= 0) return;
            var x = System.Math.Clamp(e.GetPosition(this).X / Bounds.Width, 0, 1);
            _frame.StartPoint = new RelativePoint(x * 0.6, 0, RelativeUnit.Relative);
            _frame.EndPoint = new RelativePoint(0.4 + x * 0.6, 1, RelativeUnit.Relative);
        };
        UpdateFrame();
    }

    protected override void OnPropertyChanged(AvaloniaPropertyChangedEventArgs change)
    {
        base.OnPropertyChanged(change);
        if (_inner == null) return;
        if (change.Property == SourceProperty) _image.Source = Source;
        if (change.Property == RarityProperty || change.Property == CornerRadiusProperty) UpdateFrame();
    }

    private void UpdateFrame()
    {
        var colors = Rarity switch
        {
            "Drink" => new[] { "#80D9F0", "#F4FCFF", "#A6B4F7", "#D6F5FC" },
            "SSR" => new[] { "#AD71FF", "#A7EEFF", "#FFF6AF", "#ECA5FF", "#9264FF" },
            "SR" => new[] { "#FFA51A", "#FFF0A6", "#F8C956", "#FF9300" },
            "LR" => new[] { "#FF71BA", "#FFF6B5", "#A4F3FF", "#BD7BFF" },
            _ => new[] { "#8DA6C0", "#EAF7FF", "#9BAAC6" }
        };
        var gradient = new LinearGradientBrush { StartPoint = new RelativePoint(0, 0, RelativeUnit.Relative), EndPoint = new RelativePoint(1, 1, RelativeUnit.Relative) };
        for (int i = 0; i < colors.Length; i++) gradient.GradientStops.Add(new GradientStop(Color.Parse(colors[i]), (double)i / (colors.Length - 1)));
        Background = _frame = gradient;
        _inner.CornerRadius = new CornerRadius(System.Math.Max(0, CornerRadius.TopLeft - 2));
        BoxShadow = BoxShadows.Parse("0 1 4 0 #22000000");
    }
}
