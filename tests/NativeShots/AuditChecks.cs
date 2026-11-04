using Avalonia;
using Avalonia.Controls;
using Avalonia.Styling;
using Avalonia.VisualTree;
using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.Helper;
using MFAAvalonia.Views.Pages;
using System.Text.Json;

internal static class AuditChecks
{
    public static bool Run(Window window, string output, string[] pages, string[] sections)
    {
        var checks = new List<object>();
        var failures = 0;
        void Check(bool ok, string name)
        {
            checks.Add(new { name, ok });
            if (!ok) failures++;
            Console.WriteLine((ok ? "PASS " : "FAIL ") + name);
        }
        void Inspect(string name)
        {
            var controls = window.GetVisualDescendants().OfType<Control>().Where(c => c.IsEffectivelyVisible).ToArray();
            Check(controls.Length > 20, name + " renders controls");
            Check(controls.All(c => double.IsFinite(c.Bounds.X) && double.IsFinite(c.Bounds.Y)
                && double.IsFinite(c.Bounds.Width) && double.IsFinite(c.Bounds.Height)), name + " has finite control bounds");
            var missingIcons = controls.OfType<LiquidIcon>().Where(icon => !LiquidIcon.Exists(icon.Kind) || icon.Foreground == null)
                .Select(icon => icon.Kind ?? "<null>").Distinct().ToArray();
            Check(missingIcons.Length == 0, name + " defines all visible icons" + (missingIcons.Length == 0 ? "" : " [" + string.Join(", ", missingIcons) + "]"));
            foreach (var row in controls.OfType<SettingRow>())
            {
                bool Present(Control? part) => part == null || !part.IsVisible || part.IsEffectivelyVisible && part.Bounds.Width > 0 && part.Bounds.Height > 0;
                Check(Present(row.Content as Control) && Present(row.Footer as Control)
                    && (row.Content != null || row.Footer != null), name + " renders setting " + row.Title);
            }
            Program.Save(window, Path.Combine(output, name + ".png"));
        }

        foreach (var (width, height) in new[] { (1280, 800), (1600, 1000) })
        foreach (var theme in new[] { ThemeVariant.Light, ThemeVariant.Dark })
        {
            window.Width = width;
            window.Height = height;
            Instances.GuiSettingsUserControlModel.BaseTheme = theme;
            Program.Pump(400);
            var prefix = $"{width}x{height}-{theme}";
            Shots.SelectTask(window, "开始培育");
            foreach (var page in pages)
            {
                LiquidNavigation.Navigate(page);
                Program.Pump(350);
                var expectedType = page switch
                {
                    LiquidPages.Tasks => typeof(TaskQueueView),
                    LiquidPages.CardRules => typeof(CardRulesView),
                    LiquidPages.Drinks => typeof(DrinkSettingsView),
                    LiquidPages.CardList => typeof(CardListSettingsView),
                    LiquidPages.Monitor => typeof(MonitorView),
                    LiquidPages.Screenshot => typeof(ScreenshotView),
                    _ => typeof(SettingsView),
                };
                Check(LiquidNavigation.CurrentPage == page && window.GetVisualDescendants().OfType<Control>()
                    .Any(c => expectedType.IsInstanceOfType(c) && c.IsEffectivelyVisible && c.Bounds.Width > 0 && c.Bounds.Height > 0),
                    prefix + " navigates to " + page);
                Inspect(prefix + "-" + page);
                if (page != LiquidPages.Settings) continue;
                var settings = window.GetVisualDescendants().OfType<SettingsView>().First(v => v.IsEffectivelyVisible);
                foreach (var section in sections)
                {
                    settings.SelectSection(section);
                    Program.Pump(250);
                    Check(settings.CurrentSectionView is { IsEffectivelyVisible: true } current && current.Bounds.Width > 0 && current.Bounds.Height > 0,
                        prefix + " renders settings section " + section);
                    if (section == "gui")
                    {
                        var themeSelector = settings.CurrentSectionView!.GetVisualDescendants().OfType<SegmentedControl>().First();
                        var savedIndex = themeSelector.SelectedIndex;
                        themeSelector.SelectedIndex = -1;
                        Check(Equals(Instances.GuiSettingsUserControlModel.BaseTheme, theme), prefix + " transient theme selection preserves the selected theme");
                        themeSelector.SelectedIndex = savedIndex;
                    }
                    Inspect(prefix + "-settings-" + section);
                }
            }
        }
        File.WriteAllText(Path.Combine(output, "ui-audit.json"), JsonSerializer.Serialize(new { result = failures == 0 ? "pass" : "fail", checks }, new JsonSerializerOptions { WriteIndented = true }));
        Console.WriteLine(failures == 0 ? $"UI_AUDIT_PASSED {checks.Count}" : $"UI_AUDIT_FAILED {failures}/{checks.Count}");
        return failures == 0;
    }
}
