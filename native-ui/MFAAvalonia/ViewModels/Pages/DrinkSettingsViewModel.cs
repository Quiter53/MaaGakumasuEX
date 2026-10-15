using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;
using Avalonia.Media.Imaging;
using CommunityToolkit.Mvvm.ComponentModel;
using MFAAvalonia.Helper;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.ViewModels.Pages;

public sealed class DrinkRuleViewModel : ObservableObject
{
    public string Id { get; init; } = "";
    public string Name { get; init; } = "";
    public string JapaneseName { get; init; } = "";
    public Bitmap? Thumbnail { get; init; }
    public string[] TimingOptions { get; } = ["开局使用", "指定卡牌前使用", "倒数 N 回合使用"];
    private bool _target;
    public bool Target { get => _target; set { if(SetProperty(ref _target, value)) OnPropertyChanged(nameof(TargetLabel)); } }
    public string TargetLabel => Target ? "目标饮料" : "非目标";
    private int _timing;
    public int Timing { get => _timing; set { if (value < 0 || value > 2) return; if (SetProperty(ref _timing, value)) { OnPropertyChanged(nameof(IsCardTiming)); OnPropertyChanged(nameof(IsTurnTiming)); OnPropertyChanged(nameof(IsOpeningTiming)); } } }
    public bool IsOpeningTiming => Timing == 0;
    public bool IsCardTiming => Timing == 1;
    public bool IsTurnTiming => Timing == 2;
    private string _card = "";
    public string Card { get => _card; set { if(SetProperty(ref _card, value)) OnPropertyChanged(nameof(CardLabel)); } }
    public string CardLabel => string.IsNullOrEmpty(Card) ? "选择触发卡牌…" : Card;
    private decimal _turns = 1;
    public decimal Turns { get => _turns; set { if(value >= 1 && value <= 99 && value == decimal.Truncate(value)) SetProperty(ref _turns, value); } }
}

public sealed class DrinkSettingsViewModel : ObservableObject
{
    private readonly string _root;
    private readonly string _path;
    private bool _ready;
    private string _status = "修改后立即保存；战斗中修改将在下一次战斗动作生效。";
    public string Status { get => _status; private set => SetProperty(ref _status, value); }
    public ObservableCollection<DrinkRuleViewModel> Drinks { get; } = [];
    public ObservableCollection<DrinkRuleViewModel> VisibleDrinks { get; } = [];
    public ObservableCollection<CardListCard> VisibleCards { get; } = [];
    private List<CardListCard> _cards = [];
    private string _search = "";
    public string Search { get => _search; set { if(SetProperty(ref _search,value)) FilterDrinks(); } }
    private int _targetFilter;
    public int TargetFilter { get => _targetFilter; set { if(value is >= 0 and <= 2 && SetProperty(ref _targetFilter,value)) { FilterDrinks(); NotifyFilters(); } } }
    public bool AllSelected => TargetFilter == 0;
    public bool TargetsSelected => TargetFilter == 1;
    public bool OthersSelected => TargetFilter == 2;
    public string AllLabel => $"全部饮料 {Drinks.Count}";
    public string TargetsLabel => $"目标 {Drinks.Count(d=>d.Target)}";
    public string OthersLabel => $"非目标 {Drinks.Count(d=>!d.Target)}";
    public string DrinkCount => $"显示 {VisibleDrinks.Count} / {Drinks.Count} 种饮料";
    public bool IsEmpty => VisibleDrinks.Count == 0;
    private void NotifyFilters()
    {
        foreach(var name in new[]{nameof(AllSelected),nameof(TargetsSelected),nameof(OthersSelected),nameof(AllLabel),nameof(TargetsLabel),nameof(OthersLabel)}) OnPropertyChanged(name);
    }
    private string _cardSearch = "";
    public string CardSearch { get => _cardSearch; set { if(SetProperty(ref _cardSearch,value)) FilterCards(); } }
    public string[] Rarities { get; } = ["全部稀有度", "SSR", "SR", "R", "N"];
    public string[] Plans { get; } = ["全部计划", "不限", "感性", "理性", "非凡"];
    public string[] Categories { get; } = ["全部分类", "主动技能", "心智技能", "负面卡牌"];
    public string[] Origins { get; } = ["全部来源", "普通", "角色卡", "支援卡"];
    public string[] Conversions { get; } = ["全部转换", "非转换卡", "转换卡"];
    public string[] Levels { get; } = ["解锁等级", "等级升序", "等级降序"];
    private int _rarity, _plan, _category, _origin, _conversion, _level;
    public int Rarity { get=>_rarity; set { if(SetProperty(ref _rarity,value)) FilterCards(); } }
    public int Plan { get=>_plan; set { if(SetProperty(ref _plan,value)) FilterCards(); } }
    public int Category { get=>_category; set { if(SetProperty(ref _category,value)) FilterCards(); } }
    public int Origin { get=>_origin; set { if(SetProperty(ref _origin,value)) FilterCards(); } }
    public int Conversion { get=>_conversion; set { if(SetProperty(ref _conversion,value)) FilterCards(); } }
    public int Level { get=>_level; set { if(SetProperty(ref _level,value)) FilterCards(); } }
    private DrinkRuleViewModel? _editing;
    public bool PickerOpen => _editing != null;
    public string PickerTitle => _editing == null ? "选择触发卡牌" : $"为「{ExText.T(_editing.Name)}」选择触发卡牌";
    public string CardCount => $"显示 {VisibleCards.Count} / {_cards.Count} 张卡牌";

    public DrinkSettingsViewModel() : this(AppPaths.InstallRoot) { }
    public DrinkSettingsViewModel(string root)
    {
        _root = root;
        _path = Path.Combine(root,"config","drink_policy.json");
        try
        {
            var catalog=JArray.Parse(File.ReadAllText(Path.Combine(root,"agent/custom/drink_catalog.json")));
            JObject saved=File.Exists(_path) ? JObject.Parse(File.ReadAllText(_path)) : new JObject();
            if(saved["version"] != null && (int)saved["version"]! != 1) throw new InvalidDataException("不支持的配置版本");
            foreach(var item in catalog)
            {
                var id=(string)item["id"]!;
                var rule=saved["drinks"]?[id];
                var timing=(string?)rule?["timing"] ?? "opening";
                var index=Array.IndexOf(new[]{"opening","before_card","last_turns"},timing);
                var turns=(int?)rule?["turns"] ?? 1;
                if(index < 0 || turns < 1 || turns > 99) throw new InvalidDataException("饮料时机或回合数无效");
                var drink=new DrinkRuleViewModel { Id=id, Name=(string)item["name"]!, JapaneseName=(string)item["jp"]!,
                    Target=(bool?)rule?["target"] ?? (bool)item["target"]!, Timing=index,
                    Card=(string?)rule?["card"] ?? "", Turns=turns,
                    Thumbnail=LoadImage($"resource/base/image/produce/drinks/{id}.png") };
                drink.PropertyChanged += (_,e) => {
                    if(e.PropertyName is nameof(DrinkRuleViewModel.Target) or nameof(DrinkRuleViewModel.Timing) or nameof(DrinkRuleViewModel.Card) or nameof(DrinkRuleViewModel.Turns)) Save();
                    if(e.PropertyName == nameof(DrinkRuleViewModel.Target)) { NotifyFilters(); if(TargetFilter != 0) FilterDrinks(); }
                };
                Drinks.Add(drink);
            }
            _ready=true;
            FilterDrinks();
            NotifyFilters();
        }
        catch(Exception ex) { Status=$"配置读取失败，未覆盖原文件：{ex.Message}"; }
    }

    private Bitmap? LoadImage(string relative)
    {
        var path=Path.Combine(_root,relative);
        if(!File.Exists(path)) path=Path.Combine(_root,"assets",relative);
        try { return File.Exists(path) ? new Bitmap(path) : null; } catch { return null; }
    }
    private void FilterDrinks()
    {
        VisibleDrinks.Clear();
        foreach(var item in Drinks.Where(d=>(TargetFilter==0 || d.Target==(TargetFilter==1)) &&
            (d.Name.Contains(Search,StringComparison.OrdinalIgnoreCase) || d.JapaneseName.Contains(Search,StringComparison.OrdinalIgnoreCase) || d.Id.Contains(Search,StringComparison.OrdinalIgnoreCase)))) VisibleDrinks.Add(item);
        OnPropertyChanged(nameof(DrinkCount)); OnPropertyChanged(nameof(IsEmpty));
    }
    public void OpenPicker(DrinkRuleViewModel drink)
    {
        if(_cards.Count==0)
        {
            try
            {
                _cards=JObject.Parse(File.ReadAllText(Path.Combine(_root,"config/card_library.json")))["cards"]!.ToObject<List<CardListCard>>()!;
                foreach(var card in _cards) card.Thumbnail=LoadImage(card.Image);
            }
            catch(Exception ex) { Status=$"卡库读取失败：{ex.Message}"; return; }
        }
        _editing=drink;
        FilterCards();
        OnPropertyChanged(nameof(PickerOpen)); OnPropertyChanged(nameof(PickerTitle));
    }
    public void ClosePicker() { _editing=null; OnPropertyChanged(nameof(PickerOpen)); }
    public void ChooseCard(CardListCard card) { if(_editing != null) _editing.Card=card.Name; ClosePicker(); }
    private void FilterCards()
    {
        var origins=new[]{"","none","idol","support"};
        var query=_cards.Where(c=>(Rarity<=0 || c.Rarity==Rarities[Rarity]) && (Plan<=0 || c.PlanType==Plan)
            && (Category<=0 || c.Category==Category) && (Origin<=0 || c.Origin==origins[Origin])
            && (Conversion<=0 || c.IsConversion==(Conversion==2)) && c.Name.Contains(CardSearch,StringComparison.OrdinalIgnoreCase));
        if(Level==1) query=query.OrderBy(c=>c.UnlockProducerLevel);
        if(Level==2) query=query.OrderByDescending(c=>c.UnlockProducerLevel);
        VisibleCards.Clear(); foreach(var card in query) VisibleCards.Add(card);
        OnPropertyChanged(nameof(CardCount));
    }
    private void Save()
    {
        if(!_ready) return;
        try
        {
            var rules=new JObject();
            foreach(var d in Drinks) rules[d.Id]=new JObject { ["target"]=d.Target,["timing"]=new[]{"opening","before_card","last_turns"}[d.Timing], ["card"]=d.Card,["turns"]=(int)d.Turns };
            var document=new JObject { ["version"]=1,["drinks"]=rules };
            Directory.CreateDirectory(Path.GetDirectoryName(_path)!);
            File.WriteAllText(_path+".tmp",document.ToString(),new System.Text.UTF8Encoding(false));
            File.Move(_path+".tmp",_path,true);
            Status="已保存；目标状态用于获取饮料，使用时机在下一次战斗动作生效。";
        }
        catch(Exception ex) { Status=$"保存失败，请检查目录权限：{ex.Message}"; }
    }
}
