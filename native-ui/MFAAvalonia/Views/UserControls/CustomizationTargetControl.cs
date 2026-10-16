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
        var panel = new StackPanel { Margin = new Thickness(10, 6), Spacing = 6 };
        var button = new Button { Content = ExText.T("自定义卡牌设置"), MinHeight = 44, HorizontalAlignment = HorizontalAlignment.Stretch };
        var summary = new TextBlock { TextWrapping = TextWrapping.Wrap, FontSize = 13 };
        void Refresh()
        {
            try
            {
                var targets = JsonConvert.DeserializeObject<List<CustomizationTarget>>(option.Data.GetValueOrDefault("targets") ?? "[]") ?? [];
                summary.Text = targets.Count == 0 ? ExText.T("尚未设置自定义目标。") : string.Join(" → ", targets.Select(t => t.Name));
            }
            catch { summary.Text = ExText.T("目标配置无法读取，请重新选择。"); }
        }
        button.Click += async (_, _) =>
        {
            if (TopLevel.GetTopLevel(button) is not Window owner) return;
            var dialog = new CustomizationTargetDialog(root, option.Data.GetValueOrDefault("targets") ?? "[]");
            var result = await dialog.ShowDialog<string?>(owner);
            if (result != null) { option.Data["targets"] = result; save(); Refresh(); }
            button.Focus();
        };
        Refresh(); panel.Children.Add(button); panel.Children.Add(summary); return panel;
    }
}

public sealed class CustomizationTargetDialog : Window
{
    public List<CustomizationTarget> Targets { get; }
    private readonly List<CardListCard> _cards;
    private readonly JObject _specs;
    private readonly string _root;
    private readonly StackPanel _body = new() { Spacing = 12 };
    private readonly StackPanel _footer = new() { Orientation = Orientation.Horizontal, Spacing = 12, HorizontalAlignment = HorizontalAlignment.Right };
    private readonly Dictionary<string, Bitmap> _images = new();
    private readonly TextBlock _status = new() { TextWrapping = TextWrapping.Wrap };
    private readonly ScrollViewer _scroll = new() { HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
    private readonly List<(Border View, CustomizationTarget Target)> _targetRows = [];

    public CustomizationTargetDialog(string root, string saved)
    {
        _root = root; Title = ExText.T("自定义卡牌设置"); Width = 920; Height = 760; MinWidth = 680; MinHeight = 540;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        void Theme()
        {
            var dark = ActualThemeVariant == ThemeVariant.Dark;
            Background = new SolidColorBrush(Color.Parse(dark ? "#202225" : "#F6F7FA"));
            Foreground = new SolidColorBrush(Color.Parse(dark ? "#F2F3F5" : "#202225"));
        }
        ActualThemeVariantChanged += (_, _) => Theme(); Theme();
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
        var layout = new Grid { RowDefinitions = new RowDefinitions("*,Auto"), Margin = new Thickness(20) };
        _scroll.Content = _body; layout.Children.Add(_scroll);
        Grid.SetRow(_footer, 1); _footer.Margin = new Thickness(0, 12, 0, 0); layout.Children.Add(_footer); Content = layout;
        KeyDown += (_, e) => { if (e.Key == Key.Escape) { Close(null); e.Handled = true; } };
        Closed += (_, _) => { foreach (var image in _images.Values) image.Dispose(); };
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
    private Control Preview(CardListCard card, int size = 76)
    {
        if (!_images.TryGetValue(card.Image, out var bitmap))
        {
            var path = Path.Combine(_root, card.Image);
            if (File.Exists(path)) _images[card.Image] = bitmap = new Bitmap(path);
        }
        return new Image { Source = bitmap, Width = size, Height = size, Stretch = Stretch.Uniform };
    }
    private Button Button(string label, Action action)
    {
        var button = new Button { Content = ExText.T(label), MinHeight = 40, Padding = new Thickness(12, 6), VerticalAlignment = VerticalAlignment.Center };
        button.Click += (_, _) => action(); return button;
    }
    public void ShowTargets()
    {
        _body.Children.Clear(); _footer.Children.Clear(); _targetRows.Clear();
        _body.Children.Add(new TextBlock { Text = Title, FontSize = 23, FontWeight = FontWeight.Bold });
        _body.Children.Add(new TextBlock { Text = ExText.T("按列表顺序自定义持有的目标卡；选项按游戏从左到右编号。次数是最终目标，包含已经完成的次数。"), TextWrapping = TextWrapping.Wrap });
        _body.Children.Add(new TextBlock { Text = ExText.T("按顺序完成每张牌。HIF 每批两张，购买当前目标饮料后再刷新；普通特别指导不刷新。"), TextWrapping = TextWrapping.Wrap });
        if (Targets.Count == 0) _body.Children.Add(new TextBlock { Text = ExText.T("尚未设置自定义目标。") });
        foreach (var target in Targets.ToArray())
        {
            var index = Targets.IndexOf(target);
            var box = new StackPanel { Spacing = 8, Margin = new Thickness(12) };
            var head = new Grid { ColumnDefinitions = new ColumnDefinitions("80,*,Auto") };
            var card = _cards.FirstOrDefault(c => c.Name == target.Name);
            if (card != null) head.Children.Add(Preview(card));
            var title = new TextBlock { Text = $"{index + 1}. {target.Name}", FontSize = 16, FontWeight = FontWeight.Bold, VerticalAlignment = VerticalAlignment.Center, TextWrapping = TextWrapping.Wrap };
            Grid.SetColumn(title, 1); head.Children.Add(title);
            var controls = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
            var up = Button("上移", () => Move(index, -1)); up.IsEnabled = index > 0;
            var down = Button("下移", () => Move(index, 1)); down.IsEnabled = index < Targets.Count - 1;
            controls.Children.Add(up); controls.Children.Add(down); controls.Children.Add(Button("移除", () => { Targets.Remove(target); ShowTargets(); }));
            Grid.SetColumn(controls, 2); head.Children.Add(controls); box.Children.Add(head);
            var spec = _specs[target.Name]; var options = spec?["options"] as JArray;
            if (options != null && target.Counts?.Length == options.Count)
            {
                var fields = new Grid { ColumnDefinitions = new ColumnDefinitions(string.Join(",", Enumerable.Repeat("*", options.Count))) };
                var total = new TextBlock { TextWrapping = TextWrapping.Wrap };
                void Validate() { total.Text = ExText.T($"目标次数 {target.Counts.Sum()} / 单卡上限 {(int)spec!["max"]!}"); if (target.Counts.Sum() > (int)spec!["max"]!) total.Text += " · " + ExText.T("请减少次数"); }
                for (var i = 0; i < options.Count; i++)
                {
                    var slot = i; var field = new StackPanel { Spacing = 4, Margin = new Thickness(4) };
                    field.Children.Add(new TextBlock { Text = ExText.T($"选项 {i + 1}"), FontWeight = FontWeight.Bold });
                    field.Children.Add(new TextBlock { Text = (string?)options[i]["label"] ?? "", FontSize = 13, TextWrapping = TextWrapping.Wrap, MinHeight = 36 });
                    var count = new NumericUpDown { Minimum = 0, Maximum = ((JArray)options[i]["costs"]!).Count, Increment = 1, FormatString = "0", Value = target.Counts[i], MinHeight = 40 };
                    Avalonia.Automation.AutomationProperties.SetName(count, ExText.T($"选项 {i + 1}"));
                    count.ValueChanged += (_, _) => { target.Counts[slot] = (int)(count.Value ?? 0); Validate(); };
                    field.Children.Add(count); Grid.SetColumn(field, i); fields.Children.Add(field);
                }
                box.Children.Add(fields); Validate(); box.Children.Add(total);
            }
            else box.Children.Add(new TextBlock { Text = ExText.T("目标配置无法读取，请重新选择。") });
            var row = new Border { Child = box, CornerRadius = new CornerRadius(8), BorderThickness = new Thickness(1), BorderBrush = Brushes.Gray };
            var handle = new Border { Name = "CustomizationDragHandle", MinHeight = 44, Padding = new Thickness(8), Background = Brushes.Transparent,
                Cursor = new Cursor(StandardCursorType.SizeAll), Child = new TextBlock { Text = ExText.T("拖动排序"), VerticalAlignment = VerticalAlignment.Center } };
            Avalonia.Automation.AutomationProperties.SetName(handle, ExText.T("拖动排序") + " " + target.Name);
            box.Children.Insert(0, handle);
            AttachDrag(handle, target);
            _targetRows.Add((row, target)); _body.Children.Add(row);
        }
        _body.Children.Add(_status);
        _footer.Children.Add(Button("添加目标卡", ShowPicker));
        _footer.Children.Add(Button("取消", () => Close(null)));
        _footer.Children.Add(Button("保存目标", () => { try { Close(Serialize()); } catch (Exception e) { _status.Text = e.Message; } }));
    }
    private void AttachDrag(Border handle, CustomizationTarget target)
    {
        Point? origin = null;
        CustomizationTarget? destination = null;
        void Clear()
        {
            origin = null; destination = null;
            foreach (var row in _targetRows) row.View.BorderBrush = Brushes.Gray;
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
            foreach (var row in _targetRows) row.View.BorderBrush = row.Target == destination ? Brushes.DodgerBlue : Brushes.Gray;
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
    public void ShowPicker()
    {
        _body.Children.Clear(); _footer.Children.Clear(); _footer.Children.Add(Button("返回目标设置", ShowTargets));
        var search = new TextBox { Watermark = ExText.T("搜索卡牌名称"), MinHeight = 40 }; _body.Children.Add(search);
        var filters = new WrapPanel { Orientation = Orientation.Horizontal };
        ComboBox FilterBox(string[] items) { var combo = new ComboBox { ItemsSource = items, ItemTemplate = ExText.ItemTemplate, SelectedIndex = 0, Margin = new Thickness(0, 0, 8, 8), MinHeight = 40 }; filters.Children.Add(combo); return combo; }
        var rarity = FilterBox(["全部", "LR", "SSR", "SR", "R", "N"]);
        var plan = FilterBox(["全部计划", "不限", "感性", "理性", "非凡"]);
        var origin = FilterBox(["全部", "通用卡", "角色卡", "支援卡"]);
        var conversion = FilterBox(["全部", "非转换卡", "转换卡"]);
        _body.Children.Add(filters);
        var list = new Grid { Name = "CustomizationPickerGrid", ColumnDefinitions = new ColumnDefinitions("*,*,*,*,*") }; _body.Children.Add(list);
        void Refresh()
        {
            list.Children.Clear(); list.RowDefinitions.Clear(); var index = 0;
            foreach (var card in Filter(search.Text ?? "", rarity.SelectedItem?.ToString() ?? "全部", plan.SelectedIndex, new[] { "全部", "none", "idol", "support" }[Math.Max(0, origin.SelectedIndex)], conversion.SelectedIndex))
            {
                var body = new StackPanel { Spacing = 4 }; body.Children.Add(Preview(card));
                var name = new TextBlock { Text = card.Name, TextWrapping = TextWrapping.Wrap, TextTrimming = TextTrimming.CharacterEllipsis, TextAlignment = TextAlignment.Center, FontSize = 14, Height = 44 };
                body.Children.Add(name);
                body.Children.Add(new TextBlock { Text = card.Rarity, TextAlignment = TextAlignment.Center, FontSize = 12 });
                var button = new Button { Content = body, Height = 165, Margin = new Thickness(4), Padding = new Thickness(6), HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch, IsEnabled = !Targets.Any(t => t.Name == card.Name) };
                button.SizeChanged += (_, _) => name.MaxWidth = Math.Max(20, button.Bounds.Width - 16);
                ToolTip.SetTip(button, card.Name); button.Click += (_, _) => AddCard(card.Name);
                if (index % 5 == 0) list.RowDefinitions.Add(new RowDefinition(GridLength.Auto));
                Grid.SetColumn(button, index % 5); Grid.SetRow(button, index / 5); index++; list.Children.Add(button);
            }
            if (index == 0) list.Children.Add(new TextBlock { Text = ExText.T("没有符合筛选条件的卡牌。") });
        }
        search.TextChanged += (_, _) => Refresh(); foreach (var filter in new[] { rarity, plan, origin, conversion }) filter.SelectionChanged += (_, _) => Refresh(); Refresh(); search.Focus();
    }
}
