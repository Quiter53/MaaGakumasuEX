using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.Helper;
using System;
using System.Collections.Generic;
using System.Linq;
using Avalonia;
using Avalonia.Automation;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Input;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Threading;
using MFAAvalonia.ViewModels.Pages;
namespace MFAAvalonia.Views.Pages;

/// <summary>
/// 出牌规则页（原生 Avalonia）：预设条与保存、规则列表、规则详情与曲线、底部手牌出牌模拟。
/// 所有数值都写入 <see cref="CardRulesViewModel"/> 的规则对象，保存走原有配置路径。
/// </summary>
public sealed partial class CardRulesView : UserControl
{
    public CardRulesViewModel Model { get; }

    private readonly Grid _root = new() { RowDefinitions = new RowDefinitions("Auto,*,Auto"), RowSpacing = 14 };
    private readonly SegmentedControl _presets = new();
    private readonly TextBlock _status = new() { Classes = { "lg-sub" }, VerticalAlignment = VerticalAlignment.Center, TextTrimming = TextTrimming.CharacterEllipsis, MaxWidth = 220 };
    private readonly Button _save = new() { Classes = { "lg-primary", "lg-sm" }, MinWidth = 72 };
    private readonly Button _managePreset = new() { Classes = { "lg-icon", "lg-sm" } };
    private readonly TextBox _search = new();
    private readonly StackPanel _list = new() { Spacing = 8 };
    private readonly Dictionary<string, Control> _listItems = new();
    private readonly StackPanel _detail = new() { Spacing = 14 };
    private readonly ScrollViewer _detailScroll;
    private readonly TextBlock _handLabel = new() { FontSize = 12, FontWeight = FontWeight.SemiBold, Classes = { "lg-accent" }, VerticalAlignment = VerticalAlignment.Center };
    private readonly Slider _handSlider = new() { Minimum = 1, Maximum = 12, Width = 150, TickFrequency = 1, IsSnapToTickEnabled = true, SmallChange = 1, LargeChange = 3 };
    private readonly WrapPanel _handCards = new() { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
    private CardRuleCurve? _curve;
    private TextBlock? _curveStatus;
    private Action? _refreshComboWarnings;
    private bool _building;
    private string _searchText = "";

    public CardRulesView() : this(new CardRulesViewModel()) { }
    public CardRulesView(string root) : this(new CardRulesViewModel(root)) { }
    public CardRulesView(CardRulesViewModel model)
    {
        Model = model;
        DataContext = model;
        _detailScroll = new ScrollViewer { Content = _detail, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, Padding = new Thickness(0, 0, 6, 0) };
        Content = _root;
        Model.PropertyChanged += (_, e) => { if (e.PropertyName is nameof(Model.Status) or nameof(Model.Dirty)) UpdateStatus(); };
        ActualThemeVariantChanged += (_, _) => { _curve?.InvalidateVisual(); foreach (var c in Avalonia.VisualTree.VisualExtensions.GetVisualDescendants(this).OfType<CardRuleCurve>()) c.InvalidateVisual(); };
        AttachedToVisualTree += (_, _) => LanguageHelper.LanguageChanged += OnLanguageChanged;
        DetachedFromVisualTree += (_, _) => LanguageHelper.LanguageChanged -= OnLanguageChanged;
        _handSlider.PropertyChanged += (_, e) =>
        {
            if (e.Property != RangeBase.ValueProperty || _building) return;
            Model.PreviewTurn = (int)_handSlider.Value;
            UpdateHand();
            if (_curve != null) { _curve.PreviewTurn = Model.PreviewTurn; _curve.InvalidateVisual(); }
            UpdateCurveStatus();
        };
        AutomationProperties.SetName(_handSlider, ExText.T("模拟剩余回合"));
        _search.TextChanged += (_, _) => { if (_building) return; _searchText = _search.Text ?? ""; RenderList(); };
        _save.Click += (_, _) => SaveRules();
        _presets.SelectionChanged += (_, _) =>
        {
            if (_building || _presets.SelectedItem is not ListBoxItem { Tag: PlayCardPreset p } || p.Id == Model.Document.Active) return;
            Model.SwitchPreset(p.Id);
            _searchText = ""; _search.Text = "";
            RenderList(); RenderDetail(true); UpdateHand(); UpdatePresetActions();
        };
        _managePreset.Click += (_, _) => _managePreset.ContextMenu?.Open(_managePreset);
        Build();
    }

    private void OnLanguageChanged(object? sender, LanguageHelper.LanguageEventArgs e) => Build();

    private static TextBlock Text(string value, double size = 13, bool bold = false, string? cls = null)
    {
        var t = new TextBlock { Text = ExText.T(value), FontSize = size, FontWeight = bold ? FontWeight.SemiBold : FontWeight.Normal, TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center };
        if (cls != null) t.Classes.Add(cls);
        return t;
    }

    private static Button Btn(string label, Action click, params string[] classes)
    {
        var b = new Button { Content = ExText.T(label), VerticalAlignment = VerticalAlignment.Center };
        foreach (var c in classes) b.Classes.Add(c);
        AutomationProperties.SetName(b, ExText.T(label));
        b.Click += (_, _) => click();
        return b;
    }

    private static GlassPanel Box(Control child, Thickness? padding = null) => new() { Classes = { "box" }, Padding = padding ?? new Thickness(14), Content = child };

    private Border Art(CardListCard card, double size, double radius)
    {
        return new Border
        {
            Width = size, Height = size, CornerRadius = new CornerRadius(radius), ClipToBounds = true, BorderThickness = new Thickness(2),
            BorderBrush = CardCatalogFilter.RarityBrush(card.Rarity), VerticalAlignment = VerticalAlignment.Center,
            Child = new Image { Source = Model.Image(card), Stretch = Stretch.UniformToFill },
        };
    }

    private static NumericUpDown Stepper(string name, int value, int min, int max, Action<int> changed)
    {
        var n = new NumericUpDown { Value = value, Minimum = min, Maximum = max, Increment = 1, FormatString = "0", Width = 108, ClipValueToMinMax = true };
        AutomationProperties.SetName(n, ExText.T(name));
        n.ValueChanged += (_, e) => { if (e.NewValue is { } v && decimal.Truncate(v) == v) changed((int)v); };
        return n;
    }

    // ================= Layout =================

    private void Build()
    {
        _building = true;
        _root.Children.Clear();
        _listItems.Clear();
        if (!Model.Loaded)
        {
            var failed = new StackPanel { Spacing = 12, Children = { Text("出牌规则", 18, true), Text(Model.Status, 13, false, "lg-danger"), Btn("重新读取", () => { Model.Load(); Build(); }, "lg-primary") } };
            _root.Children.Add(new GlassPanel { Padding = new Thickness(24), Content = failed });
            _building = false;
            return;
        }

        // Top bar: preset switcher, new preset, save.
        _presets.ItemsSource = null;
        var bar = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,*,Auto"), ColumnSpacing = 12 };
        bar.Children.Add(Text("出牌规则曲线", 14.5, true));
        var presetScroll = new ScrollViewer { Content = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { _presets, Btn("新建预设", OpenNewPreset, "lg-sm"), _managePreset } }, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled };
        Grid.SetColumn(presetScroll, 1); bar.Children.Add(presetScroll);
        _save.Content = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6, Children = { new LiquidIcon { Kind = "save", Size = 13 }, new TextBlock { Text = ExText.T("保存"), VerticalAlignment = VerticalAlignment.Center } } };
        AutomationProperties.SetName(_save, ExText.T("保存"));
        ToolTip.SetTip(_save, ExText.T("保存全部出牌预设；保存后应用于实际出牌，当前选中预设生效"));
        var right = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10, Children = { _status, _save } };
        Grid.SetColumn(right, 2); bar.Children.Add(right);
        _root.Children.Add(new GlassPanel { Padding = new Thickness(18, 10), CornerRadius = new CornerRadius(20), Content = bar });
        AutomationProperties.SetName(_presets, ExText.T("职业预设"));
        _managePreset.Content = new LiquidIcon { Kind = "moreH", Size = 14 };
        ToolTip.SetTip(_managePreset, ExText.T("管理自定义预设"));
        RenderPresets();

        // Workspace: rule list and detail.
        var work = new Grid { ColumnDefinitions = new ColumnDefinitions("340,14,*") };
        _search.Watermark = ExText.T("搜索已配置技能卡…");
        _search.Text = _searchText;
        AutomationProperties.SetName(_search, ExText.T("搜索规则或卡名"));
        var add = new Button { Classes = { "lg-primary", "lg-sm" }, Content = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6, Children = { new LiquidIcon { Kind = "plus", Size = 12 }, new TextBlock { Text = ExText.T("添加规则"), VerticalAlignment = VerticalAlignment.Center } } } };
        AutomationProperties.SetName(add, ExText.T("添加规则"));
        add.Click += (_, _) => OpenPicker();
        var searchRow = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), ColumnSpacing = 8, Children = { _search } };
        Grid.SetColumn(add, 1); searchRow.Children.Add(add);
        var left = new DockPanel { LastChildFill = true };
        DockPanel.SetDock(searchRow, Dock.Top);
        left.Children.Add(searchRow);
        var listScroll = new ScrollViewer { Content = _list, Margin = new Thickness(0, 12, 0, 0), HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, Padding = new Thickness(0, 0, 6, 0) };
        left.Children.Add(listScroll);
        work.Children.Add(new GlassPanel { Padding = new Thickness(16), Content = left, ClipContent = true });
        var detailPanel = new GlassPanel { Padding = new Thickness(18), Content = _detailScroll, ClipContent = true };
        Grid.SetColumn(detailPanel, 2); work.Children.Add(detailPanel);
        Grid.SetRow(work, 1); _root.Children.Add(work);

        // Bottom dock: hand simulation.
        var dock = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,*"), ColumnSpacing = 16 };
        var controls = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10, VerticalAlignment = VerticalAlignment.Center, Children = { Text("手牌出牌模拟", 13, true), _handLabel, _handSlider } };
        dock.Children.Add(controls);
        var cards = new ScrollViewer { Content = _handCards, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled };
        Grid.SetColumn(cards, 1); dock.Children.Add(cards);
        var dockPanel = new GlassPanel { Padding = new Thickness(18, 10), CornerRadius = new CornerRadius(20), Content = dock };
        Grid.SetRow(dockPanel, 2); _root.Children.Add(dockPanel);

        UpdateStatus();
        RenderList();
        RenderDetail(true);
        UpdateHand();
        UpdatePresetActions();
        _building = false;
    }

    private void UpdateStatus()
    {
        _status.Text = ExText.T(Model.Status);
        _status.Classes.Set("lg-warning", Model.Dirty);
    }

    // ================= Presets =================

    private void RenderPresets()
    {
        var old = _building; _building = true;
        var items = Model.Document.Presets.OrderBy(p => p.Builtin ? 0 : 1).Select(p =>
        {
            var item = new ListBoxItem { Content = p.Builtin ? ExText.T(p.Name) : p.Name, Tag = p };
            AutomationProperties.SetName(item, p.ToString());
            return item;
        }).ToList();
        _presets.ItemsSource = items;
        _presets.SelectedItem = items.FirstOrDefault(i => i.Tag is PlayCardPreset p && p.Id == Model.Document.Active);
        _building = old;
    }

    private void UpdatePresetActions()
    {
        var custom = !Model.Preset.Builtin;
        _managePreset.IsVisible = custom;
        if (!custom) { _managePreset.ContextMenu = null; return; }
        var rename = new MenuItem { Header = ExText.T("重命名预设") };
        rename.Click += (_, _) => OpenPresetEditor(false);
        var delete = new MenuItem { Header = ExText.T("删除预设"), Classes = { "lg-danger" } };
        delete.Click += (_, _) => LiquidModal.Confirm(this, "删除预设", $"删除自定义预设“{Model.Preset.Name}”？内置职业预设不受影响。", "删除", () =>
        {
            Model.DeletePreset();
            RenderPresets(); RenderList(); RenderDetail(true); UpdateHand(); UpdatePresetActions();
        }, true);
        _managePreset.ContextMenu = new ContextMenu { ItemsSource = new object[] { rename, delete } };
    }

    private void OpenNewPreset() => OpenPresetEditor(true);

    private void OpenPresetEditor(bool create)
    {
        LiquidModal? modal = null;
        var name = new TextBox { Text = create ? Model.Preset.ToString() + ExText.T(" · 自定义") : Model.Preset.Name, MaxLength = 30 };
        AutomationProperties.SetName(name, ExText.T("预设名称"));
        var careers = new[] { "通用" }.Concat(PlayCardPolicy.Careers).ToArray();
        var career = new ComboBox { ItemsSource = careers.Select(ExText.T).ToArray(), SelectedIndex = Math.Max(0, Array.IndexOf(careers, Model.Preset.Career)), HorizontalAlignment = HorizontalAlignment.Stretch };
        AutomationProperties.SetName(career, ExText.T("职业"));
        var copy = new ToggleSwitch { IsChecked = true, Content = ExText.T("复制当前预设的规则与手牌") };
        var error = Text("", 12, false, "lg-danger");
        var body = new StackPanel { Spacing = 12 };
        if (create) body.Children.Add(Text($"基于当前「{Model.Preset}」创建一套自定义预设：", 12, false, "lg-sub"));
        body.Children.Add(new StackPanel { Spacing = 4, Children = { Text("预设名称", 12, false, "lg-sub"), name } });
        body.Children.Add(new StackPanel { Spacing = 4, Children = { Text("职业", 12, false, "lg-sub"), career } });
        if (create) body.Children.Add(copy);
        body.Children.Add(error);
        var ok = Btn(create ? "创建预设" : "保存名称", () =>
        {
            try
            {
                Model.EditPreset(name.Text ?? "", careers[Math.Max(0, career.SelectedIndex)], create, create && copy.IsChecked == true);
                modal?.Close();
                RenderPresets(); RenderList(); RenderDetail(true); UpdateHand(); UpdatePresetActions(); UpdateStatus();
            }
            catch (Exception ex) { error.Text = ExText.T(ex.Message); }
        }, "lg-primary");
        var cancel = Btn("取消", () => modal?.RequestCancel());
        var footer = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, HorizontalAlignment = HorizontalAlignment.Right, Children = { cancel, ok } };
        modal = LiquidModal.Show(this, LiquidModal.Layout(create ? "新建出牌规则预设" : "重命名预设", null, body, footer, () => modal?.RequestCancel(), false), 440);
    }

    private void SaveRules()
    {
        if (Model.Save()) ToastHelper.Success(ExText.T("出牌规则已保存"), ExText.T("已保存全部预设，当前选中预设生效"));
        else LiquidModal.Notice(this, "保存失败", Model.Status);
        UpdateStatus();
    }

    // ================= Rule list =================

    private string Summary(PlayCardRule r, CardListCard c)
    {
        var tier = PlayCardPolicy.Tiers[Math.Max(0, PlayCardPolicy.EffectiveTier(c.Rarity, r))];
        var extra = PlayCardPolicy.EffectiveExtraAction(c.Extra, r) ? ExText.T("再动") + " · " : "";
        return extra + ExText.T($"{tier}档") + " · " + (r.Limited ? ExText.T($"倒数 {r.Min}~{r.Max} 回合") : ExText.T("全回合"));
    }

    private void RenderList()
    {
        var rules = Model.Preset.Rules.Where(r => r.Card.Contains(_searchText.Trim(), StringComparison.OrdinalIgnoreCase)).ToList();
        foreach (var stale in _listItems.Keys.Except(Model.Preset.Rules.Select(r => r.Id)).ToList()) _listItems.Remove(stale);
        var controls = rules.Select(RuleItem).ToList();
        if (controls.Count == 0)
        {
            _list.Children.Clear();
            _list.Children.Add(new StackPanel
            {
                Spacing = 6, Margin = new Thickness(12, 40), HorizontalAlignment = HorizontalAlignment.Center,
                Children = { Text(Model.Preset.Rules.Count == 0 ? "暂无配置卡牌" : "没有匹配的规则", 13.5, true), Text("点击上方「添加规则」选择技能卡", 12, false, "lg-sub") }
            });
            return;
        }
        if (!_list.Children.SequenceEqual(controls)) { _list.Children.Clear(); _list.Children.AddRange(controls); }
    }

    private Control RuleItem(PlayCardRule r)
    {
        var c = Model.Card(r.Card);
        if (!_listItems.TryGetValue(r.Id, out var item))
        {
            var name = new TextBlock { FontSize = 12.5, FontWeight = FontWeight.Bold, TextTrimming = TextTrimming.CharacterEllipsis, Name = "RuleName" };
            var summary = new TextBlock { FontSize = 11, Classes = { "lg-sub" }, Margin = new Thickness(0, 2, 0, 0), TextWrapping = TextWrapping.Wrap, Name = "RuleSummary" };
            var mini = new CardRuleCurve { Rule = r, Mini = true, Width = 66, Height = 22, IsHitTestVisible = false };
            var toggle = new ToggleSwitch { IsChecked = r.Enabled, VerticalAlignment = VerticalAlignment.Center };
            AutomationProperties.SetName(toggle, ExText.T("启用" + r.Card + "规则"));
            toggle.IsCheckedChanged += (_, _) =>
            {
                if (_building || r.Enabled == (toggle.IsChecked == true)) return;
                r.Enabled = toggle.IsChecked == true; Model.Mark();
                RefreshRule(r);
            };
            var art = Art(c, 42, 11);
            art.Name = "RuleArt";
            var texts = new StackPanel { VerticalAlignment = VerticalAlignment.Center, Children = { name, summary } };
            var grid = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,*,Auto,Auto"), ColumnSpacing = 10, Children = { art } };
            Grid.SetColumn(texts, 1); grid.Children.Add(texts);
            Grid.SetColumn(mini, 2); grid.Children.Add(mini);
            Grid.SetColumn(toggle, 3); grid.Children.Add(toggle);
            var panel = new GlassPanel { Classes = { "box", "interactive" }, Padding = new Thickness(12, 10), Content = grid, Focusable = true, Tag = r };
            AutomationProperties.SetName(panel, ExText.T("编辑" + r.Card + "规则"));
            void Choose() { if (Model.SelectedId == r.Id) return; Model.SelectedId = r.Id; foreach (var i in _listItems.Values) UpdateItemState(i); RenderDetail(true); UpdateHand(); }
            panel.PointerReleased += (_, e) => { if (e.InitialPressMouseButton == MouseButton.Left && !toggle.IsPointerOver) Choose(); };
            panel.KeyDown += (_, e) => { if (e.Key is Key.Enter or Key.Space) { Choose(); e.Handled = true; } };
            _listItems[r.Id] = item = panel;
        }
        UpdateItem(item, r);
        return item;
    }

    private void UpdateItem(Control item, PlayCardRule r)
    {
        if (item is not GlassPanel { Content: Grid grid }) return;
        var c = Model.Card(r.Card);
        var texts = grid.Children.OfType<StackPanel>().First();
        ((TextBlock)texts.Children[0]).Text = r.Card;
        ((TextBlock)texts.Children[1]).Text = Summary(r, c);
        if (grid.Children[0] is Border art && art.Child is Image img && art.Tag as string != r.Card)
        {
            art.Tag = r.Card; img.Source = Model.Image(c); art.BorderBrush = CardCatalogFilter.RarityBrush(c.Rarity);
        }
        var mini = grid.Children.OfType<CardRuleCurve>().First();
        mini.Rule = r; mini.InvalidateVisual();
        var toggle = grid.Children.OfType<ToggleSwitch>().First();
        if (toggle.IsChecked != r.Enabled) { var old = _building; _building = true; toggle.IsChecked = r.Enabled; _building = old; }
        UpdateItemState(item);
    }

    private void UpdateItemState(Control item)
    {
        if (item is not GlassPanel { Tag: PlayCardRule r } panel) return;
        panel.Classes.Set("selected", r.Id == Model.SelectedId);
        panel.Opacity = r.Enabled ? 1 : 0.55;
    }

    /// <summary>Refreshes everything that depends on one rule's values without rebuilding controls.</summary>
    private void RefreshRule(PlayCardRule r)
    {
        if (_listItems.TryGetValue(r.Id, out var item)) UpdateItem(item, r);
        _curve?.InvalidateVisual();
        UpdateCurveStatus();
        _refreshComboWarnings?.Invoke();
        UpdateHand();
    }
}
