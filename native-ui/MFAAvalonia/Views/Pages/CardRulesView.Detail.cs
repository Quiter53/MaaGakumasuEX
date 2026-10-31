using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.Helper;
using System;
using System.Linq;
using Avalonia;
using Avalonia.Automation;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Media;
using MFAAvalonia.ViewModels.Pages;
namespace MFAAvalonia.Views.Pages;

public sealed partial class CardRulesView
{
    // ================= Rule detail =================

    private void RenderDetail(bool animate)
    {
        var old = _building; _building = true;
        _detail.Children.Clear();
        _refreshComboWarnings = null;
        _curve = null;
        if (Model.Selected is not { } r)
        {
            _detail.Children.Add(new StackPanel
            {
                Spacing = 10, Margin = new Thickness(0, 80), HorizontalAlignment = HorizontalAlignment.Center,
                Children = { Text("请在左侧选择或新建一条卡牌规则", 13.5, true), Btn("添加规则", () => OpenPicker(), "lg-primary") }
            });
            _building = old;
            return;
        }
        var c = Model.Card(r.Card);

        // Header
        var badges = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, VerticalAlignment = VerticalAlignment.Center };
        badges.Children.Add(new TextBlock { Text = r.Card, FontSize = 16, FontWeight = FontWeight.Bold, VerticalAlignment = VerticalAlignment.Center });
        badges.Children.Add(new Border { Classes = { "lg-badge", "neutral" }, Child = new TextBlock { Text = ExText.T("原生") + " " + CardCatalogFilter.Describe(c) } });
        var extraBadge = new Border { Classes = { "lg-badge", "success" }, Child = new TextBlock() };
        badges.Children.Add(extraBadge);
        void UpdateExtraBadge()
        {
            extraBadge.IsVisible = PlayCardPolicy.EffectiveExtraAction(c.Extra, r);
            ((TextBlock)extraBadge.Child!).Text = ExText.T(c.Extra ? "自带再动" : "自定义再动");
        }
        UpdateExtraBadge();
        var head = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,*,Auto"), ColumnSpacing = 14 };
        head.Children.Add(Art(c, 52, 13));
        Grid.SetColumn(badges, 1); head.Children.Add(badges);
        var headActions = new StackPanel
        {
            Orientation = Orientation.Horizontal, Spacing = 8, VerticalAlignment = VerticalAlignment.Center,
            Children =
            {
                Btn("更换卡牌", () => OpenPicker(change: true), "lg-sm"),
                Btn("删除", () => LiquidModal.Confirm(this, "删除规则", $"删除“{r.Card}”的规则？", "删除", () => DeleteRule(r), true), "lg-sm", "lg-danger-text"),
            }
        };
        Grid.SetColumn(headActions, 2); head.Children.Add(headActions);
        _detail.Children.Add(head);
        _detail.Children.Add(new Border { Classes = { "lg-divider" } });

        // Timing window + tier override
        var two = new Grid { ColumnDefinitions = new ColumnDefinitions("*,12,*") };
        two.Children.Add(Box(TimingBox(r)));
        var tierBox = Box(TierBox(r, c, UpdateExtraBadge));
        Grid.SetColumn(tierBox, 2); two.Children.Add(tierBox);
        _detail.Children.Add(two);

        if (PlayCardPolicy.Key(c.Name) == "国民的アイドル") _detail.Children.Add(Box(ComboBox(r, c), new Thickness(14, 12)));

        _detail.Children.Add(Box(CurveBox(r)));
        if (animate) LiquidStage.PlayEnter(_detail);
        _building = old;
    }

    private Control TimingBox(PlayCardRule r)
    {
        var host = new StackPanel { Spacing = 10 };
        var mode = new SegmentedControl { Classes = { "lg-sm" }, ItemsSource = new[] { ExText.T("全回合"), ExText.T("指定区间") }, SelectedIndex = r.Limited ? 1 : 0, HorizontalAlignment = HorizontalAlignment.Right };
        AutomationProperties.SetName(mode, ExText.T("生效回合窗口"));
        var title = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), Children = { Text("生效回合窗口", 12.5, true) } };
        Grid.SetColumn(mode, 1); title.Children.Add(mode);
        host.Children.Add(title);
        var body = new ContentControl();
        host.Children.Add(body);
        void RenderBody()
        {
            if (!r.Limited) { body.Content = Text("全程所有回合均参与出牌评估", 12, false, "lg-sub"); return; }
            var total = PlayCardPolicy.Total(r);
            NumericUpDown? min = null, max = null;
            min = Stepper("最少剩余回合", r.Min, 1, total, v =>
            {
                if (_building) return;
                r.Min = v; if (r.Max < v) { r.Max = v; max!.Value = v; }
                Model.Mark(); RefreshRule(r);
            });
            max = Stepper("最多剩余回合", Math.Min(r.Max, total), 1, total, v =>
            {
                if (_building) return;
                r.Max = v; if (r.Min > v) { r.Min = v; min!.Value = v; }
                Model.Mark(); RefreshRule(r);
            });
            var row = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), ColumnSpacing = 8 };
            row.Children.Add(Text("倒数区间", 12.5, false, "lg-sub"));
            var steppers = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { min, Text("至", 12, false, "lg-muted"), max, Text("回合", 12.5, false, "lg-sub") } };
            Grid.SetColumn(steppers, 1); row.Children.Add(steppers);
            body.Content = row;
            LiquidStage.PlayExpand(row);
        }
        RenderBody();
        mode.SelectionChanged += (_, _) =>
        {
            if (_building) return;
            r.Limited = mode.SelectedIndex == 1;
            Model.Mark(); RenderBody(); RefreshRule(r);
        };
        return host;
    }

    private Control TierBox(PlayCardRule r, CardListCard c, Action updateExtraBadge)
    {
        var host = new StackPanel { Spacing = 10 };
        // Real policy: SSR may move down to R/SR; lower rarities may only rise up to SSR; LR stays LR.
        int baseTier = Array.IndexOf(PlayCardPolicy.Tiers, c.Rarity), minTier = baseTier == 3 ? 1 : baseTier, maxTier = baseTier == 4 ? 4 : 3;
        var items = PlayCardPolicy.Tiers.Select((t, i) => new ListBoxItem { Content = t, IsEnabled = i >= minTier && i <= maxTier, Tag = t }).ToList();
        var tier = new SegmentedControl { Classes = { "lg-sm" }, ItemsSource = items, SelectedIndex = PlayCardPolicy.EffectiveTier(c.Rarity, r), HorizontalAlignment = HorizontalAlignment.Right };
        AutomationProperties.SetName(tier, ExText.T(baseTier == 3 ? "调整出牌档位" : "提升排序档位"));
        tier.SelectionChanged += (_, _) =>
        {
            if (_building || tier.SelectedItem is not ListBoxItem { Tag: string t }) return;
            r.Tier = t; Model.Mark(); RefreshRule(r);
        };
        var title = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), Children = { Text("排序档位覆盖", 12.5, true) } };
        Grid.SetColumn(tier, 1); title.Children.Add(tier);
        host.Children.Add(title);

        // Two equal columns: left group starts at the left edge, right group at the centre line.
        var toggles = new Grid { ColumnDefinitions = new ColumnDefinitions("*,*"), Margin = new Thickness(0, 2, 0, 0) };
        Control Toggle(string label, bool value, Action<bool> set)
        {
            var sw = new ToggleSwitch { IsChecked = value, VerticalAlignment = VerticalAlignment.Center };
            AutomationProperties.SetName(sw, ExText.T(label));
            sw.IsCheckedChanged += (_, _) => { if (!_building) set(sw.IsChecked == true); };
            var g = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,Auto"), ColumnSpacing = 10, HorizontalAlignment = HorizontalAlignment.Left, Height = 28 };
            var t = new TextBlock { Text = ExText.T(label), FontSize = 12.5, LineHeight = 28, Height = 28, VerticalAlignment = VerticalAlignment.Center };
            g.Children.Add(t);
            Grid.SetColumn(sw, 1); g.Children.Add(sw);
            return g;
        }
        var single = Toggle("最多保留一张", r.SingleCopy, v => { r.SingleCopy = v; Model.Mark(); });
        var extra = Toggle("自定义再动", r.CustomExtraAction, v => { r.CustomExtraAction = v; Model.Mark(); updateExtraBadge(); RefreshRule(r); });
        toggles.Children.Add(single);
        Grid.SetColumn(extra, 1); toggles.Children.Add(extra);
        host.Children.Add(new Border { Classes = { "lg-divider" } });
        host.Children.Add(toggles);
        host.Children.Add(Text(baseTier == 3 ? "SSR 可降至 R 或 SR，仅影响战斗出牌；再动属性独立判断。" : baseTier == 4 ? "LR 保持原档位；再动属性独立判断。" : "提升档位应用于整个培育的选牌判断；停用规则后恢复。回合限制与曲线仅用于出牌。", 11.5, false, "lg-muted"));
        var tip = Text("最多保留一张：仅在准备拿此牌时检查牌组；自定义再动：出牌与培育选牌时按再动牌排序。", 11.5, false, "lg-muted");
        host.Children.Add(tip);
        return host;
    }

    private Control ComboBox(PlayCardRule r, CardListCard c)
    {
        var host = new StackPanel { Spacing = 8 };
        var row = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), ColumnSpacing = 10 };
        var line = new WrapPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
        line.Children.Add(new TextBlock { Text = ExText.T("国民的アイドル配合目标："), FontSize = 12.5, FontWeight = FontWeight.SemiBold });
        line.Children.Add(new TextBlock { Text = ExText.T("联动「初星黑醋」取回："), FontSize = 12.5, Classes = { "lg-sub" } });
        line.Children.Add(new TextBlock { Text = string.IsNullOrEmpty(r.ComboTarget) ? ExText.T("未指定") : r.ComboTarget, FontSize = 12.5, FontWeight = FontWeight.SemiBold, Classes = { "lg-accent" } });
        row.Children.Add(line);
        var pick = Btn("选择要生效的牌", () => OpenPicker(combo: true), "lg-sm", "lg-link");
        var clear = Btn("清除选择", () => { r.ComboTarget = ""; Model.Mark(); RenderDetail(false); }, "lg-sm");
        clear.IsVisible = !string.IsNullOrEmpty(r.ComboTarget);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { pick, clear } };
        Grid.SetColumn(actions, 1); row.Children.Add(actions);
        host.Children.Add(row);
        host.Children.Add(Text("出牌前用初星黑醋取回缺少的配合牌；两张都在手中时不喝。取牌后按原优先级出牌。", 11.5, false, "lg-muted"));
        var warnings = new StackPanel { Spacing = 4 };
        host.Children.Add(warnings);
        _refreshComboWarnings = () =>
        {
            warnings.Children.Clear();
            if (string.IsNullOrEmpty(r.ComboTarget)) return;
            var targetRule = Model.Preset.Rules.FirstOrDefault(x => x.Enabled && PlayCardPolicy.Key(x.Card) == PlayCardPolicy.Key(r.ComboTarget));
            var targetCard = Model.Card(r.ComboTarget);
            bool conflict = Enumerable.Range(1, 30).Any(turn => PlayCardPolicy.Allowed(r, turn) != PlayCardPolicy.Allowed(targetRule, turn));
            bool order = Enumerable.Range(1, 30).Any(turn => PlayCardPolicy.Allowed(r, turn) && PlayCardPolicy.Allowed(targetRule, turn) &&
                (PlayCardPolicy.EffectiveExtraAction(c.Extra, r) ? 1 : 0, PlayCardPolicy.EffectiveTier(c.Rarity, r), PlayCardPolicy.Priority(r, turn)).CompareTo(
                (PlayCardPolicy.EffectiveExtraAction(targetCard.Extra, targetRule) ? 1 : 0, PlayCardPolicy.EffectiveTier(targetCard.Rarity, targetRule), PlayCardPolicy.Priority(targetRule, turn))) <= 0);
            if (conflict) warnings.Children.Add(Text("配合牌的可用回合不一致，请调整回合限制；实际仍按各自规则出牌。", 11.5, false, "lg-warning"));
            if (order) warnings.Children.Add(Text("部分回合国民的优先级不高于目标牌，请调整档位或曲线；程序不会强制改变出牌顺序。", 11.5, false, "lg-warning"));
        };
        _refreshComboWarnings();
        return host;
    }

    private Control CurveBox(PlayCardRule r)
    {
        var host = new StackPanel { Spacing = 10 };
        var total = Stepper("总回合数", PlayCardPolicy.Total(r), 3, 30, v =>
        {
            if (_building) return;
            Model.SetTotalTurns(r, v);
            RenderDetail(false); RefreshRule(r);
        });
        total.Width = 112;
        var left = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10, VerticalAlignment = VerticalAlignment.Center, Children = { Text("同档位回合权重曲线", 12.5, true), Text("总回合数:", 12, false, "lg-sub"), total } };
        var mode = new SegmentedControl { Classes = { "lg-sm" }, ItemsSource = new[] { ExText.T("平滑折线"), ExText.T("阶梯保持") }, SelectedIndex = r.Mode == "step" ? 1 : 0 };
        AutomationProperties.SetName(mode, ExText.T("曲线过渡方式"));
        mode.SelectionChanged += (_, _) => { if (_building) return; r.Mode = mode.SelectedIndex == 1 ? "step" : "linear"; Model.Mark(); RefreshRule(r); RenderNodes(); };
        Button Template(string label, string kind, string toast)
        {
            var b = Btn(label, () => { Model.ApplyTemplate(r, kind); RefreshRule(r); RenderNodes(); ToastHelper.Info(ExText.T("已应用权重"), ExText.T(toast)); }, "lg-sm");
            return b;
        }
        var reset = Template("默认权重", "default", "当前卡牌所有回合的权重已恢复为 50");
        ToolTip.SetTip(reset, ExText.T("将当前卡牌的回合权重恢复为 50"));
        var right = new WrapPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, ItemSpacing = 8, LineSpacing = 6, Children = { mode, reset, Template("后期爆发", "late", "后期爆发"), Template("前期抢分", "early", "前期抢分") } };
        var head = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,*"), ColumnSpacing = 12, Children = { left } };
        Grid.SetColumn(right, 1); head.Children.Add(right);
        host.Children.Add(head);

        _curveStatus = Text("", 11.5, false, "lg-sub");
        host.Children.Add(_curveStatus);
        _curve = new CardRuleCurve { Rule = r, Model = Model, PreviewTurn = Model.PreviewTurn, Height = 230, HorizontalAlignment = HorizontalAlignment.Stretch };
        AutomationProperties.SetName(_curve, ExText.T("剩余回合与同档优先级曲线"));
        _curve.PointChanged += (_, _, _, done) => { RefreshRule(r); if (done) RenderNodes(); };
        _curve.DeleteRejected += () => ToastHelper.Warn(ExText.T("无法删除"), ExText.T("至少需要保留 2 个曲线节点"));
        host.Children.Add(_curve);
        UpdateCurveStatus();
        var hints = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), Children = { Text("• 按住节点可自由拖动（左右调整回合、上下调整权重）；点击空白处添加节点", 11.5, false, "lg-muted") } };
        var hint2 = Text("• 双击或右键节点可直接删除", 11.5, false, "lg-muted");
        Grid.SetColumn(hint2, 1); hints.Children.Add(hint2);
        host.Children.Add(hints);

        // Exact numeric editing kept from 2.1 (not in the prototype, same design language).
        var nodes = new WrapPanel { Orientation = Orientation.Horizontal, ItemSpacing = 8, LineSpacing = 8 };
        var expander = new Expander { Header = ExText.T("精确编辑节点"), Content = nodes, HorizontalAlignment = HorizontalAlignment.Stretch, Padding = new Thickness(10) };
        host.Children.Add(expander);
        void RenderNodesLocal()
        {
            nodes.Children.Clear();
            foreach (var p in r.Points.ToList())
            {
                var point = p;
                var turn = Stepper("节点剩余回合", p[0], 1, PlayCardPolicy.Total(r), v => { if (Model.MovePoint(r, point, v, point[1])) { Model.CommitPoint(r, point); RefreshRule(r); } });
                var score = Stepper("节点优先级", p[1], 0, 100, v => { if (Model.MovePoint(r, point, point[0], v)) RefreshRule(r); });
                var remove = new Button { Classes = { "lg-icon", "lg-sm" }, Content = new LiquidIcon { Kind = "x", Size = 12 }, IsEnabled = r.Points.Count > 2 };
                AutomationProperties.SetName(remove, ExText.T("删除节点"));
                remove.Click += (_, _) => { if (Model.DeletePoint(r, point)) { RefreshRule(r); RenderNodesLocal(); } };
                nodes.Children.Add(new GlassPanel { Classes = { "box" }, Padding = new Thickness(10, 6), Content = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6, Children = { turn, Text("→", 12, false, "lg-muted"), score, remove } } });
            }
            var add = Btn("＋ 添加节点", () => { Model.AddPoint(); RefreshRule(r); RenderNodesLocal(); }, "lg-sm");
            add.IsEnabled = r.Points.Count < 30;
            nodes.Children.Add(add);
        }
        _renderNodes = RenderNodesLocal;
        RenderNodesLocal();
        return host;
    }

    private Action? _renderNodes;
    private void RenderNodes() => _renderNodes?.Invoke();

    private void UpdateCurveStatus()
    {
        if (_curveStatus == null || Model.Selected is not { } r) return;
        _curveStatus.Text = ExText.T(!r.Enabled ? "规则已停用" : PlayCardPolicy.Allowed(r, Model.PreviewTurn)
            ? $"剩余 {Model.PreviewTurn} 回合 · 同档优先级 {PlayCardPolicy.Priority(r, Model.PreviewTurn)}"
            : $"剩余 {Model.PreviewTurn} 回合 · 暂不使用");
    }

    private void DeleteRule(PlayCardRule r)
    {
        if (_listItems.TryGetValue(r.Id, out var item)) LiquidStage.PlayDeparture(item);
        Model.SelectedId = r.Id;
        Model.RemoveRule();
        RenderList();
        foreach (var i in _listItems.Values) UpdateItemState(i);
        RenderDetail(true);
        UpdateHand();
    }

    // ================= Hand simulation =================

    private void UpdateHand()
    {
        var old = _building; _building = true;
        var total = PlayCardPolicy.Total(Model.Selected);
        _handSlider.Maximum = total;
        if (Model.PreviewTurn > total) Model.PreviewTurn = total;
        if (Model.PreviewTurn < 1) Model.PreviewTurn = 1;
        if ((int)_handSlider.Value != Model.PreviewTurn) _handSlider.Value = Model.PreviewTurn;
        _handLabel.Text = ExText.T($"剩余 {Model.PreviewTurn} / {total} 回合");
        AutomationProperties.SetHelpText(_handSlider, ExText.T($"剩余 {Model.PreviewTurn} 回合，共 {total} 回合"));
        _building = old;

        _handCards.Children.Clear();
        int rank = 0;
        foreach (var p in Model.Preview())
        {
            var label = p.Allowed ? "#" + (++rank) : ExText.T("回合外");
            var rankText = new TextBlock { Text = label, FontSize = 12, FontWeight = FontWeight.Bold, VerticalAlignment = VerticalAlignment.Center };
            rankText.Classes.Set(rank == 1 && p.Allowed ? "lg-success" : "lg-sub", true);
            var info = new TextBlock
            {
                Text = (p.Extra ? ExText.T("再动") + " · " : "") + PlayCardPolicy.Tiers[p.Tier] + " · " + ExText.T($"{p.Priority}分"),
                FontSize = 11, Classes = { "lg-muted" }, VerticalAlignment = VerticalAlignment.Center
            };
            var slot = p.Slot;
            var remove = new Button { Classes = { "lg-icon", "lg-sm" }, Width = 22, Height = 22, Content = new LiquidIcon { Kind = "x", Size = 10 }, IsEnabled = Model.Preset.Hand.Count > 1 };
            AutomationProperties.SetName(remove, ExText.T("移除模拟手牌 ") + p.Card.Name);
            ToolTip.SetTip(remove, ExText.T(Model.Preset.Hand.Count > 1 ? "移除" : "至少保留一张模拟手牌"));
            remove.Click += (_, _) => { Model.RemoveHand(slot); UpdateHand(); };
            var chip = new GlassPanel
            {
                Classes = { "box" }, Padding = new Thickness(8, 5), Margin = new Thickness(0, 2, 10, 2), Opacity = p.Allowed ? 1 : 0.55,
                Content = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { rankText, Art(p.Card, 26, 7), new TextBlock { Text = p.Card.Name, FontSize = 12, FontWeight = FontWeight.SemiBold, VerticalAlignment = VerticalAlignment.Center }, info, remove } }
            };
            _handCards.Children.Add(chip);
        }
        if (Model.Preset.Hand.Count < 5)
        {
            var add = new Button { Classes = { "lg-icon", "lg-sm" }, Content = new LiquidIcon { Kind = "plus", Size = 12 }, VerticalAlignment = VerticalAlignment.Center };
            AutomationProperties.SetName(add, ExText.T("添加模拟手牌"));
            ToolTip.SetTip(add, ExText.T("添加模拟手牌"));
            add.Click += (_, _) => OpenPicker(hand: true);
            _handCards.Children.Add(add);
        }
    }
}
