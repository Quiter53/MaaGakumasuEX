using MFAAvalonia.Helper;
using System;
using System.Linq;
using Avalonia;
using Avalonia.Automation;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Input;
using Avalonia.Layout;
using Avalonia.Media;
using MFAAvalonia.ViewModels.Pages;
namespace MFAAvalonia.Views.Pages;

public sealed partial class CardRulesView
{
    private void ShowDialog(string title,Control body,Control footer,int width=620)
    {
        CloseDialog();_modalOpener=TopLevel.GetTopLevel(this)?.FocusManager?.GetFocusedElement() as Control;
        var panel=new Grid { RowDefinitions=new RowDefinitions("Auto,*,Auto"),MaxWidth=width };
        var header=new Grid { ColumnDefinitions=new ColumnDefinitions("*,Auto"),Margin=new Thickness(24,18,24,16) };header.Children.Add(Text(title,21,true));var close=Button("×",CloseDialog);AutomationProperties.SetName(close,"关闭弹窗");Grid.SetColumn(close,1);header.Children.Add(close);panel.Children.Add(header);
        var scroll=new ScrollViewer { Content=body,Margin=new Thickness(24,0,24,16),HorizontalScrollBarVisibility=ScrollBarVisibility.Disabled };Grid.SetRow(scroll,1);panel.Children.Add(scroll);
        var foot=new Border { Child=footer,Padding=new Thickness(24,14),Background=Soft,BorderBrush=Line,BorderThickness=new Thickness(0,1,0,0) };Grid.SetRow(foot,2);panel.Children.Add(foot);
        _modal=new Border { Child=panel,Background=Panel,CornerRadius=new CornerRadius(18),BorderBrush=Line,BorderThickness=new Thickness(1),Width=width,MaxWidth=Math.Max(280,Bounds.Width-40),MaxHeight=Math.Max(260,Bounds.Height-40),HorizontalAlignment=HorizontalAlignment.Center,VerticalAlignment=VerticalAlignment.Center };
        _root.Children.Add(new Border { Background=Brush.Parse("#80172942"),Child=_modal });_page.IsEnabled=false;KeyboardNavigation.SetTabNavigation(_modal,KeyboardNavigationMode.Cycle);close.Focus();
    }
    private void CloseDialog(){if(_root.Children.Count>1)_root.Children.RemoveAt(1);_modal=null;_page.IsEnabled=true;_modalOpener?.Focus();}
    private void Notice(string message)=>ShowDialog("操作提示",Text(message),Button("确定",CloseDialog,true));
    private void Confirm(string message,Action action)=>ShowDialog("确认操作",Text(message),Row(Button("取消",CloseDialog),Button("确认",()=>{CloseDialog();action();},true)));
    public void OpenPicker(int? slot=null,Action<string>? onPicked=null)
    {
        string? picked=null;var search=Input("搜索日文卡牌名称，例如：届いて！、せのびの魔法");
        var result=new WrapPanel { Orientation=Orientation.Horizontal };var count=Text("",12,false,Muted);var selected=Text("从本地卡牌库中选择",12,false,Muted);
        var create=Button(onPicked!=null||slot.HasValue?"使用此卡":"创建规则",()=>{if(picked==null)return;if(onPicked!=null){CloseDialog();onPicked(picked);}else if(slot.HasValue){Model.SetHand(slot.Value,picked);CloseDialog();RenderPreview();}else{Model.AddRule(picked);CloseDialog();Build();}},true);create.IsEnabled=false;
        var filters=new[]{Select("稀有度",new[]{"全部稀有度","LR","SSR","SR","R","N"},0),Select("计划",new[]{"全部计划","不限","感性","理性","非凡"},0),Select("技能分类",new[]{"全部分类","主动技能","心智技能","负面卡牌"},0),Select("来源",new[]{"全部来源","普通","角色卡","支援卡"},0),Select("转换",new[]{"全部转换","非转换卡","转换卡"},0),Select("再动",new[]{"全部再动属性","再动","非再动"},0),Select("解锁等级",new[]{"不限解锁等级","初始解锁","1–20 级","21–40 级","41–60 级","61 级及以上"},0),Select("排列方式",new[]{"默认顺序","等级升序","等级降序"},0)};
        void Render()
        {
            result.Children.Clear();var f=filters.Select(c=>Math.Max(0,c.SelectedIndex)).ToArray();var cards=Model.Filter(search.Text??"",f[0],f[1],f[2],f[3],f[4],f[5],f[6],f[7]);count.Text=ExText.T($"卡牌库 · {cards.Count} / {Model.Cards.Count} 张");
            foreach(var c in cards)
            {
                if(onPicked!=null && PlayCardPolicy.Key(c.Name)=="国民的アイドル")continue;
                var button=Button(c.Name,()=>{picked=c.Name;selected.Text=c.Name;create.IsEnabled=onPicked!=null||slot.HasValue||!Model.Preset.Rules.Any(r=>r.Card==c.Name);Render();});button.Height=double.NaN;button.MinHeight=84;button.Width=265;button.Margin=new Thickness(0,0,8,8);button.HorizontalContentAlignment=HorizontalAlignment.Left;button.BorderBrush=c.Name==picked?Accent:Line;
                string[] plans=["","不限","感性","理性","非凡"],categories=["","主动技能","心智技能","负面卡牌"];
                button.Content=Row(Art(c,40),new StackPanel { MaxWidth=190,Spacing=3,Children={Text(c.Name,12,true),Text($"{c.Rarity} · {plans[Math.Clamp(c.PlanType,0,4)]} · {categories[Math.Clamp(c.Category,0,3)]}",10,false,Muted),Text($"{(c.Extra?"再动 · ":"")}{(c.IsConversion?"转换卡 · ":"")}Lv.{c.UnlockProducerLevel}",10,false,Muted)} });result.Children.Add(button);
            }
        }
        var body=new StackPanel { Spacing=12,Children={search} };
        if(slot.HasValue)
        {
            var quick=new StackPanel { Spacing=8 };quick.Children.Add(Text($"当前预设已有规则 · {Model.Preset.Rules.Count}",13,true));quick.Children.Add(Text($"点击直接放入槽位 {slot+1}",11,false,Muted));var tiles=new WrapPanel { Orientation=Orientation.Horizontal };
            foreach(var r in Model.Preset.Rules)
            {
                var c=Model.Card(r.Card);var b=Button(c.Name,()=>{Model.SetHand(slot.Value,c.Name);CloseDialog();RenderPreview();});b.Height=double.NaN;b.MinHeight=60;b.Margin=new Thickness(0,0,8,4);b.Content=Row(Art(c,32),new StackPanel { Spacing=3,Children={Text(c.Name,12,true),Text(PlayCardPolicy.Tiers[PlayCardPolicy.EffectiveTier(c.Rarity,r)]+" · "+ExText.T(!r.Enabled?"规则已停用":r.Limited?$"剩余 {r.Min}–{r.Max} 回合":"不限回合"),10,false,Muted)} });tiles.Children.Add(b);
            }
            if(tiles.Children.Count==0)tiles.Children.Add(Text("当前预设还没有规则，可从下方卡牌库选牌。",12,false,Muted));quick.Children.Add(tiles);body.Children.Add(Box(quick,12));
        }
        var filterPanel=new WrapPanel { Orientation=Orientation.Horizontal };string[] labels=["稀有度","计划","技能分类","来源","转换","再动","解锁等级","排列方式"];
        for(int i=0;i<filters.Length;i++){filterPanel.Children.Add(new StackPanel { Width=195,Spacing=4,Margin=new Thickness(0,0,12,8),Children={Text(labels[i],11,false,Muted),filters[i]} });filters[i].SelectionChanged+=(_,_)=>Render();}
        search.TextChanged+=(_,_)=>Render();body.Children.Add(filterPanel);body.Children.Add(Row(count,Button("重置筛选",()=>{search.Text="";foreach(var f in filters)f.SelectedIndex=0;Render();})));
        body.Children.Add(new ScrollViewer { Content=result,Height=330,HorizontalScrollBarVisibility=ScrollBarVisibility.Disabled });Render();ShowDialog(onPicked!=null?"选择要生效的牌":slot.HasValue?$"选择预览手牌 · 槽位 {slot+1}":"为一张卡新建规则",body,Row(selected,create),940);search.Focus();
    }
}
