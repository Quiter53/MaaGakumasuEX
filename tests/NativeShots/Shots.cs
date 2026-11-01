using Avalonia.Headless;
using Avalonia.Controls.Primitives;
using Avalonia;
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
        if (script == "button") { ButtonProbe(output); return; }
        if (script == "modal-frames") { ModalFrames(w, output); return; }
        if (script == "taskopts") { Environment.ExitCode = TaskOptionChecks(w) ? 0 : 1; return; }
        if (script == "verify")
        {
            // Runs the in-app verifier (pass "--ui-verify <dir>" too) and waits for its report.
            var dir = MFAAvalonia.Helper.UiVerification.OutputDirectory ?? throw new InvalidOperationException("--ui-verify <dir> missing");
            var deadline = DateTime.UtcNow.AddMinutes(15);
            while (!File.Exists(Path.Combine(dir, "report.json")) && DateTime.UtcNow < deadline) Program.Pump(500);
            Program.Pump(500);
            return;
        }
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
        var sizes = script == "sizes" ? new[] { (1600, 1000), (1280, 800) } : script == "small" ? new[] { (1044, 768) } : new[] { (1600, 1000) };
        foreach (var (width, height) in sizes)
        {
            w.Width = width; w.Height = height;
            Program.Pump(400);
            foreach (var theme in new[] { ThemeVariant.Light, ThemeVariant.Dark })
            {
                Instances.GuiSettingsUserControlModel.BaseTheme = theme;
                Program.Pump(500);
                var prefix = Path.Combine(output, $"{theme}-{width}x{height}");
                SelectTask(w, script == "small" ? "安排工作" : "开始培育");
                foreach (var page in Pages)
                {
                    LiquidNavigation.Navigate(page);
                    Program.Pump(700);
                    Program.Save(w, $"{prefix}-{page}.png");
                    if (page == LiquidPages.Tasks && w.GetVisualDescendants().OfType<LiquidBoard>().FirstOrDefault(b => b.IsEffectivelyVisible) is { } board)
                    {
                        board.MaximizedId = "task_settings"; Program.Pump(700);
                        foreach (var item in w.GetVisualDescendants().OfType<ListBoxItem>().Where(i => i.IsSelected && i.FindAncestorOfType<ListBox>()?.Classes.Contains("lg-idol-grid") == true).Take(1))
                            foreach (var v in item.GetSelfAndVisualDescendants().OfType<Control>())
                            {
                                var prop = v switch { Border => Border.BackgroundProperty, Panel => Panel.BackgroundProperty, Avalonia.Controls.Presenters.ContentPresenter => Avalonia.Controls.Presenters.ContentPresenter.BackgroundProperty, TemplatedControl => TemplatedControl.BackgroundProperty, _ => null };
                                if (prop == null || v.GetValue(prop) == null) continue;
                                var d = Avalonia.Diagnostics.AvaloniaObjectExtensions.GetDiagnostic(v, prop);
                                Console.WriteLine($"IDOLBG {v.GetType().Name}#{v.Name} bg={v.GetValue(prop)} priority={d.Priority} source={d.Diagnostic}");
                            }
                        Program.Save(w, $"{prefix}-task-settings.png");
                        board.MaximizedId = null; Program.Pump(500);
                    }
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

    /// <summary>
    /// Drives the produce task's idol grid, numeric stepper and item cards through the headless input pipeline and
    /// checks the task option model (index, sub-option, input value, selected cases). Prints PASS/FAIL lines.
    /// </summary>
    private static bool TaskOptionChecks(Window w)
    {
        var ok = true;
        void Check(bool pass, string name) { ok &= pass; Console.WriteLine((pass ? "PASS " : "FAIL ") + name); }
        void Click(Control c)
        {
            var p = c.TranslatePoint(new Avalonia.Point(c.Bounds.Width / 2, c.Bounds.Height / 2), w)!.Value;
            w.MouseDown(p, Avalonia.Input.MouseButton.Left); w.MouseUp(p, Avalonia.Input.MouseButton.Left); Program.Pump(150);
        }
        LiquidNavigation.Navigate(LiquidPages.Tasks);
        ListBox? taskList = null;
        for (var i = 0; i < 40 && (taskList = w.GetVisualDescendants().OfType<ListBox>().FirstOrDefault(l => l.Name == "TaskListBox" && l.IsEffectivelyVisible && l.ItemCount > 0)) == null; i++) Program.Pump(250);
        if (taskList == null) { Check(false, "task list loaded"); return false; }
        SelectTask(w, "开始培育");
        var board = w.GetVisualDescendants().OfType<LiquidBoard>().First(b => b.IsEffectivelyVisible);
        board.MaximizedId = "task_settings"; Program.Pump(700);
        var item = taskList.SelectedItem as DragItemViewModel;
        var options = item?.InterfaceItem?.Option ?? [];
        var groupIds = w.GetVisualDescendants().OfType<Control>().Where(c => c.IsEffectivelyVisible)
            .Select(c => c.Tag as string).Where(t => t?.StartsWith("produce-") == true).ToArray();
        Check(new[] { "produce-scenario", "produce-idol", "produce-mode", "produce-strategies", "produce-items", "produce-automation" }
            .All(groupIds.Contains), "produce settings include every prototype group through the real task panel entry");
        var idolOption = options.FirstOrDefault(o => o.Name?.StartsWith("培育偶像") == true);
        var grid = w.GetVisualDescendants().OfType<ListBox>().FirstOrDefault(l => l.Classes.Contains("lg-idol-grid") && l.IsEffectivelyVisible);
        Check(idolOption != null && grid != null, "produce task shows the idol avatar grid");
        if (idolOption != null && grid != null)
        {
            var target = (idolOption.Index ?? 0) == 1 ? 2 : 1;
            var tile = grid.ContainerFromIndex(target)!;
            tile.BringIntoView(); Program.Pump(300);
            Click(tile);
            var selectedCase = (MFAAvalonia.Extensions.MaaFW.MaaInterface.MaaInterfaceOptionCase)grid.Items[target]!;
            Check(idolOption.Index == target, $"clicking an idol tile selects case {target} ({selectedCase.Name})");
            var expectedSub = selectedCase.Option?.FirstOrDefault();
            Check(expectedSub != null && idolOption.SubOptions?.Any(s => s.Name == expectedSub) == true, $"the selected idol's card option ({expectedSub}) is created below the grid");
        }
        var stepperBox = w.GetVisualDescendants().OfType<TextBox>().FirstOrDefault(t => t.Classes.Contains("lg-step-value") && t.IsEffectivelyVisible);
        var delayOption = options.FirstOrDefault(o => o.Name == "出牌后额外等待");
        Check(stepperBox != null && delayOption != null, "post-play delay uses the stepper");
        if (stepperBox != null && delayOption != null)
        {
            var buttons = ((Panel)stepperBox.Parent!).Children.OfType<Button>().ToList();
            stepperBox.Text = "1.5"; Program.Pump(100);
            Click(buttons[1]);
            Check(stepperBox.Text == "1.6" && delayOption.Data?["post_play_delay"] == "1.6", "+ moves 1.5 to 1.6 and stores it");
            stepperBox.Text = "5"; Program.Pump(100); Click(buttons[1]);
            Check(stepperBox.Text == "5", "+ stops at the upper bound accepted by the input pattern (5)");
            stepperBox.Text = "0"; Program.Pump(100); Click(buttons[0]);
            Check(stepperBox.Text == "0", "- stops at 0");
            stepperBox.Text = "1.5"; Program.Pump(100);
        }
        var itemOption = options.FirstOrDefault(o => o.Name == "使用道具");
        var card = w.GetVisualDescendants().OfType<Avalonia.Controls.Primitives.ToggleButton>().FirstOrDefault(t => t.Classes.Contains("lg-item-card") && t.IsEffectivelyVisible);
        Check(itemOption != null && card != null, "items render as item cards");
        if (itemOption != null && card != null)
        {
            card.BringIntoView(); Program.Pump(300);
            var before = itemOption.SelectedCases?.Count ?? 0;
            Click(card);
            Check(card.IsChecked == true && (itemOption.SelectedCases?.Count ?? 0) == before + 1, "clicking an item card selects it");
            Click(card);
            Check(card.IsChecked == false && (itemOption.SelectedCases?.Count ?? 0) == before, "clicking again clears it");
        }
        board.MaximizedId = null;
        for (var i = 0; i < 12; i++)
        {
            SelectTask(w, "安排工作");
            w.Width = i % 2 == 0 ? 1280 : 1600;
            SelectTask(w, "开始培育");
            Program.Pump(25);
        }
        Check(options.FirstOrDefault(o => o.Name == "出牌后额外等待")?.Data?["post_play_delay"] == "1.5",
            "rebuilding and resizing task forms preserves the configured delay");
        if (item?.InterfaceItem is { } task)
        {
            var view = w.GetVisualDescendants().OfType<TaskQueueView>().First();
            var model = (MFAAvalonia.ViewModels.Pages.TaskQueueViewModel)view.DataContext!;
            var originalAdvanced = task.Advanced;
            var originalOptions = task.Option;
            var definitions = MFAAvalonia.Extensions.MaaFW.MaaProcessor.Interface!.Advanced ??= new();
            const string key = "__ui_regression_advanced";
            definitions[key] = new MFAAvalonia.Extensions.MaaFW.MaaInterfaceAdvancedOption
                { Field = ["回归参数"], Type = ["string"], Default = [new Newtonsoft.Json.Linq.JValue("initial")] };
            var advancedOption = new MFAAvalonia.Extensions.MaaFW.MaaInterface.MaaInterfaceSelectAdvanced { Name = key };
            try
            {
                task.Advanced = [advancedOption];
                view.SetOption(item, true); Program.Pump(100);
                model.IsCommon = false; Program.Pump(100);
                var advancedInput = view.GetVisualDescendants().OfType<AutoCompleteBox>().FirstOrDefault(c => c.IsEffectivelyVisible);
                Check(model.ShowSettings && advancedInput != null, "mixed common/advanced tasks populate the advanced tab");
                if (advancedInput != null) { advancedInput.Text = "changed"; Program.Pump(100); }
                Check(advancedOption.Data.GetValueOrDefault("回归参数") == "changed", "advanced editor writes back to its original option model");
                task.Option = [];
                view.SetOption(item, true); Program.Pump(100);
                Check(!model.ShowSettings && view.GetVisualDescendants().OfType<AutoCompleteBox>().Any(c => c.IsEffectivelyVisible),
                    "advanced-only task keeps its settings accessible without an empty tab");
            }
            finally
            {
                task.Option = originalOptions; task.Advanced = originalAdvanced;
                definitions.Remove(key); view.SetOption(item, true); Program.Pump(100);
            }
        }
        Console.WriteLine(ok ? "TASK_OPTION_CHECKS_PASSED" : "TASK_OPTION_CHECKS_FAILED");
        return ok;
    }

    /// <summary>Frame sequence of the real "new preset" dialog: open, close, rapid reopen (both themes).</summary>
    private static void ModalFrames(Window w, string output)
    {
        var open = typeof(CardRulesView).GetMethod("OpenPresetEditor", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!;
        foreach (var theme in new[] { ThemeVariant.Light, ThemeVariant.Dark })
        {
            Instances.GuiSettingsUserControlModel.BaseTheme = theme;
            LiquidNavigation.Navigate(LiquidPages.CardRules);
            Program.Pump(900);
            var rules = w.GetVisualDescendants().OfType<CardRulesView>().First(v => v.IsEffectivelyVisible);
            int frame = 0;
            void Shot(string tag) => Program.Save(w, Path.Combine(output, $"{theme}-modal-{frame++:00}-{tag}.png"));
            open.Invoke(rules, [true]);
            Program.Pump(16);
            if (theme == ThemeVariant.Light)
                foreach (var v in w.GetVisualDescendants().OfType<Control>())
                {
                    var origin = v.TranslatePoint(new Avalonia.Point(0, 0), w);
                    if (origin == null || !v.IsEffectivelyVisible) continue;
                    var r = new Avalonia.Rect(origin.Value, v.Bounds.Size);
                    if (r.Contains(new Avalonia.Point(657, 35)) && r.Width < 900)
                        Console.WriteLine($"AT {v.GetType().Name}#{v.Name} rect={r} parent={(v.GetVisualParent() as Control)?.GetType().Name}#{(v.GetVisualParent() as Control)?.Name} opacity={v.Opacity}");
                }
            foreach (var ms in new[] { 16, 40, 80, 140, 220, 400 }) { Program.Pump(ms); Shot($"open+{ms}"); }
            if (theme == ThemeVariant.Light)
            {
                var sw = w.GetVisualDescendants().OfType<ToggleSwitch>().FirstOrDefault(t => t.IsEffectivelyVisible && t.Content is string);
                var card = sw?.FindAncestorOfType<GlassPanel>();
                if (sw != null && card != null)
                    foreach (var v in sw.GetSelfAndVisualDescendants().OfType<Control>().Take(6))
                        Console.WriteLine($"SWITCH {v.GetType().Name}#{v.Name} at={v.TranslatePoint(new Avalonia.Point(0, 0), card)} size={v.Bounds.Size} margin={v.Margin}");
                if (sw != null) Console.WriteLine($"SWITCH self bounds={sw.Bounds} rt={sw.RenderTransform?.Value} origin={sw.RenderTransformOrigin} halign={sw.HorizontalAlignment} desired={sw.DesiredSize} parentW={(sw.Parent as Control)?.Bounds.Width}");
                if (sw != null && card != null)
                    foreach (var a in sw.GetVisualAncestors().OfType<Control>().TakeWhile(a => a != card))
                        Console.WriteLine($"SWITCH ancestor {a.GetType().Name}#{a.Name} at={a.TranslatePoint(new Avalonia.Point(0, 0), card)} margin={a.Margin} rt={a.RenderTransform}");
                var tb = w.GetVisualDescendants().OfType<TextBox>().FirstOrDefault(t => t.IsEffectivelyVisible && t.FindAncestorOfType<GlassPanel>() == card);
                if (tb != null) Console.WriteLine($"SWITCH ref TextBox at={tb.TranslatePoint(new Avalonia.Point(0, 0), card)}");
            }
            Avalonia.Headless.HeadlessWindowExtensions.KeyPress(w, Avalonia.Input.Key.Escape, Avalonia.Input.RawInputModifiers.None, Avalonia.Input.PhysicalKey.Escape, null);
            Program.Pump(60); Shot("closing+60");
            open.Invoke(rules, [true]); // reopen while the previous one is still leaving
            foreach (var ms in new[] { 16, 80, 400 }) { Program.Pump(ms); Shot($"reopen+{ms}"); }
            Avalonia.Headless.HeadlessWindowExtensions.KeyPress(w, Avalonia.Input.Key.Escape, Avalonia.Input.RawInputModifiers.None, Avalonia.Input.PhysicalKey.Escape, null);
            Program.Pump(600); Shot("closed");
        }
    }

    /// <summary>Renders isolated buttons at 3x to inspect shadows/clipping against a plain background.</summary>
    private static void ButtonProbe(string output)
    {
        var host = new StackPanel { Orientation = Avalonia.Layout.Orientation.Horizontal, Spacing = 30, Margin = new Avalonia.Thickness(40),
            Background = Avalonia.Media.Brushes.White };
        foreach (var cls in new[] { "lg-primary", "", "lg-icon" })
        {
            var b = new Button { Content = cls == "lg-icon" ? "R" : "开始运行", Height = 38, MinWidth = cls == "lg-icon" ? 0 : 120 };
            if (cls.Length > 0) b.Classes.Add(cls);
            host.Children.Add(b);
        }
        var win = new Window { Width = 520, Height = 160, Content = new Border { Background = Avalonia.Media.Brushes.White, Child = host } };
        win.Show(); Program.Pump(800);
        using var bmp = new Avalonia.Media.Imaging.RenderTargetBitmap(new Avalonia.PixelSize(520 * 3, 160 * 3), new Avalonia.Vector(288, 288));
        bmp.Render(win); bmp.Save(Path.Combine(output, "button-probe.png"));
        foreach (var b in host.Children.OfType<Button>())
        {
            var diag = Avalonia.Diagnostics.AvaloniaObjectExtensions.GetDiagnostic(b, Visual.ClipToBoundsProperty);
            Console.WriteLine($"SOURCE clip priority={diag.Priority} diag={diag.Diagnostic} theme={b.Theme}");
            foreach (var t in new[] { typeof(Visual), typeof(Control), typeof(Avalonia.Controls.Primitives.TemplatedControl), typeof(ContentControl), typeof(Button) })
                Console.WriteLine($"DEFAULT {t.Name} = {Visual.ClipToBoundsProperty.GetDefaultValue(t)}");
            Console.WriteLine($"PROBE {string.Join(',', b.Classes)} bounds={b.Bounds} clip={b.ClipToBounds} parts=" +
                string.Join(" | ", b.GetVisualDescendants().OfType<Avalonia.Controls.Control>().Select(c => $"{c.GetType().Name}#{c.Name} clip={c.ClipToBounds} " +
                    (c is Border bd ? $"bg={bd.Background} cr={bd.CornerRadius} shadow={bd.BoxShadow}" : c is Avalonia.Controls.Presenters.ContentPresenter cp ? $"bg={cp.Background} cr={cp.CornerRadius}" : ""))));
        }
        win.Close();
    }

    private static void SelectTask(Window w, string name)
    {
        var list = w.GetVisualDescendants().OfType<ListBox>().FirstOrDefault(l => l.Name == "TaskListBox" && l.IsEffectivelyVisible);
        if (list?.ItemsSource is not IEnumerable<DragItemViewModel> items) return;
        var item = items.FirstOrDefault(i => i.Name == name || i.InterfaceItem?.Name == name);
        if (item == null) return;
        list.SelectedItem = item;
        item.EnableSetting = true;
        Program.Pump(600);
    }
}
