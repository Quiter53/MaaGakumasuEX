using Avalonia.Controls;
using Avalonia.Styling;
using MFAAvalonia.Helper;

internal static class Shots
{
    public static void Run(Window w, string output, string script)
    {
        foreach (var theme in new[] { ThemeVariant.Light, ThemeVariant.Dark })
        {
            Instances.GuiSettingsUserControlModel.BaseTheme = theme;
            Program.Pump(600);
            Program.Save(w, Path.Combine(output, $"{theme}-home.png"));
        }
    }
}
