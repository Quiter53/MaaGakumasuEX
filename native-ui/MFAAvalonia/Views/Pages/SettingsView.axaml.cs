using Avalonia.Controls;
using Avalonia.Media;
using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.Helper;
using MFAAvalonia.Views.UserControls.Settings;
using Microsoft.Extensions.DependencyInjection;
using System;
using System.Collections.Generic;
using System.Linq;

namespace MFAAvalonia.Views.Pages;

/// <summary>
/// Preferences: grouped section list on the left, the existing settings controls on the right.
/// Each settings control is created once and reused, so values, focus and bindings persist.
/// </summary>
public partial class SettingsView : UserControl
{
    private sealed record Section(string Key, string Title, string Description, string Color, Type View);

    private static readonly Section[] Sections =
    [
        new("gui", "外观与液态玻璃", "深浅主题、背景光晕、动态效果、界面语言与字体", "#007AFF", typeof(GuiSettingsUserControl)),
        new("config", "配置方案管理", "独立保存多套日常与培育任务参数", "#5856D6", typeof(ConfigurationMgrUserControl)),
        new("timer", "定时调度任务", "按时段自动唤醒并执行指定配置", "#FF9500", typeof(TimerSettingsUserControl)),
        new("perf", "GPU 与推理性能", "显卡加速与运行性能选项", "#34C759", typeof(PerformanceUserControl)),
        new("game", "运行与识别诊断", "调试截图、识别绘制与运行选项", "#AF52DE", typeof(GameSettingsUserControl)),
        new("connect", "模拟器与桌面连接", "ADB 截图/触控模式与 DMM 窗口连接", "#00C7BE", typeof(ConnectSettingsUserControl)),
        new("start", "前后置启动联动", "任务前启动模拟器或游戏，完成后执行动作", "#FF2D55", typeof(StartSettingsUserControl)),
        new("notify", "外部通知推送", "任务完成或出错时推送到外部服务", "#FF3B30", typeof(ExternalNotificationSettingsUserControl)),
        new("hotkey", "全局快捷键", "显示或隐藏窗口、启动或停止任务", "#64748B", typeof(HotKeySettingsUserControl)),
        new("update", "软件与资源更新", "检查版本、更新渠道与自动更新", "#0284C7", typeof(VersionUpdateSettingsUserControl)),
        new("about", "关于 MaaGakumasu", "版本信息、开源协议与使用须知", "#4F46E5", typeof(AboutUserControl)),
    ];

    private readonly Dictionary<string, Control> _views = new();
    private readonly Dictionary<string, Button> _buttons = new();
    private string _current = "";

    public SettingsView()
    {
        DataContext = Instances.SettingsViewModel;
        InitializeComponent();
        foreach (var section in Sections)
        {
            var title = new TextBlock { VerticalAlignment = Avalonia.Layout.VerticalAlignment.Center, TextTrimming = TextTrimming.CharacterEllipsis };
            title.Bind(TextBlock.TextProperty, (Avalonia.Data.IBinding)new TExtension(section.Title).ProvideValue(null!));
            var row = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,*,Auto"), ColumnSpacing = 10 };
            row.Children.Add(new Border { Classes = { "section-dot" }, Background = Brush.Parse(section.Color), VerticalAlignment = Avalonia.Layout.VerticalAlignment.Center });
            Grid.SetColumn(title, 1); row.Children.Add(title);
            var chevron = new LiquidIcon { Kind = "chevronRight", Size = 12 };
            chevron.Bind(LiquidIcon.ForegroundProperty, chevron.GetResourceObservable("LgTextMutedBrush"));
            Grid.SetColumn(chevron, 2); row.Children.Add(chevron);
            var button = new Button { Classes = { "lg-section" }, Content = row };
            Avalonia.Automation.AutomationProperties.SetName(button, section.Title);
            button.Click += (_, _) => SelectSection(section.Key);
            SettingsLayout.Children.Add(button);
            _buttons[section.Key] = button;
        }
        LanguageHelper.LanguageChanged += (_, _) => UpdateHeader();
        SelectSection("gui");
    }

    public void SelectSection(string key)
    {
        var section = Sections.FirstOrDefault(s => s.Key == key) ?? Sections[0];
        if (!_views.TryGetValue(section.Key, out var view))
        {
            view = (Control)App.Services.GetRequiredService(section.View);
            _views[section.Key] = view;
            SectionHost.Children.Add(view);
        }
        foreach (var (k, v) in _views) v.IsVisible = k == section.Key;
        foreach (var (k, b) in _buttons) b.Classes.Set("active", k == section.Key);
        if (_current != section.Key)
        {
            _current = section.Key;
            Settings.Offset = default;
            LiquidStage.PlayEnter(view);
        }
        UpdateHeader();
    }

    /// <summary>The control currently shown, used by navigation helpers and the tutorial.</summary>
    public Control? CurrentSectionView => _views.TryGetValue(_current, out var v) ? v : null;

    private void UpdateHeader()
    {
        var section = Sections.FirstOrDefault(s => s.Key == _current) ?? Sections[0];
        SectionTitle.Text = ExText.T(section.Title);
        SectionDesc.Text = ExText.T(section.Description);
        SectionColor.Background = Brush.Parse(section.Color);
    }
}
