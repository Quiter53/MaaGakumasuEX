using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Layout;
using Avalonia.Input;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Styling;
using MFAAvalonia.Helper;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.ViewModels.Pages;
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

// A task-local option: serializes through the same Data/pipeline contract as other inputs.
public static class MemoryTargetControl
{
    public static bool Handles(string? name) => name is "回忆卡选择" or "卡组选择";
    public static Control Create(MaaInterface.MaaInterfaceSelectOption option, Action save, string? root=null)
    {
        root ??= AppPaths.InstallRoot;
        var deck=option.Name=="卡组选择";
        var panel=new StackPanel { Margin=new Thickness(10,6), Spacing=6 };
        var button=new Button { Content=ExText.T(option.Name), HorizontalAlignment=HorizontalAlignment.Stretch, MinHeight=44 };
        var summary=new TextBlock { TextWrapping=TextWrapping.Wrap, FontSize=13 };
        void Refresh()
        {
            try { var items=JsonConvert.DeserializeObject<List<MemoryTarget>>(option.Data.GetValueOrDefault("targets") ?? "[]") ?? [];
                summary.Text=items.Count==0 ? ExText.T("尚未选择目标；至少选择一张牌。") : string.Join("; ",items.Select(t=>$"{t.Name} · {ExText.T(t.Upgrade=="upgraded"?"已强化":t.Upgrade=="base"?"未强化":"强化不限")} · {ExText.T("绿豆至少")} {t.CustomMin}")); }
            catch { summary.Text=ExText.T("目标配置无法读取，请重新选择。"); }
        }
        Refresh();
        button.Click+=async (_,_)=>
        {
            if (TopLevel.GetTopLevel(button) is not Window owner) return;
            var dialog=new MemoryTargetDialog(root,deck,option.Data.GetValueOrDefault("targets") ?? "[]");
            var result=await dialog.ShowDialog<string?>(owner);
            if(result!=null) { option.Data["targets"]=result;save();Refresh(); }
            button.Focus();
        };
        panel.Children.Add(button);panel.Children.Add(summary);return panel;
    }
}

public sealed class MemoryTargetDialog : Window
{
    public List<MemoryTarget> Targets { get; }
    public IReadOnlyList<CardListCard> Cards => _cards;
    private readonly List<CardListCard> _cards;
    private readonly bool _deck;
    private readonly StackPanel _body=new() { Spacing=12 };
    private readonly StackPanel _footer=new() { Orientation=Orientation.Horizontal, Spacing=12, HorizontalAlignment=HorizontalAlignment.Right };
    private readonly TextBlock _status=new() { TextWrapping=TextWrapping.Wrap };
    private readonly Dictionary<string,Bitmap> _images=new();
    private readonly string _root;
    private int _editing;
    public MemoryTargetDialog(string root,bool deck,string saved)
    {
        _root=root;_deck=deck;
        Title=ExText.T(deck?"卡组选择":"回忆卡选择"); Width=920;Height=760;MinWidth=680;MinHeight=540;
        WindowStartupLocation=WindowStartupLocation.CenterOwner;
        void ApplySurfaceTheme()
        {
            var dark=ActualThemeVariant==ThemeVariant.Dark;
            Background=new SolidColorBrush(Color.Parse(dark?"#202225":"#F6F7FA"));
            Foreground=new SolidColorBrush(Color.Parse(dark?"#F2F3F5":"#202225"));
        }
        ActualThemeVariantChanged+=(_,_)=>ApplySurfaceTheme();ApplySurfaceTheme();
        try { Targets=JsonConvert.DeserializeObject<List<MemoryTarget>>(saved) ?? []; }
        catch { Targets=[];_status.Text=ExText.T("原目标配置无法读取，请重新选择后保存。"); }
        Targets=deck ? Enumerable.Range(0,6).Select(i=>Targets.FirstOrDefault(t=>t.Slot==i)?.Copy() ?? new MemoryTarget {Slot=i}).ToList()
            : Targets.Take(64).Select((t,i)=>new MemoryTarget {Slot=i,Name=t.Name,Upgrade=t.Upgrade,CustomMin=t.CustomMin}).ToList();
        if(!deck && Targets.Count==0)Targets.Add(new MemoryTarget {Slot=0});
        try { _cards=JObject.Parse(File.ReadAllText(Path.Combine(root,"config/card_library.json")))["cards"]!.ToObject<List<CardListCard>>()!; }
        catch(Exception e) { _cards=[];_status.Text=ExText.T($"卡库加载失败：{e.Message}"); }
        var layout=new Grid { RowDefinitions=new RowDefinitions("*,Auto"), Margin=new Thickness(20) };
        layout.Children.Add(new ScrollViewer { Content=_body, HorizontalScrollBarVisibility=ScrollBarVisibility.Disabled });
        Grid.SetRow(_footer,1);_footer.Margin=new Thickness(0,12,0,0);layout.Children.Add(_footer);Content=layout;
        KeyDown+=(_,e)=> { if(e.Key==Key.Escape) { Close(null);e.Handled=true; } };
        Closed+=(_,_)=> { foreach(var image in _images.Values)image.Dispose(); };
        ShowSlots();
    }
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
        var grid=new Grid { Width=82,Height=82 };
        var card=_cards.FirstOrDefault(c=>c.Name==target.Name);
        if(card!=null)
        {
            if(!_images.TryGetValue(card.Image,out var bmp))
            { var path=Path.Combine(_root,card.Image);if(File.Exists(path))_images[card.Image]=bmp=new Bitmap(path); }
            if(bmp!=null)grid.Children.Add(new Image { Source=bmp,Stretch=Stretch.Uniform });
        }
        if(target.Upgrade=="upgraded" && card!=null)grid.Children.Add(new TextBlock { Text="+",FontSize=32,FontWeight=FontWeight.Bold,Foreground=Brushes.OrangeRed,HorizontalAlignment=HorizontalAlignment.Right,VerticalAlignment=VerticalAlignment.Top });
        if(target.CustomMin>0)grid.Children.Add(new Border { Background=Brushes.DarkGreen,CornerRadius=new CornerRadius(8),Padding=new Thickness(5,2),HorizontalAlignment=HorizontalAlignment.Right,VerticalAlignment=VerticalAlignment.Bottom,Child=new TextBlock {Text=$"≥{target.CustomMin}",Foreground=Brushes.White} });
        return grid;
    }
    private void ShowSlots()
    {
        _body.Children.Clear();_footer.Children.Clear();_body.Children.Add(new TextBlock {Text=Title,FontSize=23,FontWeight=FontWeight.Bold});
        _body.Children.Add(new TextBlock {Text=ExText.T(_deck?"只需填写需要的卡；同一份回忆包含全部已选目标才算达标。第1格为角色卡，第2格为支援卡，后4格为卡组卡。":"可添加多个目标，逐轮收集，全部保存后停止培育。每张卡分别设置强化和绿豆下限。"),TextWrapping=TextWrapping.Wrap});
        if(!_deck)_body.Children.Add(new TextBlock {Text=ExText.T("保留优先级：目标卡＋钻石词条 ＞ 目标卡 ＞ 非目标卡＋钻石词条。已完成目标再次带钻出现也保留；中断后继续累计进度。"),TextWrapping=TextWrapping.Wrap,FontSize=13});
        var columns=_deck?3:2;
        var slots=new Grid {Name="MemoryTargetsGrid",ColumnDefinitions=new ColumnDefinitions(_deck?"*,*,*":"*,*")};
        for(var i=0;i<(Targets.Count+columns-1)/columns;i++)slots.RowDefinitions.Add(new RowDefinition(GridLength.Auto));
        foreach(var target in Targets)
        {
            var box=new StackPanel {Margin=new Thickness(6),Spacing=4};
            box.Children.Add(new TextBlock {Text=ExText.T(_deck?target.Slot==0?"1 · 角色卡":target.Slot==1?"2 · 支援卡":$"{target.Slot+1} · 卡组卡":$"目标 {target.Slot+1}"),FontWeight=FontWeight.Bold});
            var preview=new Viewbox {Child=Preview(target),Height=58,Stretch=Stretch.Uniform};
            box.Children.Add(preview);
            var choose=new Button {Content=ExText.T(target.Name.Length==0?"选择卡牌":target.Name),MinHeight=40,HorizontalAlignment=HorizontalAlignment.Stretch};
            choose.Click+=(_,_)=>ShowPicker(target.Slot);box.Children.Add(choose);
            var upgrade=new ComboBox { ItemTemplate=ExText.ItemTemplate,ItemsSource=new[]{"不限","未强化","已强化"},SelectedIndex=Array.IndexOf(new[]{"any","base","upgraded"},target.Upgrade),HorizontalAlignment=HorizontalAlignment.Stretch};
            upgrade.SelectionChanged+=(_,_)=> {target.Upgrade=new[]{"any","base","upgraded"}[Math.Max(0,upgrade.SelectedIndex)];preview.Child=Preview(target);};
            var upgradeRow=new Grid {ColumnDefinitions=new ColumnDefinitions("76,*")};
            upgradeRow.Children.Add(new TextBlock {Text=ExText.T("强化状态"),VerticalAlignment=VerticalAlignment.Center});Grid.SetColumn(upgrade,1);upgradeRow.Children.Add(upgrade);box.Children.Add(upgradeRow);
            var count=new NumericUpDown {Minimum=0,Maximum=CustomLimit(target.Name),Increment=1,FormatString="0",Value=target.CustomMin,IsEnabled=target.Name.Length>0};
            count.ValueChanged+=(_,_)=> {target.CustomMin=(int)(count.Value??0);preview.Child=Preview(target);};
            var countRow=new Grid {ColumnDefinitions=new ColumnDefinitions("76,*")};
            countRow.Children.Add(new TextBlock {Text=ExText.T("绿豆至少"),VerticalAlignment=VerticalAlignment.Center});Grid.SetColumn(count,1);countRow.Children.Add(count);box.Children.Add(countRow);
            box.Children.Add(new TextBlock {Text=ExText.T($"0 为不限 · 上限 {CustomLimit(target.Name)}"),FontSize=12});
            var clear=new Button {Content=ExText.T(_deck?"清空此格":"移除目标"),MinHeight=36};clear.Click+=(_,_)=> {if(!_deck){RemoveTarget(target.Slot);return;}target.Name="";target.CustomMin=0;ShowSlots();};box.Children.Add(clear);
            Grid.SetColumn(box,target.Slot%columns);Grid.SetRow(box,target.Slot/columns);slots.Children.Add(box);
        }
        _body.Children.Add(slots);_body.Children.Add(_status);
        if(!_deck){var add=new Button {Content=ExText.T("添加目标卡"),MinHeight=42,IsEnabled=Targets.Count<64};add.Click+=(_,_)=>AddTarget();_footer.Children.Add(add);}
        var buttons=new StackPanel {Orientation=Orientation.Horizontal,Spacing=12,HorizontalAlignment=HorizontalAlignment.Right};
        var cancel=new Button {Content=ExText.T("取消"),MinWidth=90,MinHeight=42};cancel.Click+=(_,_)=>Close(null);
        var save=new Button {Content=ExText.T("保存目标"),MinWidth=110,MinHeight=42};save.Click+=(_,_)=> {try {Close(Serialize());}catch(Exception e){_status.Text=ExText.T(e.Message);}};
        buttons.Children.Add(cancel);buttons.Children.Add(save);_footer.Children.Add(buttons);
    }
    public void ShowPicker(int slot)
    {
        _editing=slot;_body.Children.Clear();_footer.Children.Clear();
        var back=new Button {Content=ExText.T("返回目标设置"),MinHeight=40};back.Click+=(_,_)=>ShowSlots();_body.Children.Add(back);
        var search=new TextBox {Watermark=ExText.T("搜索卡牌名称"),MinHeight=40};_body.Children.Add(search);
        var filters=new StackPanel {Orientation=Orientation.Horizontal,Spacing=8};
        var rarity=new ComboBox { ItemTemplate=ExText.ItemTemplate,ItemsSource=new[]{"全部","LR","SSR","SR","R","N"}.Where(r=>r=="全部" || Filter(slot).Any(c=>c.Rarity==r)).ToArray(),SelectedIndex=0};
        var plan=new ComboBox { ItemTemplate=ExText.ItemTemplate,ItemsSource=new[]{"全部计划","不限","感性","理性","非凡"},SelectedIndex=0};
        filters.Children.Add(rarity);filters.Children.Add(plan);_body.Children.Add(filters);
        var list=new Grid {Name="MemoryPickerGrid",ColumnDefinitions=new ColumnDefinitions("*,*,*,*,*"),HorizontalAlignment=HorizontalAlignment.Stretch};_body.Children.Add(list);
        void Refresh()
        {
            list.Children.Clear();list.RowDefinitions.Clear();var index=0;
            foreach(var card in Filter(_editing,search.Text??"",rarity.SelectedItem?.ToString()??"全部",plan.SelectedIndex))
            {
                var item=new StackPanel {Spacing=4,HorizontalAlignment=HorizontalAlignment.Stretch};item.Children.Add(Preview(new MemoryTarget {Name=card.Name,Upgrade="any"}));
                var name=new TextBlock {Text=card.Name,TextWrapping=TextWrapping.Wrap,TextAlignment=TextAlignment.Center,FontSize=14,Height=42,TextTrimming=TextTrimming.CharacterEllipsis};
                item.Children.Add(name);
                item.Children.Add(new TextBlock {Text=card.Rarity,FontSize=12,TextAlignment=TextAlignment.Center});
                var button=new Button {Content=item,Margin=new Thickness(4),Padding=new Thickness(6),Height=165,HorizontalAlignment=HorizontalAlignment.Stretch,HorizontalContentAlignment=HorizontalAlignment.Stretch};
                button.SizeChanged+=(_,_)=>name.MaxWidth=Math.Max(20,button.Bounds.Width-16);
                ToolTip.SetTip(button,card.Name);
                if(index%5==0)list.RowDefinitions.Add(new RowDefinition(GridLength.Auto));
                Grid.SetColumn(button,index%5);Grid.SetRow(button,index/5);index++;
                button.Click+=(_,_)=> {Select(_editing,card);ShowSlots();};list.Children.Add(button);
            }
            if(list.Children.Count==0)list.Children.Add(new TextBlock {Text=ExText.T("没有符合筛选条件的卡牌。")});
        }
        search.TextChanged+=(_,_)=>Refresh();rarity.SelectionChanged+=(_,_)=>Refresh();plan.SelectionChanged+=(_,_)=>Refresh();Refresh();search.Focus();
    }
}
