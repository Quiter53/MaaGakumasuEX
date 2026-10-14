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
using Avalonia.Styling;
using MFAAvalonia.ViewModels.Pages;
namespace MFAAvalonia.Views.Pages;

/// <summary>Native Avalonia controls, drawing and overlays; no browser dependency.</summary>
public sealed partial class CardRulesView : UserControl
{
    public CardRulesViewModel Model { get; }
    private readonly Grid _root=new();
    private readonly StackPanel _page=new() { Spacing=20,Margin=new Thickness(28,24) };
    private readonly StackPanel _list=new() { Spacing=10 },_right=new() { Spacing=18 },_left=new() { Spacing=12 };
    private readonly StackPanel _editor=new() { Spacing=16 },_nodes=new(),_preview=new() { Spacing=12 };
    private readonly TextBlock _status=new(),_curveStatus=new(),_error=new();
    private readonly Grid _workspace=new(),_heading=new();
    private readonly WrapPanel _hand=new() { Orientation=Orientation.Horizontal };
    private Border? _editorBox,_previewBox,_modal;
    private Control? _modalOpener;
    private string _search="";
    private bool _handExpanded,_building;
    private CardRuleCurve? _curve;
    private bool Dark => ActualThemeVariant==ThemeVariant.Dark;
    private IBrush Panel => Brush.Parse(Dark?"#202733":"#FFFFFF");
    private IBrush Soft => Brush.Parse(Dark?"#252E3D":"#F8F9FC");
    private IBrush Line => Brush.Parse(Dark?"#364152":"#E7EAF0");
    private IBrush Accent => Brush.Parse(Dark?"#73A3FF":"#2463EB");
    private IBrush Muted => Brush.Parse(Dark?"#A4B0C3":"#667085");
    private IBrush Ink => Brush.Parse(Dark?"#E4E9F2":"#202938");
    public CardRulesView():this(new CardRulesViewModel()) { }
    public CardRulesView(string root):this(new CardRulesViewModel(root)) { }
    public CardRulesView(CardRulesViewModel model)
    {
        Model=model;DataContext=model;FontFamily=new FontFamily("Segoe UI, Microsoft YaHei UI");FontSize=14;
        _root.Children.Add(new ScrollViewer { Content=_page,HorizontalScrollBarVisibility=ScrollBarVisibility.Disabled });Content=_root;
        Model.PropertyChanged+=(_,e)=>{if(e.PropertyName==nameof(Model.Status))_status.Text=Model.Status;};
        ActualThemeVariantChanged+=(_,_)=>{CloseDialog();Build();};SizeChanged+=(_,_)=>Responsive();
        AddHandler(KeyDownEvent,(_,e)=>{if(e.Key==Key.Escape&&_modal!=null){CloseDialog();e.Handled=true;}},Avalonia.Interactivity.RoutingStrategies.Tunnel);
        Build();
    }
    private TextBlock Text(string value,double size=14,bool bold=false,IBrush? color=null) => new() { Text=value,FontSize=size,FontWeight=bold?FontWeight.SemiBold:FontWeight.Normal,Foreground=color??Ink,TextWrapping=TextWrapping.Wrap,VerticalAlignment=VerticalAlignment.Center };
    private StackPanel Row(params Control[] children) { var row=new StackPanel { Orientation=Orientation.Horizontal,Spacing=9,VerticalAlignment=VerticalAlignment.Center };foreach(var c in children)row.Children.Add(c);return row; }
    private Button Button(string label,Action click,bool primary=false)
    {
        var b=new Button { Content=label,MinHeight=36,Padding=new Thickness(12,7),CornerRadius=new CornerRadius(9),BorderThickness=new Thickness(1),BorderBrush=primary?Accent:Line,Background=primary?Brush.Parse("#2463EB"):Panel,Foreground=primary?Brushes.White:Ink,VerticalContentAlignment=VerticalAlignment.Center };
        AutomationProperties.SetName(b,label);b.Click+=(_,_)=>click();return b;
    }
    private Border Box(Control child,int padding=20) => new() { Child=child,Padding=new Thickness(padding),CornerRadius=new CornerRadius(16),Background=Panel,BorderBrush=Line,BorderThickness=new Thickness(1) };
    private Border Badge(string value) => new() { Child=Text(value,11,true,Accent),Padding=new Thickness(7,3),CornerRadius=new CornerRadius(5),Background=Brush.Parse(Dark?"#293B5B":"#EDF3FF") };
    private Image Art(CardListCard card,int width=44) => new() { Source=Model.Image(card),Width=width,Height=width*1.24,Stretch=Stretch.UniformToFill };
    private TextBox Input(string name,string value="") { var input=new TextBox { Watermark=name,Text=value,MinHeight=36 };AutomationProperties.SetName(input,name);return input; }
    private ComboBox Select(string name,IEnumerable<string> values,int selected) { var c=new ComboBox { ItemsSource=values.ToArray(),SelectedIndex=selected,MinHeight=36,MinWidth=120,HorizontalAlignment=HorizontalAlignment.Stretch };AutomationProperties.SetName(c,name);return c; }
    private NumericUpDown Number(string name,int value,int min,int max,Action<int> changed)
    {
        var n=new NumericUpDown { Value=value,Minimum=min,Maximum=max,Increment=1,FormatString="0",Width=80,MinHeight=34,AllowSpin=true };
        int accepted=value;bool reverting=false;
        AutomationProperties.SetName(n,name);
        n.ValueChanged+=(_,_)=>
        {
            if(_building||reverting)return;
            if(n.Value.HasValue&&decimal.Truncate(n.Value.Value)==n.Value.Value)
            {
                _error.Text="";changed((int)n.Value.Value);
                if(string.IsNullOrEmpty(_error.Text)){accepted=(int)n.Value.Value;return;}
            }
            reverting=true;n.Value=accepted;reverting=false;
        };
        return n;
    }
    private Border Separator() => new() { Height=1,Background=Line,Margin=new Thickness(0,2) };
    private void Build()
    {
        _building=true;Background=Brush.Parse(Dark?"#151A24":"#F5F6FA");Foreground=Ink;
        _page.Children.Clear();_heading.Children.Clear();_heading.ColumnDefinitions=new ColumnDefinitions("*,Auto");_heading.RowDefinitions=new RowDefinitions("Auto,Auto");
        if(!Model.Loaded){_page.Children.Add(Text("出牌规则",26,true));_page.Children.Add(Text(Model.Status,14,false,Brushes.IndianRed));_page.Children.Add(Button("重新读取",()=>{Model.Load();Build();}));_building=false;return;}
        _heading.Children.Add(new StackPanel { Spacing=6,Children={Text("出牌规则",26,true),Text("决定一张牌什么时候打，以及同档位里谁先打。",12,false,Muted)} });
        var preset=new ComboBox { ItemsSource=Model.Document.Presets.OrderBy(p=>p.Builtin?0:1).ToList(),SelectedItem=Model.Preset,Width=150,MinHeight=40 };
        AutomationProperties.SetName(preset,"职业预设");preset.SelectionChanged+=(_,_)=>{if(!_building&&preset.SelectedItem is PlayCardPreset p&&p.Id!=Model.Document.Active){Model.SwitchPreset(p.Id);_search="";Build();}};
        _status.Text=Model.Status;_status.FontSize=11;_status.Foreground=Muted;_status.VerticalAlignment=VerticalAlignment.Center;_status.MaxWidth=140;_status.TextWrapping=TextWrapping.Wrap;
        if(_status.Parent is Panel statusParent)statusParent.Children.Remove(_status);
        var actions=Row(Text("预设"),preset,_status,Button("保存规则",()=>{if(!Model.Save())Notice(Model.Status);},true));
        var toolbar=new ScrollViewer { Content=actions,HorizontalScrollBarVisibility=ScrollBarVisibility.Auto,VerticalScrollBarVisibility=ScrollBarVisibility.Disabled,HorizontalAlignment=HorizontalAlignment.Right,MaxWidth=820 };
        Grid.SetColumn(toolbar,1);_heading.Children.Add(toolbar);_page.Children.Add(_heading);
        var sequence=new WrapPanel { Orientation=Orientation.Horizontal };
        foreach(var v in new[]{"① 使用时机  ·  能不能打","›","② 再动 ＋ 排序档位","›","③ 同档优先级曲线","›","④ 手牌原顺序"})sequence.Children.Add(new Border { Child=Text(v,12,v.StartsWith("③"),v.StartsWith("③")?Accent:Muted),Margin=new Thickness(6,2),Padding=new Thickness(4) });
        _page.Children.Add(Box(sequence,12));_workspace.Children.Clear();_left.Children.Clear();_right.Children.Clear();
        if(_editorBox!=null)_editorBox.Child=null;if(_previewBox!=null)_previewBox.Child=null;_editorBox=null;_previewBox=null;
        _left.Children.Add(Text($"规则卡片  {Model.Preset.Rules.Count}    ·    {Model.Preset.Rules.Count(r=>r.Enabled)} 条已启用",15,true));
        var search=Input("搜索规则或卡名",_search);search.TextChanged+=(_,_)=>{_search=search.Text??"";RenderList();};_left.Children.Add(search);_left.Children.Add(_list);
        _left.Children.Add(Button("＋ 新建规则卡片",()=>OpenPicker()));_left.Children.Add(Text("每张卡牌一条规则，普通与强化版本共用。\n未配置卡牌使用原稀有度档位，优先级为 50。",11,false,Muted));
        _workspace.Children.Add(_left);_workspace.Children.Add(_right);_page.Children.Add(_workspace);
        _page.Children.Add(Text("各预设独立保存规则与预览手牌 · 保存后应用于实际出牌 · 当前选中预设生效",11,false,Muted));
        RenderList();RenderEditor();RenderPreview();_building=false;Responsive();
    }
    private void Responsive()
    {
        if(_heading.Children.Count<2)return;bool compact=Bounds.Width<1000;var toolbar=_heading.Children[1];
        Grid.SetRow(toolbar,compact?1:0);Grid.SetColumn(toolbar,compact?0:1);Grid.SetColumnSpan(toolbar,compact?2:1);toolbar.Margin=new Thickness(0,compact?16:0,0,0);toolbar.HorizontalAlignment=compact?HorizontalAlignment.Left:HorizontalAlignment.Right;
        bool stacked=Bounds.Width<800;_workspace.ColumnDefinitions=new ColumnDefinitions(stacked?"*":"260,22,*");_workspace.RowDefinitions=new RowDefinitions(stacked?"Auto,Auto":"Auto");Grid.SetColumn(_right,stacked?0:2);Grid.SetRow(_right,stacked?1:0);_right.Margin=new Thickness(0,stacked?18:0,0,0);
        if(_modal!=null)_modal.MaxHeight=Math.Max(260,Bounds.Height-40);
    }
    private void RenderList()
    {
        _list.Children.Clear();
        if(_left.Children.FirstOrDefault() is TextBlock heading)heading.Text=$"规则卡片  {Model.Preset.Rules.Count}    ·    {Model.Preset.Rules.Count(r=>r.Enabled)} 条已启用";
        foreach(var r in Model.Preset.Rules.Where(r=>r.Card.Contains(_search,StringComparison.OrdinalIgnoreCase)))
        {
            var c=Model.Card(r.Card);var tier=PlayCardPolicy.Tiers[Math.Max(0,PlayCardPolicy.EffectiveTier(c.Rarity,r))];
            var toggle=new ToggleSwitch { IsChecked=r.Enabled,OnContent="",OffContent="",MinWidth=40,VerticalAlignment=VerticalAlignment.Top };
            AutomationProperties.SetName(toggle,"启用"+r.Card+"规则");toggle.IsCheckedChanged+=(_,_)=>{r.Enabled=toggle.IsChecked==true;Model.Mark();RenderList();RenderEditor();RenderPreview();};
            var labels=new StackPanel { Spacing=5,Children={Text(c.Name,13,true),Badge((c.Extra?"再动 · ":"")+(tier!=c.Rarity?c.Rarity+" → ":"")+tier)} };
            var top=new Grid { ColumnDefinitions=new ColumnDefinitions("40,8,*,Auto") };top.Children.Add(Art(c,40));Grid.SetColumn(labels,2);top.Children.Add(labels);Grid.SetColumn(toggle,3);top.Children.Add(toggle);
            var mini=new CardRuleCurve { Rule=r,Mini=true,Dark=Dark,Width=65,Height=26 };
            var bottom=new Grid { ColumnDefinitions=new ColumnDefinitions("*,Auto"),Margin=new Thickness(0,12,0,0) };bottom.Children.Add(Text(r.Limited?$"剩余 {r.Min}–{r.Max} 回合可用":"不限使用回合",11,false,Muted));Grid.SetColumn(mini,1);bottom.Children.Add(mini);
            var card=Box(new StackPanel { Children={top,bottom} },14);card.BorderBrush=r.Id==Model.SelectedId?Accent:Line;card.Background=r.Id==Model.SelectedId?Brush.Parse(Dark?"#293B5B":"#EDF3FF"):Panel;card.Opacity=r.Enabled?1:.65;card.Focusable=true;
            AutomationProperties.SetName(card,"编辑"+r.Card+"规则");void Choose(){Model.SelectedId=r.Id;RenderList();RenderEditor();}
            card.PointerPressed+=(_,_)=>{if(!toggle.IsPointerOver)Choose();};card.KeyDown+=(_,e)=>{if(e.Key is Key.Enter or Key.Space){Choose();e.Handled=true;}};_list.Children.Add(card);
        }
        if(_list.Children.Count==0)_list.Children.Add(Text("没有匹配的规则",13,false,Muted));
    }
    private void RenderEditor()
    {
        bool old=_building;_building=true;_editor.Children.Clear();
        if(_editorBox!=null){_editorBox.Child=null;_right.Children.Remove(_editorBox);}
        if(Model.Selected is not {} r){_editor.Children.Add(Text("这个预设还没有规则",20,true));_editor.Children.Add(Text("选择一张卡牌，开始设置它的使用时机与优先级。",13,false,Muted));_editor.Children.Add(Button("＋ 新建规则卡片",()=>OpenPicker(),true));_editorBox=Box(_editor,24);_right.Children.Insert(0,_editorBox);_building=old;return;}
        var c=Model.Card(r.Card);var header=new Grid { ColumnDefinitions=new ColumnDefinitions("64,16,*,Auto") };header.Children.Add(Art(c,64));
        var labels=new StackPanel { Spacing=6,Children={Text(c.Name,22,true),Text("普通 / 强化共用",12,false,Muted),Badge((c.Extra?"再动 · ":"")+PlayCardPolicy.Tiers[PlayCardPolicy.EffectiveTier(c.Rarity,r)]+" 档 · 曲线仅比较同档")} };Grid.SetColumn(labels,2);header.Children.Add(labels);
        var toggle=new ToggleSwitch { IsChecked=r.Enabled,OnContent="",OffContent="",VerticalAlignment=VerticalAlignment.Center };AutomationProperties.SetName(toggle,"启用当前规则");toggle.IsCheckedChanged+=(_,_)=>{if(_building)return;r.Enabled=toggle.IsChecked==true;Model.Mark();RenderList();RenderEditor();RenderPreview();};var enable=Row(Text("启用规则",12,false,Muted),toggle);Grid.SetColumn(enable,3);header.Children.Add(enable);_editor.Children.Add(header);_editor.Children.Add(Separator());
        _editor.Children.Add(Row(Text("◷ 使用时机",15,true),Text("剩余回合包含追加回合",11,false,Muted)));
        var timing=new WrapPanel { Orientation=Orientation.Horizontal };timing.Children.Add(Row(Button("不限回合",()=>{r.Limited=false;Model.Mark();RenderList();RenderEditor();RenderPreview();},!r.Limited),Button("仅在区间",()=>{r.Limited=true;Model.Mark();RenderList();RenderEditor();RenderPreview();},r.Limited)));
        if(r.Limited)
        {
            var min=Number("最少剩余回合",r.Min,1,30,v=>{if(v>r.Max){_error.Text="最少回合不能大于最多回合";return;}r.Min=v;Updated();});
            var max=Number("最多剩余回合",r.Max,1,30,v=>{if(v<r.Min){_error.Text="最多回合不能小于最少回合";return;}r.Max=v;Updated();});timing.Children.Add(new Border { Child=Row(Text("剩余",12),min,Text("至",12),max,Text("回合",12)),Margin=new Thickness(12,0,0,0) });
        }
        _editor.Children.Add(timing);_error.Text="";_error.Foreground=Brushes.IndianRed;_error.FontSize=12;_editor.Children.Add(_error);_editor.Children.Add(Separator());_editor.Children.Add(Text("☷ 排序档位",15,true));
        int baseTier=Array.IndexOf(PlayCardPolicy.Tiers,c.Rarity),top=baseTier>=3?baseTier+1:3;var tierText=Text(PlayCardPolicy.Tiers[PlayCardPolicy.EffectiveTier(c.Rarity,r)],17,true,Accent);
        var slider=new Slider { Minimum=baseTier,Maximum=top,TickFrequency=1,IsSnapToTickEnabled=true,Value=PlayCardPolicy.EffectiveTier(c.Rarity,r),IsEnabled=baseTier<3,MinWidth=140,MaxWidth=320,HorizontalAlignment=HorizontalAlignment.Stretch };AutomationProperties.SetName(slider,"提升排序档位");
        slider.PropertyChanged+=(_,e)=>{if(e.Property==RangeBase.ValueProperty&&!_building){r.Tier=PlayCardPolicy.Tiers[(int)slider.Value];tierText.Text=r.Tier;labels.Children[2]=Badge((c.Extra?"再动 · ":"")+PlayCardPolicy.Tiers[PlayCardPolicy.EffectiveTier(c.Rarity,r)]+" 档 · 曲线仅比较同档");Model.Mark();RenderList();RenderPreview();}};
        var tierGrid=new Grid { ColumnDefinitions=new ColumnDefinitions("Auto,20,*,20,Auto") };tierGrid.Children.Add(Text("原卡 "+c.Rarity,12,false,Muted));Grid.SetColumn(slider,2);tierGrid.Children.Add(slider);Grid.SetColumn(tierText,4);tierGrid.Children.Add(tierText);_editor.Children.Add(tierGrid);
        _editor.Children.Add(Text(baseTier>=3?"此卡保持原档位。再动属性独立判断。":"R → SR → SSR · 提升后与目标档位比较曲线分数；停用规则后恢复原档位。",11,false,Muted));_editor.Children.Add(Separator());
        var curveHead=new Grid { ColumnDefinitions=new ColumnDefinitions("*,Auto") };curveHead.Children.Add(Text("⌁ 同档优先级曲线",15,true));var mode=Select("曲线过渡方式",new[]{"线性过渡","阶梯变化"},r.Mode=="linear"?0:1);mode.SelectionChanged+=(_,_)=>{if(_building)return;r.Mode=mode.SelectedIndex==0?"linear":"step";Updated();};Grid.SetColumn(mode,1);curveHead.Children.Add(mode);_editor.Children.Add(curveHead);_editor.Children.Add(Text("拖动节点，或在下方输入数值。50 为默认值，100 最先，0 最后。",12,false,Muted));
        _curve=new CardRuleCurve { Rule=r,PreviewTurn=Model.PreviewTurn,Dark=Dark,Height=222,HorizontalAlignment=HorizontalAlignment.Stretch };AutomationProperties.SetName(_curve,"剩余回合与同档优先级曲线");
        _curve.PointChanged+=(i,t,p,done)=>{Model.SetPoint(i,t,p);UpdateCurveStatus();RenderList();RenderPreview();if(done)RenderNodes();};_curveStatus.Foreground=Muted;_curveStatus.FontSize=11;UpdateCurveStatus();
        // Release reusable controls from their previous parent before rebuilding.
        if(_curveStatus.Parent is Panel parent)parent.Children.Remove(_curveStatus);
        _editor.Children.Add(new Border { Child=new StackPanel { Children={_curveStatus,_curve} },Padding=new Thickness(10,8),CornerRadius=new CornerRadius(10),BorderBrush=Line,BorderThickness=new Thickness(1),Background=Soft });
        var nodeHead=new Grid { ColumnDefinitions=new ColumnDefinitions("*,Auto") };nodeHead.Children.Add(Text("曲线节点 · 剩余回合 → 优先级",12));var add=Button("＋ 添加节点",()=>{Model.AddPoint();RenderNodes();Updated();});Grid.SetColumn(add,1);nodeHead.Children.Add(add);_editor.Children.Add(nodeHead);_editor.Children.Add(_nodes);RenderNodes();_editor.Children.Add(Text("节点外沿用最近值 · 阴影区间暂不允许使用",11,false,Muted));_editor.Children.Add(Separator());
        var footer=new Grid { ColumnDefinitions=new ColumnDefinitions("*,Auto") };footer.Children.Add(Text("先满足使用时机，再比较同档优先级。",11,false,Muted));var del=Button("删除这条规则",()=>Confirm($"删除“{r.Card}”的规则？",()=>{Model.RemoveRule();Build();}));del.Foreground=Brushes.IndianRed;Grid.SetColumn(del,1);footer.Children.Add(del);_editor.Children.Add(footer);
        _editorBox=Box(_editor,24);_right.Children.Insert(0,_editorBox);_building=old;
    }
    private void UpdateCurveStatus(){var r=Model.Selected;if(r==null)return;_curveStatus.Text=!r.Enabled?"规则已停用":PlayCardPolicy.Allowed(r,Model.PreviewTurn)?$"同档优先级                 剩余 {Model.PreviewTurn} 回合 · 优先级 {PlayCardPolicy.Priority(r,Model.PreviewTurn)}":$"剩余 {Model.PreviewTurn} 回合 · 暂不使用";}
    private void Updated(){_error.Text="";Model.Mark();_curve?.InvalidateVisual();UpdateCurveStatus();RenderList();RenderPreview();}
    private void RenderNodes()
    {
        _nodes.Children.Clear();if(Model.Selected is not {} r)return;var wrap=new WrapPanel { Orientation=Orientation.Horizontal };
        for(int i=0;i<r.Points.Count;i++)
        {
            int index=i;var p=r.Points[i];var remove=Button("×",()=>{r.Points.RemoveAt(index);Model.Mark();RenderNodes();Updated();});remove.IsEnabled=r.Points.Count>2;remove.MinHeight=22;remove.Padding=new Thickness(3,0);AutomationProperties.SetName(remove,$"删除节点{i+1}");
            var head=new Grid { ColumnDefinitions=new ColumnDefinitions("*,Auto") };head.Children.Add(Text($"节点 {i+1}",11,false,Muted));Grid.SetColumn(remove,1);head.Children.Add(remove);
            var turn=Number($"节点{i+1}剩余回合",p[0],1,30,v=>{if(!Model.SetPoint(index,v,r.Points[index][1])){_error.Text="节点回合不能重复";return;}Updated();});var score=Number($"节点{i+1}优先级",p[1],0,100,v=>{Model.SetPoint(index,r.Points[index][0],v);Updated();});turn.Width=72;score.Width=72;
            var node=Box(new StackPanel { Spacing=6,Children={head,Row(turn,Text("→",11,false,Muted),score)} },10);node.CornerRadius=new CornerRadius(9);node.Margin=new Thickness(0,0,8,8);wrap.Children.Add(node);
        }
        _nodes.Children.Add(wrap);
    }
    private void RenderPreview()
    {
        _preview.Children.Clear();if(!Model.Loaded)return;
        var head=new WrapPanel { Orientation=Orientation.Horizontal };head.Children.Add(Row(Text("▷ 出牌顺序预览",17,true),Button(_handExpanded?"收起配置":"配置手牌",()=>{_handExpanded=!_handExpanded;RenderPreview();})));
        var turn=Text($"剩余 {Model.PreviewTurn} 回合",13,true,Accent);var slider=new Slider { Minimum=1,Maximum=30,TickFrequency=1,IsSnapToTickEnabled=true,Value=Model.PreviewTurn,Width=140 };AutomationProperties.SetName(slider,"预览剩余回合");
        slider.PropertyChanged+=(_,e)=>{if(e.Property==RangeBase.ValueProperty){Model.PreviewTurn=(int)slider.Value;turn.Text=$"剩余 {Model.PreviewTurn} 回合";if(_curve!=null){_curve.PreviewTurn=Model.PreviewTurn;_curve.InvalidateVisual();}UpdateCurveStatus();RenderHand();}};
        head.Children.Add(new Border { Child=Row(turn,slider),Margin=new Thickness(18,0,0,0) });_preview.Children.Add(head);
        if(_handExpanded)
        {
            var slots=new WrapPanel { Orientation=Orientation.Horizontal };
            for(int i=0;i<Model.Preset.Hand.Count;i++)
            {
                int slot=i;var c=Model.Card(Model.Preset.Hand[i]);var change=Button(c.Name,()=>OpenPicker(slot));change.Content=Row(Art(c,25),Text(c.Name,12));AutomationProperties.SetName(change,$"更换槽位{slot+1} {c.Name}");
                var remove=Button("×",()=>{Model.RemoveHand(slot);RenderPreview();});remove.IsEnabled=Model.Preset.Hand.Count>1;AutomationProperties.SetName(remove,$"移除槽位{slot+1}");slots.Children.Add(new Border { Child=Row(change,remove),Margin=new Thickness(0,0,8,8) });
            }
            var add=Button("＋ 添加手牌",()=>OpenPicker(Model.Preset.Hand.Count));add.IsEnabled=Model.Preset.Hand.Count<5;slots.Children.Add(add);
            _preview.Children.Add(new Border { Background=Soft,Padding=new Thickness(12),CornerRadius=new CornerRadius(10),Child=new StackPanel { Spacing=8,Children={Text("选择 1–5 张牌，可重复。完全同分时按槽位先后排序。",11,false,Muted),slots} } });
        }
        _preview.Children.Add(_hand);RenderHand();_preview.Children.Add(Text("从左至右出牌：使用时机 → 再动 → 排序档位 → 曲线分数 → 手牌槽位顺序。",11,false,Muted));
        if(_previewBox==null){_previewBox=Box(_preview,22);_right.Children.Add(_previewBox);}
    }
    private void RenderHand()
    {
        _hand.Children.Clear();int rank=0;
        foreach(var p in Model.Preview())
        {
            var info=p.Allowed?$"{(p.Card.Extra?"再动 · ":"")}{PlayCardPolicy.Tiers[p.Tier]} · {p.Priority} 分":$"保留至 {p.Rule!.Min}–{p.Rule.Max} 回合";
            _hand.Children.Add(new Border { Background=Soft,BorderBrush=Line,BorderThickness=new Thickness(1),CornerRadius=new CornerRadius(9),Padding=new Thickness(10,8),Margin=new Thickness(0,0,8,8),Opacity=p.Allowed?1:.6,Child=Row(Text(p.Allowed?(++rank).ToString():"—",12,true,Accent),Art(p.Card,32),new StackPanel { Spacing=4,Children={Text(p.Card.Name,11,true),Text(info,10,false,Muted)} }) });
        }
    }
}
