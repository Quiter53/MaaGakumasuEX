using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.Helper;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.Views.Pages;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Views.UserControls;

public sealed class MemoryTarget
{
    [JsonProperty("name")] public string Name { get; set; } = "";
    [JsonProperty("slot")] public int Slot { get; set; }
    [JsonProperty("upgrade")] public string Upgrade { get; set; } = "upgraded";
    [JsonProperty("customMin")] public int CustomMin { get; set; }
    public MemoryTarget Copy() => new() { Name=Name, Slot=Slot, Upgrade=Upgrade, CustomMin=CustomMin };
}

/// <summary>Shared look of the target option rows in the task settings (memory, deck, customization).</summary>
internal static class TargetOptionRow
{
    public static Control Create(string title, Func<IReadOnlyList<(string Name, Bitmap? Image)>> preview, Func<string> summary, Func<Task> edit)
    {
        var thumbs = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
        var text = new TextBlock { TextWrapping = TextWrapping.Wrap, FontSize = 12, Classes = { "lg-sub" } };
        var button = new Button { Classes = { "lg-sm" }, Content = ExText.T("编辑目标"), VerticalAlignment = VerticalAlignment.Center };
        Avalonia.Automation.AutomationProperties.SetName(button, ExText.T(title));
        void Refresh()
        {
            thumbs.Children.Clear();
            foreach (var (name, image) in preview().Take(6))
            {
                var t = new Border { Width = 34, Height = 34, CornerRadius = new CornerRadius(9), ClipToBounds = true, Child = new Image { Source = image, Stretch = Stretch.UniformToFill } };
                ToolTip.SetTip(t, name);
                thumbs.Children.Add(t);
            }
            thumbs.IsVisible = thumbs.Children.Count > 0;
            text.Text = summary();
        }
        button.Click += async (_, _) => { await edit(); Refresh(); button.Focus(); };
        Refresh();
        var head = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), ColumnSpacing = 12 };
        head.Children.Add(new TextBlock { Text = ExText.T(title), FontSize = 13, FontWeight = FontWeight.SemiBold, VerticalAlignment = VerticalAlignment.Center });
        Grid.SetColumn(button, 1); head.Children.Add(button);
        return new StackPanel { Margin = new Thickness(10, 6, 4, 6), Spacing = 8, Children = { head, thumbs, text } };
    }

    /// <summary>Opens <paramref name="editor"/> in a glass dialog and completes with its result (null = cancelled).</summary>
    public static Task<string?> ShowAsync(Visual anchor, ITargetEditor editor, double width = 920)
    {
        var done = new TaskCompletionSource<string?>();
        var modal = LiquidModal.Show(anchor, (Control)editor, width, 760);
        if (modal == null) { done.SetResult(null); return done.Task; }
        editor.Finished += result => { modal.Close(); done.TrySetResult(result); };
        modal.Cancel = () => { modal.Close(); done.TrySetResult(null); };
        return done.Task;
    }
}

/// <summary>Editor contract: draft editing, <see cref="Finished"/> with JSON on save or null on cancel.</summary>
public interface ITargetEditor
{
    event Action<string?>? Finished;
}

// A task-local option: serializes through the same Data/pipeline contract as other inputs.
public static class MemoryTargetControl
{
    public static bool Handles(string? name) => name is "回忆卡选择" or "卡组选择";
    public static Control Create(MaaInterface.MaaInterfaceSelectOption option, Action save, string? root=null)
    {
        root ??= AppPaths.InstallRoot;
        var deck=option.Name=="卡组选择";
        Control? row=null;
        List<CardListCard>? cards=null;
        List<MemoryTarget> Current() { try { return JsonConvert.DeserializeObject<List<MemoryTarget>>(option.Data.GetValueOrDefault("targets") ?? "[]") ?? []; } catch { return []; } }
        string Summary()
        {
            try
            {
                var items=JsonConvert.DeserializeObject<List<MemoryTarget>>(option.Data.GetValueOrDefault("targets") ?? "[]") ?? [];
                return items.Count==0 ? ExText.T("尚未选择目标；至少选择一张牌。") : string.Join("; ",items.Select(t=>$"{t.Name} · {ExText.T(t.Upgrade=="upgraded"?"已强化":t.Upgrade=="base"?"未强化":"强化不限")} · {ExText.T("绿豆至少")} {t.CustomMin}"));
            }
            catch { return ExText.T("目标配置无法读取，请重新选择。"); }
        }
        IReadOnlyList<(string, Bitmap?)> Preview()
        {
            cards ??= LoadCards(root);
            return Current().Where(t=>t.Name.Length>0).Select(t=>(t.Name, cards.FirstOrDefault(c=>c.Name==t.Name) is {} c ? CardImages.Get(root,c) : null)).ToList();
        }
        row=TargetOptionRow.Create(option.Name ?? "", Preview, Summary, async ()=>
        {
            var editor=new MemoryTargetDialog(root,deck,option.Data.GetValueOrDefault("targets") ?? "[]");
            var result=await TargetOptionRow.ShowAsync(row!, editor);
            if(result!=null) { option.Data["targets"]=result;save(); }
        });
        return row;
    }

    internal static List<CardListCard> LoadCards(string root)
    {
        try { return JObject.Parse(File.ReadAllText(Path.Combine(root,"config/card_library.json")))["cards"]!.ToObject<List<CardListCard>>()!; }
        catch { return []; }
    }
}

/// <summary>Thumbnail cache shared by the target editors (decoded once at 112 px).</summary>
internal static class CardImages
{
    private static readonly Dictionary<string, Bitmap?> Cache = new();
    public static Bitmap? Get(string root, CardListCard card)
    {
        if (Cache.TryGetValue(card.Image, out var image)) return image;
        try
        {
            var path = Path.Combine(root, card.Image.Replace('/', Path.DirectorySeparatorChar));
            if (File.Exists(path)) { using var s = File.OpenRead(path); image = Bitmap.DecodeToWidth(s, 112); }
        }
        catch { image = null; }
        Cache[card.Image] = image;
        return image;
    }
}

/// <summary>回忆卡 / 卡组目标编辑器（草稿编辑，保存才写回任务配置）。</summary>
public sealed class MemoryTargetDialog : UserControl, ITargetEditor
{
    public event Action<string?>? Finished;
    public string Title { get; }
    public List<MemoryTarget> Targets { get; }
    public IReadOnlyList<CardListCard> Cards => _cards;
    private readonly List<CardListCard> _cards;
    private readonly bool _deck;
    private readonly StackPanel _body=new() { Spacing=12 };
    private readonly StackPanel _footer=new() { Orientation=Orientation.Horizontal, Spacing=8, HorizontalAlignment=HorizontalAlignment.Right };
    private readonly TextBlock _status=new() { TextWrapping=TextWrapping.Wrap, Classes={ "lg-danger" } };
    private readonly string _root;

    public MemoryTargetDialog(string root,bool deck,string saved)
    {
        _root=root;_deck=deck;
        Title=ExText.T(deck?"卡组选择":"回忆卡选择");
        try { Targets=JsonConvert.DeserializeObject<List<MemoryTarget>>(saved) ?? []; }
        catch { Targets=[];_status.Text=ExText.T("原目标配置无法读取，请重新选择后保存。"); }
        Targets=deck ? Enumerable.Range(0,6).Select(i=>Targets.FirstOrDefault(t=>t.Slot==i)?.Copy() ?? new MemoryTarget {Slot=i}).ToList()
            : Targets.Take(64).Select((t,i)=>new MemoryTarget {Slot=i,Name=t.Name,Upgrade=t.Upgrade,CustomMin=t.CustomMin}).ToList();
        if(!deck && Targets.Count==0)Targets.Add(new MemoryTarget {Slot=0});
        _cards=MemoryTargetControl.LoadCards(root);
        if(_cards.Count==0)_status.Text=ExText.T("卡库加载失败");
        var layout=new Grid { RowDefinitions=new RowDefinitions("Auto,*,Auto"), RowSpacing=14 };
        var head=new Grid { ColumnDefinitions=new ColumnDefinitions("*,Auto") };
        head.Children.Add(new TextBlock { Text=Title, FontSize=16, FontWeight=FontWeight.Bold });
        var close=new Button { Classes={ "lg-icon","lg-sm" }, Content=new LiquidIcon { Kind="x", Size=13 } };
        Avalonia.Automation.AutomationProperties.SetName(close,ExText.T("关闭"));
        close.Click+=(_,_)=>Close(null);Grid.SetColumn(close,1);head.Children.Add(close);
        layout.Children.Add(head);
        var scroll=new ScrollViewer { Content=_body, HorizontalScrollBarVisibility=ScrollBarVisibility.Disabled, Padding=new Thickness(0,0,6,0) };
        Grid.SetRow(scroll,1);layout.Children.Add(scroll);
        Grid.SetRow(_footer,2);layout.Children.Add(_footer);Content=layout;
        ShowSlots();
    }

    public void Close(string? result) => Finished?.Invoke(result);

    public IEnumerable<CardListCard> Filter(int slot,string search="",string rarity="全部",int plan=0,string origin="全部")
    {
        var required=_deck ? (slot==0?"idol":slot==1?"support":"none") : "none";
        return _cards.Where(c=>c.Origin==required && (required!="none" || c.Rarity!="LR") && (rarity=="全部" || c.Rarity==rarity)
            && (plan==0 || c.PlanType==plan) && (origin=="全部" || c.Origin==origin)
            && c.Name.Contains(search,StringComparison.OrdinalIgnoreCase));
    }
    public bool Select(int slot,CardListCard card)
    {
        if(!Filter(slot).Any(c=>c.Key==card.Key)) return false;
        Targets[slot].Name=card.Name;Targets[slot].CustomMin=Math.Min(Targets[slot].CustomMin,CustomLimit(card.Name));return true;
    }
    public int CustomLimit(string name) => _cards.FirstOrDefault(c=>c.Name==name)?.Rarity switch { "SSR"=>1,"SR" or "R"=>2,_=>0 };
    public string Serialize()
    {
        if(!Targets.Any(t=>t.Name.Length>0)) throw new InvalidDataException("至少选择一张目标卡。");
        if(Targets.Where(t=>t.Name.Length>0).Any(t=>!Filter(t.Slot).Any(c=>c.Name==t.Name))) throw new InvalidDataException("请重新选择目标：回忆卡和卡组后4格不允许 LR、角色卡或支援卡；前2格仅限对应来源。");
        if(Targets.Where(t=>t.Name.Length>0).Any(t=>t.CustomMin<0 || t.CustomMin>CustomLimit(t.Name) || !Filter(t.Slot).Any(c=>c.Name==t.Name) || !new[]{"any","base","upgraded"}.Contains(t.Upgrade))) throw new InvalidDataException("目标条件无效：R / SR 绿豆最多 2，SSR 最多 1。");
        var selected=Targets.Where(t=>t.Name.Length>0).ToList();
        if(!_deck && selected.GroupBy(t=>new {t.Name,t.Upgrade,t.CustomMin}).Any(g=>g.Count()>1))throw new InvalidDataException("存在条件完全相同的重复目标，请移除重复项。");
        return JsonConvert.SerializeObject(selected);
    }
    public bool AddTarget()
    {
        if(_deck || Targets.Count>=64)return false;
        Targets.Add(new MemoryTarget {Slot=Targets.Count});ShowSlots();return true;
    }
    public void RemoveTarget(int slot)
    {
        if(_deck)return;
        Targets.RemoveAt(slot);
        for(var i=0;i<Targets.Count;i++)Targets[i].Slot=i;
        if(Targets.Count==0)Targets.Add(new MemoryTarget {Slot=0});
        ShowSlots();
    }
    private Control Preview(MemoryTarget target)
    {
        var grid=new Grid { Width=64,Height=64 };
        var card=_cards.FirstOrDefault(c=>c.Name==target.Name);
        grid.Children.Add(new Border
        {
            CornerRadius=new CornerRadius(14), ClipToBounds=true, BorderThickness=new Thickness(card==null?1:2),
            BorderBrush=card==null?null:CardCatalogFilter.RarityBrush(card.Rarity), Classes={ "lg-target-slot" },
            Child=card==null?new LiquidIcon { Kind="plus", Size=18, HorizontalAlignment=HorizontalAlignment.Center, VerticalAlignment=VerticalAlignment.Center }
                : new Image { Source=CardImages.Get(_root,card), Stretch=Stretch.UniformToFill }
        });
        if(target.Upgrade=="upgraded" && card!=null)grid.Children.Add(new Border { Classes={ "lg-badge","warning" }, HorizontalAlignment=HorizontalAlignment.Right, VerticalAlignment=VerticalAlignment.Top, Margin=new Thickness(0,-6,-6,0), Child=new TextBlock { Text="+" } });
        if(target.CustomMin>0)grid.Children.Add(new Border { Classes={ "lg-badge","success" }, HorizontalAlignment=HorizontalAlignment.Right, VerticalAlignment=VerticalAlignment.Bottom, Margin=new Thickness(0,0,-6,-6), Child=new TextBlock {Text=$"≥{target.CustomMin}"} });
        return grid;
    }
    public void ShowSlots()
    {
        _body.Children.Clear();_footer.Children.Clear();
        _body.Children.Add(new TextBlock {Text=ExText.T(_deck?"只需填写需要的卡；同一份回忆包含全部已选目标才算达标。第1格为角色卡，第2格为支援卡，后4格为卡组卡。":"可添加多个目标，逐轮收集，全部保存后停止培育。每张卡分别设置强化和绿豆下限。"),TextWrapping=TextWrapping.Wrap,Classes={ "lg-sub" }});
        if(!_deck)_body.Children.Add(new TextBlock {Text=ExText.T("保留优先级：目标卡＋钻石词条 ＞ 目标卡 ＞ 非目标卡＋钻石词条。已完成目标再次带钻出现也保留；中断后继续累计进度。"),TextWrapping=TextWrapping.Wrap,Classes={ "lg-muted" }});
        var slots=new RowGridPanel { Name="MemoryTargetsGrid", Columns=_deck?3:2, MinColumnWidth=250, Spacing=12 };
        foreach(var target in Targets)
        {
            var box=new StackPanel {Spacing=10};
            var title=new TextBlock {Text=ExText.T(_deck?target.Slot==0?"1 · 角色卡":target.Slot==1?"2 · 支援卡":$"{target.Slot+1} · 卡组卡":$"目标 {target.Slot+1}"),FontWeight=FontWeight.SemiBold,FontSize=13};
            var preview=new ContentControl { Content=Preview(target) };
            var choose=new Button {Classes={ "lg-sm" }, Content=ExText.T(target.Name.Length==0?"选择卡牌":target.Name),HorizontalAlignment=HorizontalAlignment.Stretch};
            Avalonia.Automation.AutomationProperties.SetName(choose,ExText.T("选择卡牌"));
            choose.Click+=(_,_)=>ShowPicker(target.Slot);
            var top=new Grid { ColumnDefinitions=new ColumnDefinitions("Auto,*"), ColumnSpacing=12, Children={ preview } };
            var right=new StackPanel { Spacing=8, VerticalAlignment=VerticalAlignment.Center, Children={ title, choose } };
            Grid.SetColumn(right,1);top.Children.Add(right);box.Children.Add(top);
            var upgrade=new SegmentedControl { Classes={ "lg-sm" }, ItemsSource=new[]{ExText.T("不限"),ExText.T("未强化"),ExText.T("已强化")},SelectedIndex=Math.Max(0,Array.IndexOf(new[]{"any","base","upgraded"},target.Upgrade)),HorizontalAlignment=HorizontalAlignment.Right};
            Avalonia.Automation.AutomationProperties.SetName(upgrade,ExText.T("强化状态"));
            upgrade.SelectionChanged+=(_,_)=> {target.Upgrade=new[]{"any","base","upgraded"}[Math.Max(0,upgrade.SelectedIndex)];preview.Content=Preview(target);};
            var upgradeRow=new Grid {ColumnDefinitions=new ColumnDefinitions("*,Auto")};
            upgradeRow.Children.Add(new TextBlock {Text=ExText.T("强化状态"),VerticalAlignment=VerticalAlignment.Center,FontSize=12.5});Grid.SetColumn(upgrade,1);upgradeRow.Children.Add(upgrade);box.Children.Add(upgradeRow);
            var count=new NumericUpDown {Minimum=0,Maximum=CustomLimit(target.Name),Increment=1,FormatString="0",Value=target.CustomMin,IsEnabled=target.Name.Length>0,Width=120};
            Avalonia.Automation.AutomationProperties.SetName(count,ExText.T("绿豆至少"));
            count.ValueChanged+=(_,_)=> {target.CustomMin=(int)(count.Value??0);preview.Content=Preview(target);};
            var countRow=new Grid {ColumnDefinitions=new ColumnDefinitions("*,Auto")};
            countRow.Children.Add(new StackPanel { VerticalAlignment=VerticalAlignment.Center, Children={ new TextBlock {Text=ExText.T("绿豆至少"),FontSize=12.5}, new TextBlock {Text=ExText.T($"0 为不限 · 上限 {CustomLimit(target.Name)}"),FontSize=11,Classes={ "lg-muted" }} } });
            Grid.SetColumn(count,1);countRow.Children.Add(count);box.Children.Add(countRow);
            var clear=new Button {Classes={ "lg-sm","lg-danger-text" }, Content=ExText.T(_deck?"清空此格":"移除目标"),HorizontalAlignment=HorizontalAlignment.Right};
            clear.Click+=(_,_)=> {if(!_deck){RemoveTarget(target.Slot);return;}target.Name="";target.CustomMin=0;ShowSlots();};box.Children.Add(clear);
            slots.Children.Add(new GlassPanel { Classes={ "box" }, Padding=new Thickness(14), Content=box });
        }
        _body.Children.Add(slots);_body.Children.Add(_status);
        if(!_deck){var add=new Button {Classes={ "lg-sm" }, Content=ExText.T("添加目标卡"),IsEnabled=Targets.Count<64};add.Click+=(_,_)=>AddTarget();_footer.Children.Add(add);}
        var cancel=new Button {Content=ExText.T("取消"),MinWidth=88};cancel.Click+=(_,_)=>Close(null);
        var save=new Button {Classes={ "lg-primary" }, Content=ExText.T("保存目标"),MinWidth=100};save.Click+=(_,_)=> {try {Close(Serialize());}catch(Exception e){_status.Text=ExText.T(e.Message);}};
        _footer.Children.Add(cancel);_footer.Children.Add(save);
    }
    /// <summary>Card choice for one slot through the shared picker, limited to the slot's allowed source.</summary>
    public void ShowPicker(int slot)
    {
        var allowed=Filter(slot).Select(c=>c.Key).ToHashSet();
        LiquidCardPicker.Open(this, new CardPickerOptions
        {
            Cards=_cards, Image=c=>CardImages.Get(_root,c), Title="选择目标卡牌",
            Subtitle=_deck ? (slot==0?"第 1 格仅限角色卡":slot==1?"第 2 格仅限支援卡":"卡组卡不含 LR、角色卡和支援卡") : "回忆目标不含 LR、角色卡和支援卡",
            Eligible=c=>allowed.Contains(c.Key),
            Initial=_cards.FirstOrDefault(c=>c.Name==Targets[slot].Name),
            Confirm=card=>{ Select(slot,card);ShowSlots(); },
        });
    }
}
