using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Input;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Styling;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.Helper;
using MFAAvalonia.ViewModels.Pages;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Views.UserControls;

public sealed class CustomizationTarget
{
    [JsonProperty("name")] public string Name { get; set; } = "";
    [JsonProperty("counts")] public int[] Counts { get; set; } = [];
}

// Both task editors use this dispatch, including nested switch options.
public static class TaskTargetControl
{
    public static bool Handles(string? name) => name == "自定义卡牌设置" || MemoryTargetControl.Handles(name);
    public static Control Create(MaaInterface.MaaInterfaceSelectOption option, Action save, string? root = null)
    {
        if (option.Name != "自定义卡牌设置") return MemoryTargetControl.Create(option, save, root);
        root ??= AppPaths.InstallRoot;
        Control? row = null;
        List<CardListCard>? cards = null;
        List<CustomizationTarget> Current()
        {
            try { return JsonConvert.DeserializeObject<List<CustomizationTarget>>(option.Data.GetValueOrDefault("targets") ?? "[]") ?? []; }
            catch { return []; }
        }
        string Summary()
        {
            try
            {
                var targets = JsonConvert.DeserializeObject<List<CustomizationTarget>>(option.Data.GetValueOrDefault("targets") ?? "[]") ?? [];
                return targets.Count == 0 ? ExText.T("尚未设置自定义目标。") : string.Join(" → ", targets.Select(t => t.Name));
            }
            catch { return ExText.T("目标配置无法读取，请重新选择。"); }
        }
        IReadOnlyList<(string, Bitmap?)> Preview()
        {
            cards ??= MemoryTargetControl.LoadCards(root);
            return Current().Select(t => (t.Name, cards.FirstOrDefault(c => c.Name == t.Name) is { } c ? CardImages.Get(root, c) : null)).ToList();
        }
        row = TargetOptionRow.Create("自定义卡牌设置", Preview, Summary, async () =>
        {
            var editor = new CustomizationTargetDialog(root, option.Data.GetValueOrDefault("targets") ?? "[]");
            var result = await TargetOptionRow.ShowAsync(row!, editor);
            if (result != null) { option.Data["targets"] = result; save(); }
        });
        return row;
    }
}

public sealed class CustomizationTargetDialog : UserControl, ITargetEditor
{
    public event Action<string?>? Finished;
    public string Title { get; }
    public void Close(string? result) => Finished?.Invoke(result);
    public List<CustomizationTarget> Targets { get; }
    private readonly List<CardListCard> _cards;
    private readonly JObject _specs;
    private readonly string _root;
    private readonly StackPanel _body = new() { Spacing = 12 };
    private readonly StackPanel _footer = new() { Orientation = Orientation.Horizontal, Spacing = 8, HorizontalAlignment = HorizontalAlignment.Right };
    private readonly TextBlock _status = new() { TextWrapping = TextWrapping.Wrap, Classes = { "lg-danger" } };
    private readonly ScrollViewer _scroll = new() { HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
    private readonly List<(MFAAvalonia.Controls.Liquid.GlassPanel View, CustomizationTarget Target)> _targetRows = [];

    public CustomizationTargetDialog(string root, string saved)
    {
        _root = root; Title = ExText.T("自定义卡牌设置");
        try
        {
            Targets = JsonConvert.DeserializeObject<List<CustomizationTarget>>(saved) ?? [];
            if (Targets.Any(t => t == null || string.IsNullOrWhiteSpace(t.Name))) throw new InvalidDataException();
        }
        catch { Targets = []; _status.Text = ExText.T("原目标配置无法读取，请重新选择后保存。"); }
        try
        {
            _cards = JObject.Parse(File.ReadAllText(Path.Combine(root, "config/card_library.json")))["cards"]!.ToObject<List<CardListCard>>()!;
            _specs = (JObject)JObject.Parse(File.ReadAllText(Path.Combine(root, "data/card_customizations.json")))["cards"]!;
        }
        catch { _cards = []; _specs = new(); _status.Text = ExText.T("自定义卡牌资料无法加载。"); }
        var layout = new Grid { RowDefinitions = new RowDefinitions("Auto,*,Auto"), RowSpacing = 14 };
        var head = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto") };
        head.Children.Add(new TextBlock { Text = Title, FontSize = 16, FontWeight = FontWeight.Bold });
        var close = new Button { Classes = { "lg-icon", "lg-sm" }, Content = new LiquidIcon { Kind = "x", Size = 13 } };
        Avalonia.Automation.AutomationProperties.SetName(close, ExText.T("关闭"));
        close.Click += (_, _) => Close(null); Grid.SetColumn(close, 1); head.Children.Add(close);
        layout.Children.Add(head);
        _scroll.Content = _body; _scroll.Padding = new Thickness(0, 0, 6, 0); Grid.SetRow(_scroll, 1); layout.Children.Add(_scroll);
        Grid.SetRow(_footer, 2); layout.Children.Add(_footer); Content = layout;
        ShowTargets();
    }

    public IEnumerable<CardListCard> Filter(string search = "", string rarity = "全部", int plan = 0, string origin = "全部", int conversion = 0) =>
        _cards.Where(c => _specs[c.Name] != null && c.Name.Contains(search, StringComparison.OrdinalIgnoreCase)
            && (rarity == "全部" || c.Rarity == rarity) && (plan == 0 || c.PlanType == plan)
            && (origin == "全部" || c.Origin == origin) && (conversion == 0 || c.IsConversion == (conversion == 2)));

    public bool AddCard(string name)
    {
        if (Targets.Count >= 64 || Targets.Any(t => t.Name == name) || !Filter().Any(c => c.Name == name)) return false;
        var counts = new int[((JArray)_specs[name]!["options"]!).Count]; counts[0] = 1;
        Targets.Add(new CustomizationTarget { Name = name, Counts = counts }); ShowTargets(); return true;
    }
    public bool Move(int index, int offset)
    {
        return MoveTo(index, index + offset);
    }
    public bool MoveTo(int index, int destination)
    {
        if (index < 0 || index >= Targets.Count || destination < 0 || destination >= Targets.Count) return false;
        var target = Targets[index]; Targets.RemoveAt(index); Targets.Insert(destination, target); ShowTargets(); return true;
    }
    public string Serialize()
    {
        if (Targets.Count > 64 || Targets.GroupBy(t => t.Name).Any(g => g.Count() > 1)) throw new InvalidDataException(ExText.T("目标卡牌不能重复。"));
        foreach (var target in Targets)
        {
            var spec = _specs[target.Name]; var options = spec?["options"] as JArray;
            if (options == null || target.Counts == null || target.Counts.Length != options.Count || target.Counts.Sum() <= 0
                || target.Counts.Sum() > (int)spec!["max"]! || target.Counts.Where((n, i) => n < 0 || n > ((JArray)options[i]["costs"]!).Count).Any())
                throw new InvalidDataException(ExText.T("请检查每项次数和单卡总上限。"));
        }
        return JsonConvert.SerializeObject(Targets);
    }
    private Control Preview(CardListCard card, int size = 56) => new Border
    {
        Width = size, Height = size, CornerRadius = new CornerRadius(13), ClipToBounds = true, BorderThickness = new Thickness(2),
        BorderBrush = MFAAvalonia.Views.Pages.CardCatalogFilter.RarityBrush(card.Rarity),
        Child = new Image { Source = CardImages.Get(_root, card), Stretch = Stretch.UniformToFill },
    };
    /// <summary>Cards the picker may offer (cards with customization data).</summary>
    public List<CardListCard> PickerCandidates() => Filter().ToList();
    private Button Button(string label, Action action)
    {
        var button = new Button { Content = ExText.T(label), Classes = { "lg-sm" }, VerticalAlignment = VerticalAlignment.Center };
        button.Click += (_, _) => action(); return button;
    }
    public void ShowTargets()
    {
        _body.Children.Clear(); _footer.Children.Clear(); _targetRows.Clear();
        _body.Children.Add(new TextBlock { Text = ExText.T("按列表顺序自定义持有的目标卡；选项按游戏从左到右编号。次数是最终目标，包含已经完成的次数。"), TextWrapping = TextWrapping.Wrap, Classes = { "lg-sub" } });
        _body.Children.Add(new TextBlock { Text = ExText.T("按顺序完成每张牌。HIF 每批两张，购买当前目标饮料后再刷新；普通特别指导不刷新。"), TextWrapping = TextWrapping.Wrap, Classes = { "lg-muted" } });
        if (Targets.Count == 0) _body.Children.Add(new TextBlock { Text = ExText.T("尚未设置自定义目标。") });
        foreach (var target in Targets.ToArray())
        {
            var index = Targets.IndexOf(target);
            var box = new StackPanel { Spacing = 8, Margin = new Thickness(12) };
            var head = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,*,Auto"), ColumnSpacing = 12 };
            var card = _cards.FirstOrDefault(c => c.Name == target.Name);
            if (card != null) head.Children.Add(Preview(card));
            var title = new TextBlock { Text = $"{index + 1}. {target.Name}", FontSize = 14, FontWeight = FontWeight.Bold, VerticalAlignment = VerticalAlignment.Center, TextWrapping = TextWrapping.Wrap };
            Grid.SetColumn(title, 1); head.Children.Add(title);
            var controls = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
            var up = Button("上移", () => Move(index, -1)); up.IsEnabled = index > 0;
            var down = Button("下移", () => Move(index, 1)); down.IsEnabled = index < Targets.Count - 1;
            controls.Children.Add(up); controls.Children.Add(down); var remove = Button("移除", () => { Targets.Remove(target); ShowTargets(); }); remove.Classes.Add("lg-danger-text"); controls.Children.Add(remove);
            Grid.SetColumn(controls, 2); head.Children.Add(controls); box.Children.Add(head);
            var spec = _specs[target.Name]; var options = spec?["options"] as JArray;
            if (options != null && target.Counts?.Length == options.Count)
            {
                var fields = new Grid { ColumnDefinitions = new ColumnDefinitions(string.Join(",", Enumerable.Repeat("*", options.Count))) };
                var total = new TextBlock { TextWrapping = TextWrapping.Wrap, FontSize = 12, Classes = { "lg-sub" } };
                void Validate() { total.Text = ExText.T($"目标次数 {target.Counts.Sum()} / 单卡上限 {(int)spec!["max"]!}"); if (target.Counts.Sum() > (int)spec!["max"]!) total.Text += " · " + ExText.T("请减少次数"); }
                for (var i = 0; i < options.Count; i++)
                {
                    var slot = i; var field = new StackPanel { Spacing = 4, Margin = new Thickness(4) };
                    field.Children.Add(new TextBlock { Text = ExText.T($"选项 {i + 1}"), FontWeight = FontWeight.SemiBold, FontSize = 12.5 });
                    field.Children.Add(new TextBlock { Text = (string?)options[i]["label"] ?? "", FontSize = 12, TextWrapping = TextWrapping.Wrap, MinHeight = 34, Classes = { "lg-sub" } });
                    var count = new NumericUpDown { Minimum = 0, Maximum = ((JArray)options[i]["costs"]!).Count, Increment = 1, FormatString = "0", Value = target.Counts[i], HorizontalAlignment = HorizontalAlignment.Left, Width = 120 };
                    Avalonia.Automation.AutomationProperties.SetName(count, ExText.T($"选项 {i + 1}"));
                    count.ValueChanged += (_, _) => { target.Counts[slot] = (int)(count.Value ?? 0); Validate(); };
                    field.Children.Add(count); Grid.SetColumn(field, i); fields.Children.Add(field);
                }
                box.Children.Add(fields); Validate(); box.Children.Add(total);
            }
            else box.Children.Add(new TextBlock { Text = ExText.T("目标配置无法读取，请重新选择。") });
            var row = new MFAAvalonia.Controls.Liquid.GlassPanel { Classes = { "box" }, Padding = new Thickness(0), Content = box };
            var handle = new Border { Name = "CustomizationDragHandle", MinHeight = 30, Padding = new Thickness(4), Background = Brushes.Transparent,
                Cursor = new Cursor(StandardCursorType.SizeAll), Child = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6, Children = { new LiquidIcon { Kind = "grip", Size = 14 }, new TextBlock { Text = ExText.T("拖动排序"), VerticalAlignment = VerticalAlignment.Center, FontSize = 12, Classes = { "lg-muted" } } } } };
            Avalonia.Automation.AutomationProperties.SetName(handle, ExText.T("拖动排序") + " " + target.Name);
            box.Children.Insert(0, handle);
            AttachDrag(handle, target);
            _targetRows.Add((row, target)); _body.Children.Add(row);
        }
        _body.Children.Add(_status);
        _footer.Children.Add(Button("添加目标卡", ShowPicker));
        var cancel = Button("取消", () => Close(null)); cancel.Classes.Remove("lg-sm");
        var save = Button("保存目标", () => { try { Close(Serialize()); } catch (Exception e) { _status.Text = e.Message; } }); save.Classes.Remove("lg-sm"); save.Classes.Add("lg-primary");
        _footer.Children.Add(cancel); _footer.Children.Add(save);
    }
    private void AttachDrag(Border handle, CustomizationTarget target)
    {
        Point? origin = null;
        CustomizationTarget? destination = null;
        void Clear()
        {
            origin = null; destination = null;
            foreach (var row in _targetRows) row.View.Classes.Set("selected", false);
        }
        handle.PointerPressed += (_, e) =>
        {
            if (!e.GetCurrentPoint(handle).Properties.IsLeftButtonPressed) return;
            origin = e.GetPosition(this); e.Pointer.Capture(handle); e.Handled = true;
        };
        handle.PointerMoved += (_, e) =>
        {
            if (origin is not { } start) return;
            var delta = e.GetPosition(this) - start;
            if (delta.X * delta.X + delta.Y * delta.Y < 36) return;
            var at = e.GetPosition(_scroll);
            if (at.Y < 35) _scroll.Offset = new Vector(_scroll.Offset.X, Math.Max(0, _scroll.Offset.Y - 24));
            else if (at.Y > _scroll.Bounds.Height - 35) _scroll.Offset = new Vector(_scroll.Offset.X, _scroll.Offset.Y + 24);
            var y = e.GetPosition(_body).Y;
            destination = _targetRows.OrderBy(r => Math.Abs(r.View.Bounds.Center.Y - y)).FirstOrDefault().Target;
            foreach (var row in _targetRows) row.View.Classes.Set("selected", row.Target == destination);
            e.Handled = true;
        };
        handle.PointerReleased += (_, e) =>
        {
            var from = Targets.IndexOf(target); var to = destination == null ? -1 : Targets.IndexOf(destination);
            Clear(); e.Pointer.Capture(null);
            if (to >= 0 && to != from) MoveTo(from, to);
            e.Handled = true;
        };
        handle.PointerCaptureLost += (_, _) => Clear();
    }
    /// <summary>Adds a target through the shared picker; only cards with customization data are offered.</summary>
    public void ShowPicker()
    {
        var supported = PickerCandidates().Select(c => c.Name).ToHashSet();
        MFAAvalonia.Views.Pages.LiquidCardPicker.Open(this, new MFAAvalonia.Views.Pages.CardPickerOptions
        {
            Cards = _cards, Image = c => CardImages.Get(_root, c), Title = "选择自定义目标卡", Subtitle = "仅列出有自定义资料的卡牌",
            Eligible = c => supported.Contains(c.Name),
            Blocked = c => Targets.Any(t => t.Name == c.Name) ? "已在目标中" : Targets.Count >= 64 ? "目标已满" : null,
            Confirm = card => AddCard(card.Name),
        });
    }
}
