using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using Avalonia.Controls;
using Avalonia.Controls.Templates;
using Avalonia.Data;
using Avalonia.Data.Converters;
using Newtonsoft.Json;

namespace MFAAvalonia.Helper;

/// <summary>Display-only translations. Saved card names, preset keys and filters stay stable.</summary>
public sealed class ExText : INotifyPropertyChanged
{
    public static ExText Instance { get; } = new();
    private static readonly Dictionary<string, string[]> Messages = Load();
    private static readonly (Regex Pattern, string[] Translations)[] Formats = Messages
        .Where(p => p.Key.Contains("{0}"))
        .Select(p => (new Regex("^" + Regex.Replace(Regex.Escape(p.Key), @"\\\{\d+}", "(.*?)") + "$", RegexOptions.Singleline), p.Value)).ToArray();
    private static Dictionary<string, string[]> Load()
    {
        using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("MFAAvalonia.ExTranslations.json")!;
        using var reader = new System.IO.StreamReader(stream);
        return JsonConvert.DeserializeObject<Dictionary<string, string[]>>(reader.ReadToEnd())!;
    }
    private ExText() => LanguageHelper.LanguageChanged += (_, _) => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Revision)));
    public string Revision => LanguageHelper.CurrentLanguage;
    public event PropertyChangedEventHandler? PropertyChanged;
    public static string T(string? source)
    {
        if (string.IsNullOrEmpty(source)) return source ?? "";
        var index = LanguageHelper.CurrentLanguage switch { "en-US" => 0, "ja-JP" => 1, "zh-Hant" => 2, _ => -1 };
        if (index < 0) return source;
        if (Messages.TryGetValue(source, out var values)) return values[index];
        foreach (var (pattern, translations) in Formats)
        {
            var match = pattern.Match(source);
            if (match.Success) return string.Format(CultureInfo.CurrentCulture, translations[index], match.Groups.Cast<Group>().Skip(1).Select(g => (object)g.Value).ToArray());
        }
        // Composed badges keep card names/ranks intact while translating their labels.
        if (source.Contains(" · ")) return string.Join(" · ", source.Split(" · ").Select(T));
        return source;
    }
    public static IDataTemplate ItemTemplate { get; } = new FuncDataTemplate<object>((item, _) =>
    {
        var text = new TextBlock();
        text.Bind(TextBlock.TextProperty, new Binding(nameof(Revision)) { Source = Instance, Converter = new LabelConverter(), ConverterParameter = item });
        return text;
    });
    public sealed class LabelConverter : IValueConverter
    {
        public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) => T(parameter?.ToString() ?? value?.ToString());
        public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) => throw new NotSupportedException();
    }
}

public sealed class ExLabelConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) => ExText.T(value?.ToString());
    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) => throw new NotSupportedException();
}
