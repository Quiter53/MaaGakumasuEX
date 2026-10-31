using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Presenters;
using Avalonia.Controls.Primitives;
using Avalonia.Markup.Xaml;
using Avalonia.Styling;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Configuration;
using MFAAvalonia.Helper;
using MFAAvalonia.Views.UserControls;
using MFAAvalonia.Views.UserControls.Settings;
using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.Views.Pages;
using Microsoft.Extensions.DependencyInjection;
using Avalonia.Input;
using Avalonia.Interactivity;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using SukiUI.Controls;

namespace MFAAvalonia.Views.Mobile;

public partial class RootViewContent : UserControl, LiquidNavigation.IHost
{
    public RootViewContent()
    {
        InitializeComponent();
        BuildShell();
        HookWindowChrome();
        Loaded += OnLoaded;
    }

    private void OnLoaded(object? sender, Avalonia.Interactivity.RoutedEventArgs e)
    {
        Loaded -= OnLoaded;

        if (!AppRuntime.IsNewInstance) return;

        var completed = ConfigurationManager.Current.GetValue(
            ConfigurationKeys.HasCompletedFirstUseTutorial, false);
        if (completed) return;

        DispatcherHelper.RunOnMainThread(async () =>
        {
            await WaitForControlReady("TopToolbar", maxRetries: 30, delayMs: 200);
            TryStartTutorial();
        });
    }

    private async Task WaitForControlReady(string controlName, int maxRetries, int delayMs)
    {
        for (int i = 0; i < maxRetries; i++)
        {
            await Task.Delay(delayMs);
            var control = FindDescendantByName<Control>(this, controlName);
            if (control != null && control.Bounds.Width > 1 && control.Bounds.Height > 1)
                return;
        }
    }

    #region Navigation

    private sealed record NavEntry(string Key, string Label, string Icon, Type View);

    private static readonly NavEntry[] NavEntries =
    [
        new(LiquidPages.Tasks, "任务工作台", "tasks", typeof(InstanceContainerView)),
        new(LiquidPages.CardRules, "出牌规则", "rules", typeof(CardRulesView)),
        new(LiquidPages.Drinks, "饮料策略", "drink", typeof(DrinkSettingsView)),
        new(LiquidPages.CardList, "卡牌名单", "cardList", typeof(CardListSettingsView)),
        new(LiquidPages.Monitor, "多开监控", "monitor", typeof(MonitorView)),
        new(LiquidPages.Screenshot, "画面捕获", "camera", typeof(ScreenshotView)),
    ];

    private readonly Dictionary<string, Control> _pages = new();
    private readonly Dictionary<string, Button> _navButtons = new();
    private string _currentPage = "";
    private bool _sideExpanded = true;

    public string CurrentPage => _currentPage;

    private void BuildShell()
    {
        var list = this.FindControl<StackPanel>("NavList")!;
        foreach (var entry in NavEntries)
        {
            var label = new TextBlock { Classes = { "nav-label" }, VerticalAlignment = Avalonia.Layout.VerticalAlignment.Center };
            label.Bind(TextBlock.TextProperty, new TExtension(entry.Label).ProvideValue(null!) as Avalonia.Data.IBinding ?? throw new InvalidOperationException());
            var button = new Button
            {
                Classes = { "lg-nav" },
                Tag = entry.Key,
                Content = new StackPanel
                {
                    Orientation = Avalonia.Layout.Orientation.Horizontal,
                    Spacing = 11,
                    Children = { new LiquidIcon { Kind = entry.Icon, Size = 16 }, label }
                }
            };
            Avalonia.Automation.AutomationProperties.SetName(button, entry.Label);
            ToolTip.SetTip(button, label.Text);
            button.Click += (_, _) => Navigate(entry.Key);
            list.Children.Add(button);
            _navButtons[entry.Key] = button;
        }
        var settings = this.FindControl<Button>("SettingsNav")!;
        settings.Click += (_, _) => Navigate(LiquidPages.Settings);
        _navButtons[LiquidPages.Settings] = settings;

        this.FindControl<Button>("SideToggle")!.Click += (_, _) => SetSideExpanded(!_sideExpanded);
        this.FindControl<Button>("ThemeToggle")!.Click += (_, _) =>
            Instances.GuiSettingsUserControlModel.BaseTheme =
                ActualThemeVariant == ThemeVariant.Dark ? ThemeVariant.Light : ThemeVariant.Dark;
        ActualThemeVariantChanged += (_, _) => UpdateThemeToggle();
        LanguageHelper.LanguageChanged += (_, _) => UpdateThemeToggle();
        UpdateThemeToggle();

        _sideExpanded = ConfigurationManager.Current.GetValue(ConfigurationKeys.LiquidSidebarExpanded, true);
        SetSideExpanded(_sideExpanded, false);
        var center = this.FindControl<Panel>("TitleCenter")!;
        center.SizeChanged += (_, e) =>
        {
            if (this.FindControl<InstanceTabBar>("TitleTabs")?.FindControl<MFAAvalonia.Controls.InstanceTabsControl>("TabsControl") is { } tabs)
                tabs.LayoutWidthHint = Math.Max(0, e.NewSize.Width - 40);
        };
        LiquidNavigation.Register(this);
        Navigate(LiquidPages.Tasks);
    }

    private void UpdateThemeToggle()
    {
        var dark = ActualThemeVariant == ThemeVariant.Dark;
        this.FindControl<LiquidIcon>("ThemeIcon")!.Kind = dark ? "themeAdaptive" : "sunAdaptive";
        var text = ExText.T(dark ? "深空暗夜" : "冰川白昼");
        this.FindControl<TextBlock>("ThemeLabel")!.Text = text;
        var toggle = this.FindControl<Button>("ThemeToggle")!;
        ToolTip.SetTip(toggle, ExText.T(dark ? "当前深色 · 点击切换浅色" : "当前浅色 · 点击切换深色"));
    }

    private void SetSideExpanded(bool expanded, bool persist = true)
    {
        _sideExpanded = expanded;
        var side = this.FindControl<GlassPanel>("SideBar")!;
        side.Width = expanded ? 186 : 64;
        this.FindControl<TextBlock>("SideHeaderText")!.IsVisible = expanded;
        var header = this.FindControl<Grid>("SideHeader")!;
        header.ColumnDefinitions = new ColumnDefinitions(expanded ? "*,Auto" : "*");
        Grid.SetColumn(this.FindControl<Button>("SideToggle")!, expanded ? 1 : 0);
        this.FindControl<Button>("SideToggle")!.HorizontalAlignment = expanded ? Avalonia.Layout.HorizontalAlignment.Right : Avalonia.Layout.HorizontalAlignment.Center;
        ToolTip.SetTip(this.FindControl<Button>("SideToggle")!, ExText.T(expanded ? "收起侧边栏" : "展开侧边栏"));
        foreach (var b in _navButtons.Values) b.Classes.Set("collapsed", !expanded);
        this.FindControl<Button>("ThemeToggle")!.Classes.Set("collapsed", !expanded);
        if (persist) ConfigurationManager.Current.SetValue(ConfigurationKeys.LiquidSidebarExpanded, expanded);
    }

    public void Navigate(string page)
    {
        if (_currentPage == page) return;
        var entry = NavEntries.FirstOrDefault(e => e.Key == page);
        var viewType = page == LiquidPages.Settings ? typeof(SettingsView) : entry?.View;
        if (viewType == null) return;
        var host = this.FindControl<Panel>("PageHost")!;
        if (!_pages.TryGetValue(page, out var view))
        {
            // Pages are created once and kept alive so switching never rebuilds business controls.
            view = (Control)App.Services.GetRequiredService(viewType);
            _pages[page] = view;
            host.Children.Add(view);
        }
        foreach (var (key, control) in _pages) control.IsVisible = key == page;
        _currentPage = page;
        LiquidStage.PlayEnter(view);

        foreach (var (key, button) in _navButtons) button.Classes.Set("active", key == page);
        var index = Array.FindIndex(NavEntries, e => e.Key == page);
        var pill = this.FindControl<Border>("NavPill")!;
        pill.Opacity = index >= 0 ? 1 : 0;
        if (index >= 0) pill.RenderTransform = Avalonia.Media.Transformation.TransformOperations.Parse($"translateY({index * 44}px)");
        this.FindControl<Panel>("TitleCenter")!.Classes.Set("hidden-bar", page != LiquidPages.Tasks);
        LiquidNavigation.RaisePageChanged(page);
    }

    public void NavigateSettings(string? section)
    {
        Navigate(LiquidPages.Settings);
        if (section != null && _pages.TryGetValue(LiquidPages.Settings, out var view) && view is SettingsView settings)
            settings.SelectSection(section);
    }

    private void NavigateToSettings() => Navigate(LiquidPages.Settings);

    private void NavigateToHome() => Navigate(LiquidPages.Tasks);

    #endregion

    #region Window chrome

    private Window? HostWindow => TopLevel.GetTopLevel(this) as Window;

    private void HookWindowChrome()
    {
        var drag = this.FindControl<Grid>("TitleDragArea")!;
        drag.PointerPressed += (_, e) =>
        {
            if (HostWindow is not { } w || e.Source is Visual v && v.FindAncestorOfType<Button>(true) != null) return;
            if (!e.GetCurrentPoint(drag).Properties.IsLeftButtonPressed) return;
            if (e.ClickCount == 2) { ToggleMaximize(); e.Handled = true; return; }
            w.BeginMoveDrag(e);
        };
        this.FindControl<Button>("MinimizeButton")!.Click += (_, _) => { if (HostWindow is { } w) w.WindowState = WindowState.Minimized; };
        this.FindControl<Button>("MaximizeButton")!.Click += (_, _) => ToggleMaximize();
        this.FindControl<Button>("CloseButton")!.Click += (_, _) => HostWindow?.Close();
        var pin = this.FindControl<ToggleButton>("PinButton")!;
        pin.IsCheckedChanged += (_, _) => { if (HostWindow is { } w && w.Topmost != (pin.IsChecked == true)) w.Topmost = pin.IsChecked == true; };
    }

    private void OnWindowPropertyChanged(object? sender, AvaloniaPropertyChangedEventArgs e)
    {
        if (sender is not Window w) return;
        if (e.Property == Window.WindowStateProperty)
            this.FindControl<LiquidIcon>("MaximizeIcon")!.Kind = w.WindowState == WindowState.Maximized ? "layers" : "winMax";
        else if (e.Property == Window.TopmostProperty)
            this.FindControl<ToggleButton>("PinButton")!.IsChecked = w.Topmost;
    }

    private void ToggleMaximize()
    {
        if (HostWindow is not { } w) return;
        w.WindowState = w.WindowState == WindowState.Maximized ? WindowState.Normal : WindowState.Maximized;
    }

    protected override void OnAttachedToVisualTree(VisualTreeAttachmentEventArgs e)
    {
        base.OnAttachedToVisualTree(e);
        if (HostWindow is { } w)
        {
            w.PropertyChanged -= OnWindowPropertyChanged;
            w.PropertyChanged += OnWindowPropertyChanged;
            this.FindControl<ToggleButton>("PinButton")!.IsChecked = w.Topmost;
        }
    }

    protected override void OnDetachedFromVisualTree(VisualTreeAttachmentEventArgs e)
    {
        base.OnDetachedFromVisualTree(e);
        if (HostWindow is { } w) w.PropertyChanged -= OnWindowPropertyChanged;
    }

    #endregion

    #region Control Finders

    private Control? FindTaskListHeaderButton(int index)
    {
        var panel = FindDescendantByName<StackPanel>(this, "TaskListHeaderButtons");
        if (panel == null) return null;
        var buttons = panel.Children.OfType<Button>().ToList();
        return index < buttons.Count ? buttons[index] : null;
    }

    private Control? FindLogExportButton()
    {
        var logCard = FindDescendantByName<Control>(this, "LogCard");
        if (logCard == null) return null;
        // The export button is the first Button inside LogCard's HeaderActions StackPanel
        var headerPanels = logCard.GetVisualDescendants().OfType<StackPanel>()
            .Where(sp => sp.Parent is ContentPresenter || sp.Classes.Contains("HeaderActions"))
            .ToList();
        foreach (var panel in logCard.GetVisualDescendants().OfType<StackPanel>())
        {
            var buttons = panel.Children.OfType<Button>().ToList();
            if (buttons.Count >= 2)
            {
                // Found the header actions panel with export + clear buttons
                return buttons[0]; // Export button
            }
        }
        return null;
    }

    private Control? FindStartupSettingsControl()
    {
        return FindDescendantOfType<StartSettingsUserControl>(this);
    }

    private Control? FindBeforeAfterTaskControl()
    {
        var startSettings = FindDescendantOfType<StartSettingsUserControl>(this);
        if (startSettings == null) return null;
        // The first GlassCard contains BeforeTask and AfterTask ComboBoxes
        return startSettings.GetVisualDescendants().OfType<GlassCard>().FirstOrDefault();
    }

    private Control? FindSoftwarePathControl()
    {
        var startSettings = FindDescendantOfType<StartSettingsUserControl>(this);
        if (startSettings == null) return null;
        // Find the GlassCard containing SoftwarePath by looking for the TextBox bound to it
        foreach (var card in startSettings.GetVisualDescendants().OfType<GlassCard>())
        {
            var hasPath = card.GetVisualDescendants().OfType<TextBox>()
                .Any(tb => tb.GetValue(TextBox.TextProperty) != null || tb.Width is 215); // SoftwarePath TextBox has Width=215 inside this card
            var hasNumeric = card.GetVisualDescendants().OfType<NumericUpDown>().Any();
            if (hasPath && hasNumeric)
                return card;
        }
        return startSettings;
    }

    private Control? FindFirstTaskCheckBox()
    {
        var taskListBox = FindDescendantByName<ListBox>(this, "TaskListBox");
        if (taskListBox == null) return null;
        // Find the first visible CheckBox inside the TaskListBox
        return taskListBox.GetVisualDescendants()
            .OfType<CheckBox>()
            .FirstOrDefault(cb => cb.IsVisible && cb.Bounds.Width > 1);
    }

    private Control? FindFirstTaskListBoxItem()
    {
        var taskListBox = FindDescendantByName<ListBox>(this, "TaskListBox");
        if (taskListBox == null) return null;
        // Find the first visible ListBoxItem
        return taskListBox.GetVisualDescendants()
            .OfType<ListBoxItem>()
            .FirstOrDefault(item => item.IsVisible && item.Bounds.Width > 1);
    }

    #endregion

    public void TryStartTutorial()
    {
        try
        {
            var overlay = this.FindControl<TeachingTipOverlay>("TutorialOverlay");
            if (overlay == null) return;

            // Always start from the home page
            NavigateToHome();

            var root = this;

            var steps = new List<TutorialStep>();

            // === Home Page Steps ===

            // Step 1: Toolbar overview
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepConnectTitle,
                DescriptionKey = LangKeys.TutorialStepConnectDesc,
                FindTarget = () => FindDescendantByName<Control>(root, "TopToolbar"),
                PreferredPlacement = TipPlacement.Bottom,
                CutoutPadding = new Thickness(12, 8, 12, 8)
            });

            // Step 2: Resource selector (adaptive: wide/compact)
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepResourceTitle,
                DescriptionKey = LangKeys.TutorialStepResourceDesc,
                FindTarget = () => FindAdaptiveControl("ResourceSelectorPanel", "ResourceSelectorPanelCompact"),
                PreferredPlacement = TipPlacement.Bottom
            });

            // Step 3: Controller type (adaptive: wide/compact)
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepControllerTitle,
                DescriptionKey = LangKeys.TutorialStepControllerDesc,
                FindTarget = () => FindAdaptiveControl("ControllerSelectorPanel", "ControllerSelectorPanelCompact"),
                PreferredPlacement = TipPlacement.Bottom
            });

            // Step 4: Device selection (adaptive: wide/compact)
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepDeviceTitle,
                DescriptionKey = LangKeys.TutorialStepDeviceDesc,
                FindTarget = () => FindAdaptiveControl("DeviceSelectorPanel", "DeviceSelectorPanelCompact"),
                PreferredPlacement = TipPlacement.Bottom
            });

            // Step 5: Connection buttons (adaptive: wide/compact)
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepConnectionTitle,
                DescriptionKey = LangKeys.TutorialStepConnectionDesc,
                FindTarget = () => FindAdaptiveControl("ConnectionButtonsPanel", "ConnectionButtonsPanelCompact"),
                PreferredPlacement = TipPlacement.Bottom
            });

            // Step 6: Task list card overview
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepAddTaskTitle,
                DescriptionKey = LangKeys.TutorialStepAddTaskDesc,
                FindTarget = () => FindDescendantByName<Control>(root, "TaskListCard"),
                PreferredPlacement = TipPlacement.Top
            });

            // Step 7: SelectAll / DeselectAll buttons
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepSelectAllTitle,
                DescriptionKey = LangKeys.TutorialStepSelectAllDesc,
                FindTarget = () => FindTaskListHeaderButton(0),
                PreferredPlacement = TipPlacement.Bottom,
                CutoutPadding = new Thickness(4, 4, 50, 4) // extend right to cover DeselectAll
            });

            // Step 8: AddTask / Reset buttons
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepAddTaskBtnTitle,
                DescriptionKey = LangKeys.TutorialStepAddTaskBtnDesc,
                FindTarget = () => FindTaskListHeaderButton(2),
                PreferredPlacement = TipPlacement.Bottom,
                CutoutPadding = new Thickness(4, 4, 50, 4) // extend right to cover Reset
            });

            // Step 9: Start task button
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepResetTaskTitle,
                DescriptionKey = LangKeys.TutorialStepResetTaskDesc,
                FindTarget = () => FindAdaptiveControl("TaskToggleBorder", "TaskToggleBorder1"),
                PreferredPlacement = TipPlacement.Bottom,
                CutoutPadding = new Thickness(4)
            });

            // Step 10: Checkbox interaction tips (target the first checkbox)
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepTaskInteractTitle,
                DescriptionKey = LangKeys.TutorialStepTaskInteractDesc,
                FindTarget = () => FindFirstTaskCheckBox(),
                PreferredPlacement = TipPlacement.Right,
                CutoutPadding = new Thickness(4)
            });

            // Step 11: Task item right-click tips (target the first ListBoxItem)
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepTaskItemTipTitle,
                DescriptionKey = LangKeys.TutorialStepTaskItemTipDesc,
                FindTarget = () => FindFirstTaskListBoxItem(),
                PreferredPlacement = TipPlacement.Right,
                CutoutPadding = new Thickness(4)
            });

            // Step 12: Task settings card
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepTaskOptionsTitle,
                DescriptionKey = LangKeys.TutorialStepTaskOptionsDesc,
                FindTarget = () => FindDescendantByName<Control>(root, "SettingCard"),
                PreferredPlacement = TipPlacement.Top
            });

            // Step 13: Log card
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepLogTitle,
                DescriptionKey = LangKeys.TutorialStepLogDesc,
                FindTarget = () => FindDescendantByName<Control>(root, "LogCard"),
                PreferredPlacement = TipPlacement.Top
            });

            // Step 14: Export log button
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepExportLogTitle,
                DescriptionKey = LangKeys.TutorialStepExportLogDesc,
                FindTarget = () => FindLogExportButton(),
                PreferredPlacement = TipPlacement.Bottom,
                CutoutPadding = new Thickness(6)
            });

            // Step 15: Instance tab bar (multi-config)
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepMultiConfigTitle,
                DescriptionKey = LangKeys.TutorialStepMultiConfigDesc,
                FindTarget = () => FindDescendantOfType<InstanceTabBar>(root),
                PreferredPlacement = TipPlacement.Bottom
            });

            // Step 16: Settings sidebar icon
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepSettingsTitle,
                DescriptionKey = LangKeys.TutorialStepSettingsDesc,
                FindTarget = () =>
                {
                    var sideMenu = root.GetVisualDescendants()
                        .OfType<SukiSideMenu>()
                        .FirstOrDefault();
                    return sideMenu?.FooterMenuItems?.OfType<SukiSideMenuItem>().FirstOrDefault();
                },
                PreferredPlacement = TipPlacement.Right
            });

            // Step 17: Settings page overview (navigate to settings)
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepSettingsPageTitle,
                DescriptionKey = LangKeys.TutorialStepSettingsPageDesc,
                FindTarget = () => FindDescendantByName<Control>(root, "SettingsLayout"),
                PreferredPlacement = TipPlacement.Left,
                OnEnter = () => NavigateToSettings(),
                OnLeave = () => NavigateToHome()
            });

            // Step 18: Startup settings section
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepStartupSettingsTitle,
                DescriptionKey = LangKeys.TutorialStepStartupSettingsDesc,
                FindTarget = () => FindStartupSettingsControl(),
                PreferredPlacement = TipPlacement.Left,
                OnEnter = () => NavigateToSettings(),
                OnLeave = () => NavigateToHome()
            });

            // Step 19: Before/After task actions (first GlassCard)
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepBeforeAfterTaskTitle,
                DescriptionKey = LangKeys.TutorialStepBeforeAfterTaskDesc,
                FindTarget = () => FindBeforeAfterTaskControl(),
                PreferredPlacement = TipPlacement.Left,
                OnEnter = () => NavigateToSettings(),
                OnLeave = () => NavigateToHome()
            });

            // Step 20: Software path suggestion
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepSoftwarePathTitle,
                DescriptionKey = LangKeys.TutorialStepSoftwarePathDesc,
                FindTarget = () => FindSoftwarePathControl(),
                PreferredPlacement = TipPlacement.Left,
                OnEnter = () => NavigateToSettings(),
                OnLeave = () => NavigateToHome()
            });

            // Step 21: Re-watch tutorial hint (navigate to settings → about)
            steps.Add(new TutorialStep
            {
                TitleKey = LangKeys.TutorialStepRewatchTitle,
                DescriptionKey = LangKeys.TutorialStepRewatchDesc,
                FindTarget = () =>
                {
                    var card = FindDescendantByName<Control>(root, "TutorialCard");
                    card?.BringIntoView();
                    return card;
                },
                PreferredPlacement = TipPlacement.Top,
                OnEnter = () => NavigateToSettings(),
                OnLeave = () => NavigateToHome()
            });

            overlay.StartTutorial(steps);
        }
        catch (Exception ex)
        {
            LoggerHelper.Error($"启动新手引导失败：原因={ex.Message}", ex);
        }
    }

    /// <summary>
    /// Finds a visible control by name, trying the compact variant if the primary one is hidden.
    /// This handles the wide/compact responsive layout in TaskQueueView.
    /// </summary>
    private Control? FindAdaptiveControl(string wideName, string compactName)
    {
        // Use IsEffectivelyVisible to check the entire visual tree (including parent visibility)
        // IsVisible only checks the control's own property, not ancestors
        var wide = FindDescendantByName<Control>(this, wideName);
        if (wide != null && wide.IsEffectivelyVisible && wide.Bounds.Width > 1)
            return wide;

        var compact = FindDescendantByName<Control>(this, compactName);
        if (compact != null && compact.IsEffectivelyVisible && compact.Bounds.Width > 1)
            return compact;

        // Fallback: return whichever exists
        return compact ?? wide;
    }

    private static T? FindDescendantByName<T>(Control root, string name) where T : Control
    {
        foreach (var child in root.GetVisualDescendants())
        {
            if (child is T control && control.Name == name)
                return control;
        }
        return null;
    }

    private static T? FindDescendantOfType<T>(Control root) where T : Control
    {
        return root.GetVisualDescendants().OfType<T>().FirstOrDefault();
    }
}
