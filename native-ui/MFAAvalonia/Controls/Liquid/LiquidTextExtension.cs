using Avalonia.Data;
using Avalonia.Markup.Xaml;
using MFAAvalonia.Helper;
using System;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// <c>{lq:T 中文原文}</c> — display text translated through ExText and refreshed when the language changes.
/// </summary>
public sealed class TExtension : MarkupExtension
{
    public TExtension() { }
    public TExtension(string text) => Text = text;
    public string Text { get; set; } = "";

    public override object ProvideValue(IServiceProvider serviceProvider) => new Binding(nameof(ExText.Revision))
    {
        Source = ExText.Instance,
        Mode = BindingMode.OneWay,
        Converter = new ExText.LabelConverter(),
        ConverterParameter = Text,
    };
}
