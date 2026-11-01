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
        if (script == "tutorial") { Tutorial(w, output); return; }
        if (script == "probe")
        {
            foreach (var (width, height) in new[] { (1600, 1000), (1280, 800), (1000, 640), (1600, 1000) })
            {
                w.Width = width; w.Height = height; Program.Pump(1200);
                var card = w.GetVisualDescendants().OfType<BoardPanel>().First(p => p.PanelId == "task_list" && p.IsEffectivelyVisible);
                var btn = card.GetVisualDescendants().OfType<Button>().First(b => b.Name == "SelectAllButton");
                var header = card.GetVisualDescendants().OfType<Grid>().First(g => g.Name == "PART_Header");
                var sv = w.GetVisualDescendants().OfType<ScrollViewer>().First(x => x.Name == "BoardScroll" && x.IsEffectivelyVisible);
                Console.WriteLine($"SCROLL0 viewport={sv.Viewport} extent={sv.Extent}"); Program.Pump(1500);
                Console.WriteLine($"SCROLL viewport={sv.Viewport} extent={sv.Extent} bounds={sv.Bounds.Size} board={((Control)sv.Content!).Bounds.Size}");
                Console.WriteLine($"PROBE w={width} card={card.Bounds.Width:0} btn={btn.Bounds} classes={string.Join(',', btn.Classes)} header={header.Bounds.Width:0}");
                foreach (var c in header.Children) Console.WriteLine($"   {c.GetType().Name} {c.Bounds} desired={c.DesiredSize}");
            }
            return;
        }
        if (script.StartsWith("lang:")) { LanguageHelper.ChangeLanguage(script[5..]); Program.Pump(500); }
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
                Program.Pump(500);
                var tq = w.GetVisualDescendants().OfType<TaskQueueView>().FirstOrDefault(v => v.IsEffectivelyVisible);
                if (tq?.DataContext is MFAAvalonia.ViewModels.Pages.TaskQueueViewModel vm)
                {
                    vm.AddTaskCommand.Execute(null);
                    Program.Pump(900);
                    Program.Save(w, $"{prefix}-dialog-addtask.png");
                    Instances.DialogManager.DismissDialog();
                    Program.Pump(500);
                }
                var root = AppContext.BaseDirectory;
                var custom = new MFAAvalonia.Views.UserControls.CustomizationTargetDialog(root, "[]");
                custom.AddCard("幸せのおまじない"); custom.AddCard("仕切り直し");
                var m1 = LiquidModal.Show(w, custom, 920, 760);
                Program.Pump(700); Program.Save(w, $"{prefix}-editor-customize.png"); m1?.Close(); Program.Pump(400);
                var memory = new MFAAvalonia.Views.UserControls.MemoryTargetDialog(root, true, "[]");
                var m2 = LiquidModal.Show(w, memory, 920, 760);
                Program.Pump(700); Program.Save(w, $"{prefix}-editor-deck.png"); m2?.Close(); Program.Pump(400);
                MFAAvalonia.Helper.ToastHelper.Info("出牌规则已保存", "已保存全部预设，当前选中预设生效");
                Program.Pump(700);
                Program.Save(w, $"{prefix}-toast.png");
            }
        }
    }

    /// <summary>Walks every first-use tutorial step and records whether its target was found.</summary>
    private static void Tutorial(Window w, string output)
    {
        var shell = w.GetVisualDescendants().OfType<MFAAvalonia.Views.Mobile.RootViewContent>().First();
        var overlay = w.GetVisualDescendants().OfType<MFAAvalonia.Views.UserControls.TeachingTipOverlay>().First();
        foreach (var theme in new[] { ThemeVariant.Light, ThemeVariant.Dark })
        {
            Instances.GuiSettingsUserControlModel.BaseTheme = theme;
            Program.Pump(500);
            shell.TryStartTutorial();
            Program.Pump(1200);
            var next = overlay.FindControl<Button>("NextButton")!;
            for (int step = 1; overlay.IsRunning && step < 40; step++)
            {
                var title = overlay.FindControl<TextBlock>("TipTitle")!.Text;
                var arrow = overlay.FindControl<Avalonia.Controls.Shapes.Polygon>("TipArrow")!.IsVisible;
                Console.WriteLine($"TUTORIAL {theme} {step:00} arrow={arrow} {title}");
                Program.Save(w, Path.Combine(output, $"{theme}-tutorial-{step:00}.png"));
                next.RaiseEvent(new Avalonia.Interactivity.RoutedEventArgs(Button.ClickEvent));
                Program.Pump(900);
            }
            LiquidNavigation.Navigate(LiquidPages.Tasks);
            Program.Pump(500);
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
