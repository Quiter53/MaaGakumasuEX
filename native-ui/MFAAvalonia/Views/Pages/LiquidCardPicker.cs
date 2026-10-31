using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.Helper;
using MFAAvalonia.ViewModels.Pages;
using System;
using System.Collections.Generic;
using System.Linq;

namespace MFAAvalonia.Views.Pages;

/// <summary>Filter semantics shared by every card picker (rules, hand, drinks, targets, lists).</summary>
public static class CardCatalogFilter
{
    public static readonly string[] Rarities = ["", "LR", "SSR", "SR", "R", "N"];
    public static readonly string[] Origins = ["", "none", "idol", "support"];
    public static readonly (int Min, int Max)[] UnlockRanges = [(0, 999), (0, 0), (1, 20), (21, 40), (41, 60), (61, 999)];
    public static readonly string[] PlanNames = ["", "不限", "感性", "理性", "非凡"];
    public static readonly string[] CategoryNames = ["", "主动技能", "心智技能", "负面卡牌"];
    public static readonly Dictionary<string, string> OriginNames = new() { ["none"] = "普通", ["idol"] = "角色卡", ["support"] = "支援卡" };

    public static List<CardListCard> Apply(IEnumerable<CardListCard> cards, string query, int rarity, int plan, int category, int origin, int conversion, int extra, int unlock, int sort)
    {
        var (min, max) = UnlockRanges[Math.Clamp(unlock, 0, UnlockRanges.Length - 1)];
        var q = (query ?? "").Trim();
        var rows = cards.Where(c => c.Name.Contains(q, StringComparison.OrdinalIgnoreCase)
            && (rarity == 0 || c.Rarity == Rarities[rarity]) && (plan == 0 || c.PlanType == plan)
            && (category == 0 || c.Category == category) && (origin == 0 || c.Origin == Origins[origin])
            && (conversion == 0 || c.IsConversion == (conversion == 2)) && (extra == 0 || c.Extra == (extra == 1))
            && c.UnlockProducerLevel >= min && c.UnlockProducerLevel <= max);
        return (sort == 1 ? rows.OrderBy(c => c.UnlockProducerLevel) : sort == 2 ? rows.OrderByDescending(c => c.UnlockProducerLevel) : rows).ToList();
    }

    public static string Describe(CardListCard c) =>
        $"{c.Rarity} · {ExText.T(PlanNames[Math.Clamp(c.PlanType, 0, 4)])} · {ExText.T(CategoryNames[Math.Clamp(c.Category, 0, 3)])}";

    public static string Describe2(CardListCard c) =>
        $"{ExText.T(OriginNames.GetValueOrDefault(c.Origin, "普通"))} · {(c.Extra ? ExText.T("再动") + " · " : "")}{(c.IsConversion ? ExText.T("转换卡") + " · " : "")}Lv.{c.UnlockProducerLevel}";

    public static IBrush RarityBrush(string rarity) => Brush.Parse(rarity switch
    {
        "LR" => "#F43F5E", "SSR" => "#A855F7", "SR" => "#F59E0B", "R" => "#38BDF8", _ => "#94A3B8"
    });
}

/// <summary>Options for <see cref="LiquidCardPicker"/>.</summary>
public sealed class CardPickerOptions
{
    public required IReadOnlyList<CardListCard> Cards { get; init; }
    public required Func<CardListCard, Bitmap?> Image { get; init; }
    public required Action<CardListCard> Confirm { get; init; }
    public string Title { get; init; } = "选择技能卡";
    public string Subtitle { get; init; } = "筛选卡库，选中后确认";
    public string ConfirmText { get; init; } = "使用此卡";
    /// <summary>Hidden cards (source restrictions of the caller).</summary>
    public Func<CardListCard, bool>? Eligible { get; init; }
    /// <summary>Cards listed first in their own section (e.g. the current preset).</summary>
    public IReadOnlyList<string>? Priority { get; init; }
    public string PriorityHeader { get; init; } = "当前预设";
    /// <summary>Small accent badge under a tile (e.g. 已在模拟手牌).</summary>
    public Func<CardListCard, string?>? Badge { get; init; }
    /// <summary>Reason that blocks confirmation for a card (e.g. 已有规则).</summary>
    public Func<CardListCard, string?>? Blocked { get; init; }
    public CardListCard? Initial { get; init; }
    public Action? Cancelled { get; init; }
}

/// <summary>
/// Shared native card picker: search (IME safe — filtering waits for committed text), eight
/// filters, result count, paging, keyboard focus kept on the search box, tile instances reused.
/// </summary>
public sealed class LiquidCardPicker
{
    private const int PageSize = 60;
    private static readonly (string Label, string[] Values)[] Filters =
    [
        ("稀有度", ["全部稀有度", "LR", "SSR", "SR", "R", "N"]),
        ("计划", ["全部计划", "不限", "感性", "理性", "非凡"]),
        ("技能分类", ["全部分类", "主动技能", "心智技能", "负面卡牌"]),
        ("来源", ["全部来源", "普通", "角色卡", "支援卡"]),
        ("转换", ["全部转换", "非转换卡", "转换卡"]),
        ("再动", ["全部再动属性", "再动", "非再动"]),
        ("解锁等级", ["不限解锁等级", "初始解锁", "1–20 级", "21–40 级", "41–60 级", "61 级及以上"]),
        ("排列方式", ["默认顺序", "等级升序", "等级降序"]),
    ];

    private readonly CardPickerOptions _options;
    private readonly TextBox _search = new() { Watermark = ExText.T("搜索日文卡牌名称…") };
    private readonly ComboBox[] _filters;
    private readonly TextBlock _count = new() { Classes = { "lg-sub" }, VerticalAlignment = VerticalAlignment.Center };
    private readonly TextBlock _selection = new() { Classes = { "lg-sub" }, VerticalAlignment = VerticalAlignment.Center, TextTrimming = TextTrimming.CharacterEllipsis };
    private readonly TextBlock _pageText = new() { Classes = { "lg-sub" }, VerticalAlignment = VerticalAlignment.Center };
    private readonly Button _prev = new() { Classes = { "lg-sm" } }, _next = new() { Classes = { "lg-sm" } };
    private readonly Button _confirm = new() { Classes = { "lg-primary" }, MinWidth = 96 };
    private readonly StackPanel _results = new() { Spacing = 10 };
    private readonly UniformGrid _priorityGrid = new() { Columns = 3 }, _restGrid = new() { Columns = 3 };
    private readonly TextBlock _priorityHeader = new() { Classes = { "lg-sub" }, Margin = new Thickness(0, 2, 0, 0) };
    private readonly TextBlock _restHeader = new() { Classes = { "lg-sub" }, Text = ExText.T("其他卡牌") };
    private readonly TextBlock _empty = new() { Classes = { "lg-sub" }, Text = ExText.T("没有符合条件的卡牌，可重置筛选后重试。"), IsVisible = false };
    private readonly ScrollViewer _scroll;
    private readonly Dictionary<string, Button> _tiles = new();
    private CardListCard? _selected;
    private int _page;
    private bool _resetting, _queued;
    private LiquidModal? _modal;

    private LiquidCardPicker(CardPickerOptions options)
    {
        _options = options;
        _selected = options.Initial;
        Avalonia.Automation.AutomationProperties.SetName(_search, ExText.T("搜索卡牌"));
        _filters = Filters.Select(f =>
        {
            var c = new ComboBox { ItemsSource = f.Values.Select(ExText.T).ToArray(), SelectedIndex = 0, HorizontalAlignment = HorizontalAlignment.Stretch };
            Avalonia.Automation.AutomationProperties.SetName(c, ExText.T(f.Label));
            c.SelectionChanged += (_, _) => { _page = 0; Queue(); };
            return c;
        }).ToArray();
        _search.TextChanged += (_, _) => { _page = 0; Queue(); };
        _prev.Content = ExText.T("上一页"); _next.Content = ExText.T("下一页");
        _prev.Click += (_, _) => { _page--; Render(); _scroll!.Offset = default; };
        _next.Click += (_, _) => { _page++; Render(); _scroll!.Offset = default; };
        _confirm.Content = ExText.T(options.ConfirmText);
        _confirm.Click += (_, _) => Commit();
        _scroll = new ScrollViewer { Content = _results, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, Padding = new Thickness(3, 3, 8, 3) };
    }

    public static LiquidModal? Open(Visual anchor, CardPickerOptions options)
    {
        var picker = new LiquidCardPicker(options);
        return picker.Show(anchor);
    }

    private LiquidModal? Show(Visual anchor)
    {
        var filterGrid = new UniformGrid { Columns = 4 };
        for (int i = 0; i < _filters.Length; i++)
            filterGrid.Children.Add(new StackPanel { Spacing = 4, Margin = new Thickness(0, 0, 10, 10), Children = { new TextBlock { Text = ExText.T(Filters[i].Label), FontSize = 11, Classes = { "lg-sub" } }, _filters[i] } });
        var reset = new Button { Classes = { "lg-sm" }, Content = ExText.T("重置筛选") };
        reset.Click += (_, _) => Reset();
        var countRow = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), Children = { _count } };
        Grid.SetColumn(reset, 1); countRow.Children.Add(reset);
        _results.Children.Add(_priorityHeader);
        _results.Children.Add(_priorityGrid);
        _results.Children.Add(_restHeader);
        _results.Children.Add(_restGrid);
        _results.Children.Add(_empty);
        var pages = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10, HorizontalAlignment = HorizontalAlignment.Center, Margin = new Thickness(0, 6, 0, 0), Children = { _prev, _pageText, _next } };
        _results.Children.Add(pages);

        var cancel = new Button { Content = ExText.T("取消"), MinWidth = 88 };
        cancel.Click += (_, _) => _modal?.RequestCancel();
        var footer = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), Children = { _selection } };
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { cancel, _confirm } };
        Grid.SetColumn(buttons, 1); footer.Children.Add(buttons);

        var body = new Grid { RowDefinitions = new RowDefinitions("Auto,Auto,Auto,*"), RowSpacing = 10 };
        body.Children.Add(_search);
        Grid.SetRow(filterGrid, 1); body.Children.Add(filterGrid);
        Grid.SetRow(countRow, 2); body.Children.Add(countRow);
        Grid.SetRow(_scroll, 3); body.Children.Add(_scroll);

        Render();
        _modal = LiquidModal.Show(anchor, LiquidModal.Layout(_options.Title, _options.Subtitle, body, footer, () => _modal?.RequestCancel(), false), 940, 740);
        if (_modal != null)
        {
            _modal.Cancel = () => { _modal.Close(); _options.Cancelled?.Invoke(); };
            Dispatcher.UIThread.Post(() => _search.Focus(), DispatcherPriority.Loaded);
        }
        return _modal;
    }

    private void Reset()
    {
        _resetting = true;
        _search.Text = "";
        foreach (var f in _filters) f.SelectedIndex = 0;
        _resetting = false;
        _page = 0;
        Render();
        _search.Focus();
    }

    private void Queue()
    {
        if (_resetting || _queued) return;
        _queued = true;
        Dispatcher.UIThread.Post(() => { _queued = false; Render(); }, DispatcherPriority.Background);
    }

    private void Render()
    {
        var f = _filters.Select(c => Math.Max(0, c.SelectedIndex)).ToArray();
        var cards = CardCatalogFilter.Apply(_options.Cards, _search.Text ?? "", f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7])
            .Where(c => _options.Eligible?.Invoke(c) ?? true).ToList();
        var eligibleTotal = _options.Cards.Count(c => _options.Eligible?.Invoke(c) ?? true);
        _count.Text = ExText.T($"卡牌库 · {cards.Count} / {eligibleTotal} 张");
        var priorityNames = _options.Priority ?? [];
        var priority = cards.Where(c => priorityNames.Contains(c.Name)).OrderBy(c => priorityNames.ToList().IndexOf(c.Name)).ToList();
        var rest = cards.Where(c => !priorityNames.Contains(c.Name)).ToList();
        var pages = Math.Max(1, (int)Math.Ceiling(rest.Count / (double)PageSize));
        _page = Math.Clamp(_page, 0, pages - 1);
        Sync(_priorityGrid, priority);
        Sync(_restGrid, rest.Skip(_page * PageSize).Take(PageSize).ToList());
        _priorityHeader.IsVisible = _priorityGrid.IsVisible = priority.Count > 0;
        _priorityHeader.Text = ExText.T($"{_options.PriorityHeader} · {priority.Count} 张");
        _restHeader.IsVisible = priority.Count > 0 && rest.Count > 0;
        _empty.IsVisible = cards.Count == 0;
        _pageText.Text = $"{_page + 1} / {pages}";
        _prev.IsEnabled = _page > 0; _next.IsEnabled = _page < pages - 1;
        ((Control)_prev.Parent!).IsVisible = pages > 1;
        UpdateSelection();
    }

    /// <summary>Keeps tile instances (and keyboard focus) alive; only membership and order change.</summary>
    private void Sync(UniformGrid grid, List<CardListCard> cards)
    {
        var tiles = cards.Select(Tile).ToList();
        if (!grid.Children.SequenceEqual(tiles))
        {
            grid.Children.Clear();
            grid.Children.AddRange(tiles);
        }
    }

    private Button Tile(CardListCard card)
    {
        if (_tiles.TryGetValue(card.Name, out var tile)) { UpdateTileBadge(tile, card); return tile; }
        var image = new Border
        {
            Width = 52, Height = 52, CornerRadius = new CornerRadius(12), BorderThickness = new Thickness(2), ClipToBounds = true,
            BorderBrush = CardCatalogFilter.RarityBrush(card.Rarity), Child = new Image { Source = _options.Image(card), Stretch = Stretch.UniformToFill },
        };
        var badge = new TextBlock { FontSize = 10.5, FontWeight = FontWeight.SemiBold, Classes = { "lg-accent" }, Name = "Badge" };
        var text = new StackPanel
        {
            Spacing = 2, VerticalAlignment = VerticalAlignment.Center,
            Children =
            {
                new TextBlock { Text = card.Name, FontSize = 12, FontWeight = FontWeight.SemiBold, TextWrapping = TextWrapping.Wrap, MaxLines = 2 },
                new TextBlock { Text = CardCatalogFilter.Describe(card), FontSize = 10.5, Classes = { "lg-sub" } },
                new TextBlock { Text = CardCatalogFilter.Describe2(card), FontSize = 10.5, Classes = { "lg-sub" } },
                badge,
            }
        };
        var grid = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,*"), ColumnSpacing = 10, Children = { image } };
        Grid.SetColumn(text, 1); grid.Children.Add(text);
        tile = new Button
        {
            Classes = { "lg-picker-card" }, Content = grid, HorizontalAlignment = HorizontalAlignment.Stretch,
            HorizontalContentAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 0, 10, 10), Tag = card,
        };
        Avalonia.Automation.AutomationProperties.SetName(tile, card.Name);
        tile.Click += (_, _) => { _selected = card; UpdateSelection(); };
        tile.DoubleTapped += (_, _) => { _selected = card; UpdateSelection(); Commit(); };
        _tiles[card.Name] = tile;
        UpdateTileBadge(tile, card);
        return tile;
    }

    private void UpdateTileBadge(Button tile, CardListCard card)
    {
        if (tile.Content is not Grid g || g.Children.OfType<StackPanel>().FirstOrDefault()?.Children.OfType<TextBlock>().FirstOrDefault(t => t.Name == "Badge") is not { } badge) return;
        var text = _options.Badge?.Invoke(card) ?? _options.Blocked?.Invoke(card);
        badge.Text = text == null ? "" : ExText.T(text);
        badge.IsVisible = text != null;
    }

    private void UpdateSelection()
    {
        foreach (var (_, t) in _tiles) t.Classes.Set("selected", t.Tag == _selected);
        var blocked = _selected == null ? null : _options.Blocked?.Invoke(_selected);
        _selection.Text = _selected == null ? ExText.T("尚未选择") : _selected.Name + (blocked != null ? " · " + ExText.T(blocked) : "");
        _confirm.IsEnabled = _selected != null && blocked == null && (_options.Eligible?.Invoke(_selected) ?? true);
    }

    private void Commit()
    {
        if (_selected == null || !_confirm.IsEnabled) return;
        var card = _selected;
        _modal?.Close();
        _options.Confirm(card);
    }
}
