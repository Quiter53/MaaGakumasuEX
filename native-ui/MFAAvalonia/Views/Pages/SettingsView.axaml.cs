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
        new("gui", "界面设置", "主题、强调色、背景光晕、动态效果、语言与字体", "#007AFF", typeof(GuiSettingsUserControl)),
        new("config", "配置管理", "新建、切换与删除任务配置", "#5856D6", typeof(ConfigurationMgrUserControl)),
        new("timer", "定时任务", "在设定时间自动启动指定配置的任务", "#FF9500", typeof(TimerSettingsUserControl)),
        new("perf", "性能设置", "GPU 推理加速与防止系统休眠", "#34C759", typeof(PerformanceUserControl)),
        new("game", "运行设置", "出错后继续、调试截图与实时画面、前后置脚本", "#AF52DE", typeof(GameSettingsUserControl)),
        new("connect", "连接设置", "截图与输入方式、ADB 连接与断线重连", "#00C7BE", typeof(ConnectSettingsUserControl)),
        new("start", "启动设置", "启动模拟器或游戏、等待时间与任务结束后的动作", "#FF2D55", typeof(StartSettingsUserControl)),
        new("notify", "通知设置", "任务完成或出错时推送到钉钉、邮件、Webhook 等", "#FF3B30", typeof(ExternalNotificationSettingsUserControl)),
        new("hotkey", "快捷键", "显示窗口与启动任务的全局快捷键", "#64748B", typeof(HotKeySettingsUserControl)),
        new("update", "版本更新", "更新渠道（稳定版/测试版）与检查更新", "#0284C7", typeof(VersionUpdateSettingsUserControl)),
        new("about", "关于", "版本、公告、日志导出与开源信息", "#4F46E5", typeof(AboutUserControl)),
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
