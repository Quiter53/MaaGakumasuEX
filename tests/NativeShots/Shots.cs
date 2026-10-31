using Avalonia.Controls;
using Avalonia.Styling;
using Avalonia.VisualTree;
using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.Helper;
using MFAAvalonia.Helper.ValueType;
using MFAAvalonia.Views.Pages;

internal static class Shots
{
    private static readonly string[] Pages =
        [LiquidPages.Tasks, LiquidPages.CardRules, LiquidPages.Drinks, LiquidPages.CardList, LiquidPages.Monitor, LiquidPages.Screenshot, LiquidPages.Settings];

    private static readonly string[] SettingSections = ["gui", "config", "timer", "perf", "game", "connect", "start", "notify", "hotkey", "update", "about"];

    public static void Run(Window w, string output, string script)
    {
        var sizes = script == "sizes" ? new[] { (1600, 1000), (1280, 800) } : new[] { (1600, 1000) };
        foreach (var (width, height) in sizes)
        {
            w.Width = width; w.Height = height;
            Program.Pump(400);
            foreach (var theme in new[] { ThemeVariant.Light, ThemeVariant.Dark })
            {
                Instances.GuiSettingsUserControlModel.BaseTheme = theme;
                Program.Pump(500);
                var prefix = Path.Combine(output, $"{theme}-{width}x{height}");
                SelectTask(w, "开始培育");
                foreach (var page in Pages)
                {
                    LiquidNavigation.Navigate(page);
                    Program.Pump(700);
                    Program.Save(w, $"{prefix}-{page}.png");
                    if (page == LiquidPages.CardRules && w.GetVisualDescendants().OfType<CardRulesView>().FirstOrDefault() is { } rules)
                    {
                        rules.OpenPicker(hand: true);
                        Program.Pump(700);
                        Program.Save(w, $"{prefix}-card-picker.png");
                        Avalonia.Headless.HeadlessWindowExtensions.KeyPress(w, Avalonia.Input.Key.Escape, Avalonia.Input.RawInputModifiers.None, Avalonia.Input.PhysicalKey.Escape, null);
                        Program.Pump(500);
                    }
                }
                if (script == "all")
                {
                    foreach (var section in SettingSections)
                    {
                        LiquidNavigation.NavigateSettings(section);
                        Program.Pump(500);
                        Program.Save(w, $"{prefix}-settings-{section}.png");
                    }
                }
                LiquidNavigation.Navigate(LiquidPages.Tasks);
            }
        }
    }

    private static void SelectTask(Window w, string name)
    {
        var list = w.GetVisualDescendants().OfType<ListBox>().FirstOrDefault(l => l.Name == "TaskListBox" && l.IsEffectivelyVisible);
        if (list?.ItemsSource is not IEnumerable<DragItemViewModel> items) return;
        var item = items.FirstOrDefault(i => i.Name == name);
        if (item == null) return;
        list.SelectedItem = item;
        item.EnableSetting = true;
        Program.Pump(600);
    }
}
