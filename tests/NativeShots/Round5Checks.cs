using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Headless;
using Avalonia.Media;
using Avalonia.VisualTree;
using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;
using MFAAvalonia.Helper.ValueType;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.Views.Pages;
using MFAAvalonia.Views.UserControls;
using System.Reflection;

internal static class Round5Checks
{
    public static bool Run(Window w, string output)
    {
        var failures = new List<string>();
        var count = 0;
        void Check(bool ok, string name) { count++; Console.WriteLine((ok ? "PASS " : "FAIL ") + name); if (!ok) failures.Add(name); }
        LiquidNavigation.Navigate(LiquidPages.Tasks); Program.Pump(300);
        Shots.SelectTask(w, "开始培育"); Program.Pump(200);
        var taskView = w.GetVisualDescendants().OfType<TaskQueueView>().First();
        var vm = (TaskQueueViewModel)taskView.DataContext!;
        var source = vm.TaskItemViewModels.First(x => x.InterfaceItem?.Entry == "Produce");
        var builder = new TaskOptionGenerator(vm, () => { });
        var factory = typeof(TaskOptionGenerator).GetMethod("CreateDefaultSelectOption", BindingFlags.NonPublic | BindingFlags.Static)!;
        var add = typeof(TaskOptionGenerator).GetMethod("AddOption", BindingFlags.NonPublic | BindingFlags.Instance)!;
        var applicable = typeof(TaskOptionGenerator).GetMethod("IsOptionApplicable", BindingFlags.NonPublic | BindingFlags.Instance)!;
        var panel = new StackPanel();
        var probe = new Window { Width = 900, Height = 700, Content = new ScrollViewer { Content = panel } };
        probe.Show();
        int options = 0, cases = 0, filtered = 0;
        foreach (var (name, definition) in MaaProcessor.Interface!.Option!.ToArray())
        {
            panel.Children.Clear();
            var option = (MaaInterface.MaaInterfaceSelectOption)factory.Invoke(null, [name])!;
            try
            {
                add.Invoke(builder, [panel, option, source, true]); probe.UpdateLayout();
                if (!(bool)applicable.Invoke(builder, [definition])!) { filtered++; continue; }
                Check(panel.Children.Count > 0, "task option builds: " + name); options++;
                var selector = panel.GetVisualDescendants().OfType<SelectingItemsControl>()
                    .FirstOrDefault(x => ReferenceEquals(x.ItemsSource, definition.Cases));
                if (selector != null && definition.Cases != null)
                {
                    for (int index = 0; index < definition.Cases.Count; index++)
                    {
                        selector.SelectedIndex = index; probe.UpdateLayout();
                        Check(option.Index == index, $"selection reaches task data: {name}/{index}"); cases++;
                    }
                    var savedIndex = option.Index;
                    selector.SelectedIndex = -1;
                    Check(option.Index == savedIndex, "transient empty selection cannot save -1: " + name);
                    selector.SelectedIndex = savedIndex ?? 0;
                    probe.UpdateLayout();
                    if (selector is ComboBox combo)
                        Check(combo.GetVisualDescendants().OfType<TextBlock>().Any(t => t.Text == definition.Cases[combo.SelectedIndex].DisplayName),
                            "selection label returns after a transient empty selection: " + name);
                }
                Check(panel.GetVisualDescendants().OfType<Control>().All(x => double.IsFinite(x.Bounds.Width) && double.IsFinite(x.Bounds.Height)), "finite layout: " + name);
                if (name == "启用试镜手动接管")
                {
                    var choices = panel.GetVisualDescendants().OfType<ToggleButton>().Where(b => b.Classes.Contains("lg-option-choice")).ToArray();
                    Check(choices.Length == 3, "manual takeover exposes three independent choices");
                    choices[0].IsChecked = true;
                    Check(option.SelectedCases!.Contains("一镜") && option.SelectedCases.Contains("三镜"), "takeover preserves multiple selections");
                    choices[0].IsChecked = false;
                    Check(!option.SelectedCases.Contains("一镜") && option.SelectedCases.Contains("三镜"), "takeover deselects only the chosen audition");
                    foreach (var width in new[] { 900, 420 })
                    {
                        probe.Width = width; Program.Pump(150);
                        Check(choices.All(b => b.Bounds.Width < 160), "short multi-select choices keep natural width " + width);
                        Check(choices.All(b => b.Bounds.Height >= 38), "multi-select hit targets retain height " + width);
                        Program.Save(probe, Path.Combine(output, "manual-takeover-" + width + ".png"));
                    }
                    probe.Width = 900;
                }
                if (name == "使用道具")
                    Check(!panel.GetVisualDescendants().OfType<ToggleButton>().Where(b => b.Classes.Contains("lg-item-card"))
                        .SelectMany(b => b.GetVisualDescendants().OfType<TooltipBlock>()).Any(), "item cards do not repeat the image in a question bubble");
            }
            catch (Exception e) { Check(false, "task option exception " + name + ": " + (e.InnerException ?? e)); }
        }
        var delay = (MaaInterface.MaaInterfaceSelectOption)factory.Invoke(null, ["出牌后额外等待"])!;
        var validate = typeof(TaskQueueViewModel).GetMethod("ValidateOptionsRecursive", BindingFlags.NonPublic | BindingFlags.Static)!;
        foreach (var text in new[] { "-1", "5.01", "NaN", "Infinity", "1.234", "abc" })
        {
            delay.Data["post_play_delay"] = text;
            Check(validate.Invoke(null, [new[] { delay }]) is string, "invalid delay blocked before backend execution: " + text);
        }
        foreach (var text in new[] { "0", "1.5", "5" })
        {
            delay.Data["post_play_delay"] = text;
            Check(validate.Invoke(null, [new[] { delay }]) == null, "valid delay accepted: " + text);
        }
        var editor = new MemoryTargetDialog(AppPaths.InstallRoot, false, "[]");
        var targetCard = editor.Cards.First(c => c.Origin == "none" && c.Rarity == "SSR");
        Check(editor.Select(0, targetCard), "memory target accepts the catalog SSR");
        editor.Targets[0].Upgrade = "upgraded";
        editor.Targets[0].CustomMin = 1;
        editor.ShowSlots();
        var targetOption = (MaaInterface.MaaInterfaceSelectOption)factory.Invoke(null, ["回忆卡选择"])!;
        var targetRow = MemoryTargetControl.Create(targetOption, () => { });
        probe.Content = new StackPanel { Spacing = 20, Margin = new Thickness(24), Children = { targetRow, editor } };
        foreach (var theme in new[] { Avalonia.Styling.ThemeVariant.Light, Avalonia.Styling.ThemeVariant.Dark })
        {
            Instances.GuiSettingsUserControlModel.BaseTheme = theme;
            probe.RequestedThemeVariant = theme;
            probe.Background = (IBrush)probe.FindResource("LgCanvasBrush")!;
            probe.Foreground = (IBrush)probe.FindResource("LgTextMainBrush")!;
            Program.Pump(200);
            var artwork = editor.GetVisualDescendants().OfType<CardArtwork>().Single();
            Check(artwork.Rarity == "SSR" && artwork.Padding.Left == 2, "memory target uses inset rarity frame " + theme);
            Check(artwork.Child is Border { ClipToBounds: true }, "target art is clipped inside the frame " + theme);
            var edit = targetRow.GetVisualDescendants().OfType<Button>().Single();
            Check(edit.BorderThickness.Left == 1.5 && edit.BorderBrush is SolidColorBrush { Color.A: > 0 }, "edit target outline visible at rest " + theme);
            Program.Save(probe, Path.Combine(output, "memory-target-" + theme + ".png"));
        }
        Instances.GuiSettingsUserControlModel.BaseTheme = Avalonia.Styling.ThemeVariant.Light;
        probe.Close();
        foreach (var page in new[] { LiquidPages.Drinks, LiquidPages.CardList, LiquidPages.Settings })
        {
            LiquidNavigation.Navigate(page); Program.Pump(250);
            if (page == LiquidPages.Settings) w.GetVisualDescendants().OfType<SettingsView>().First().SelectSection("gui");
            Program.Pump(200);
            if (page == LiquidPages.CardList)
            {
                var card = w.GetVisualDescendants().OfType<Button>().First(b => b.Classes.Contains("lg-catalog-card") && b.IsEffectivelyVisible);
                var point = card.TranslatePoint(new Point(card.Bounds.Width / 2, 12), w)!.Value;
                w.MouseMove(point); Program.Pump(250);
                Check(card.IsPointerOver, "first catalog row receives hover");
                Program.Save(w, Path.Combine(output, "catalog-first-row-hover.png"));
            }
            if (page == LiquidPages.CardList)
            {
                var input = w.GetVisualDescendants().OfType<TextBox>().FirstOrDefault(b => b.IsEffectivelyVisible);
                Check(input != null, "catalog search input exists");
                input!.Focus(); Program.Pump(200); Program.Save(w, Path.Combine(output, "focused-input.png"));
            }
            foreach (var scale in new[] { 1.0, 1.5 })
            {
                Instances.GuiSettingsUserControlModel.FontScale = scale; Program.Pump(150);
                if (page == LiquidPages.CardList)
                {
                    var header = w.GetVisualDescendants().OfType<WrapPanel>().First(p => p.Name == "CatalogHeader");
                    Check(header.Children.All(c => c.Bounds.Right <= header.Bounds.Width + 1), "catalog header fits scale " + scale);
                    var tile = w.GetVisualDescendants().OfType<Button>().First(b => b.Classes.Contains("lg-catalog-card") && b.IsEffectivelyVisible);
                    Check(tile.Margin.Top >= 4, "catalog hover has space inside each row");
                }
                Program.Save(w, Path.Combine(output, page + "-" + scale.ToString("F1", System.Globalization.CultureInfo.InvariantCulture) + ".png"));
            }
        }
        Instances.GuiSettingsUserControlModel.FontScale = 1; Program.Pump(150);
        var slider = w.GetVisualDescendants().OfType<Slider>().First(s => s.IsEffectivelyVisible);
        var thumb = slider.GetVisualDescendants().OfType<Thumb>().First();
        Check(!thumb.ClipToBounds, "slider thumb does not clip its circular halo");
        foreach (var ellipse in thumb.GetVisualDescendants().OfType<Avalonia.Controls.Shapes.Ellipse>())
            Check(Math.Abs(ellipse.Bounds.Width - ellipse.Bounds.Height) < .1, "slider knob and halo are circular");
        LiquidNavigation.Navigate(LiquidPages.Drinks); Program.Pump(200);
        Check(w.GetVisualDescendants().OfType<DrinkSettingsView>().First().GetVisualDescendants().OfType<CardArtwork>().Count() == 28,
            "all 28 drinks use the shared artwork frame");
        Console.WriteLine($"OPTION_SWEEP options={options} cases={cases} filtered={filtered}");
        Console.WriteLine(failures.Count == 0 ? $"ROUND5_CHECKS_PASSED {count}" : $"ROUND5_CHECKS_FAILED {failures.Count}/{count}");
        return failures.Count == 0;
    }
}
