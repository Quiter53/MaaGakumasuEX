using Avalonia.Media;
using Avalonia.Media.Imaging;
using CommunityToolkit.Mvvm.ComponentModel;
using MFAAvalonia.Helper;
using Newtonsoft.Json;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;

namespace MFAAvalonia.ViewModels.Pages;

public sealed class CardListCard
{
    public string Key { get; init; } = string.Empty;
    public string Name { get; init; } = string.Empty;
    public string Rarity { get; init; } = string.Empty;
    public bool Extra { get; init; }
    public string Image { get; init; } = string.Empty;
    [JsonProperty("planType")] public int PlanType { get; init; }
    [JsonProperty("category")] public int Category { get; init; }
    [JsonProperty("origin")] public string Origin { get; init; } = "none";
    [JsonProperty("isConversion")] public bool IsConversion { get; init; }
    [JsonProperty("unlockProducerLevel")] public int UnlockProducerLevel { get; init; }
    [JsonIgnore] public Bitmap? Thumbnail { get; set; }
    [JsonIgnore] public string ExtraLabel => Extra ? "再动" : string.Empty;
    [JsonIgnore] public IBrush RarityBrush => Rarity switch
    {
        "LR" => new SolidColorBrush(Color.Parse("#E75B73")),
        "SSR" => new SolidColorBrush(Color.Parse("#C084FC")),
        "SR" => new SolidColorBrush(Color.Parse("#F5C542")),
        "R" => new SolidColorBrush(Color.Parse("#7CC7E8")),
        _ => new SolidColorBrush(Color.Parse("#B7BEC7"))
    };
}

internal sealed class CardLibraryDocument
{
    [JsonProperty("cards")] public List<CardListCard> Cards { get; set; } = [];
}

internal sealed class CardListSection
{
    [JsonProperty("enabled")] public bool Enabled { get; set; }
    [JsonProperty("cards")] public List<string> Cards { get; set; } = [];
}

internal sealed class CardListPreset
{
    [JsonProperty("blacklist")] public CardListSection Blacklist { get; set; } = new();
    [JsonProperty("whitelist")] public CardListSection Whitelist { get; set; } = new();
    [JsonProperty("replacementList")] public CardListSection ReplacementList { get; set; } = new();
}

internal sealed class CardListsDocument
{
    [JsonIgnore] public bool ReadSucceeded { get; set; } = true;
    public static readonly string[] PresetNames = ["好调", "集中", "干劲", "好印象", "强气", "全力"];
    [JsonProperty("activePreset")] public string ActivePreset { get; set; } = PresetNames[0];
    [JsonProperty("presets")] public Dictionary<string, CardListPreset> Presets { get; set; } = new();
    // The active snapshot keeps the existing Python strategy/config contract.
    [JsonProperty("blacklist")] public CardListSection Blacklist { get; set; } = new();
    [JsonProperty("whitelist")] public CardListSection Whitelist { get; set; } = new();
    [JsonProperty("replacementList")] public CardListSection ReplacementList { get; set; } = new();
    [JsonProperty("debugDiagnosticsEnabled")] public bool DebugDiagnosticsEnabled { get; set; }
    [JsonProperty("detailedLogsEnabled")] public bool DetailedLogsEnabled { get; set; }

    private static CardListSection Copy(CardListSection? section) => new()
    {
        Enabled = section?.Enabled ?? false,
        Cards = (section?.Cards ?? []).Where(name => !string.IsNullOrWhiteSpace(name))
            .Distinct(StringComparer.Ordinal).ToList()
    };

    public void Normalize()
    {
        Presets ??= new();
        // Rename the original preset without losing its lists or switches.
        // If both names already exist, keep the older entry for recovery.
        if (Presets.TryGetValue("感性", out var legacyPreset) && legacyPreset != null
            && (!Presets.TryGetValue("好调", out var renamedPreset) || renamedPreset == null))
        {
            Presets["好调"] = legacyPreset;
            Presets.Remove("感性");
        }
        if (ActivePreset == "感性") ActivePreset = "好调";
        if (Presets.Count == 0)
            Presets[PresetNames[0]] = new() { Blacklist = Copy(Blacklist), Whitelist = Copy(Whitelist), ReplacementList = Copy(ReplacementList) };
        foreach (var name in PresetNames)
        {
            if (!Presets.TryGetValue(name, out var preset) || preset == null)
                Presets[name] = preset = new();
            preset.Blacklist = Copy(preset.Blacklist);
            preset.Whitelist = Copy(preset.Whitelist);
            preset.Whitelist.Cards.RemoveAll(preset.Blacklist.Cards.Contains);
            preset.ReplacementList = Copy(preset.ReplacementList);
        }
        ActivatePreset(PresetNames.Contains(ActivePreset) ? ActivePreset : PresetNames[0]);
    }

    public void ActivatePreset(string name)
    {
        if (!PresetNames.Contains(name)) throw new ArgumentException("无效的卡牌名单预设", nameof(name));
        var preset = Presets[name];
        ActivePreset = name;
        Blacklist = Copy(preset.Blacklist);
        Whitelist = Copy(preset.Whitelist);
        ReplacementList = Copy(preset.ReplacementList);
    }
}

internal static class CardListSettingsStore
{
    private static string SettingsPath => Path.Combine(AppPaths.InstallRoot, "config", "card_lists.json");

    public static CardListsDocument Load()
    {
        try
        {
            var document = File.Exists(SettingsPath)
                ? JsonConvert.DeserializeObject<CardListsDocument>(File.ReadAllText(SettingsPath))
                    ?? new CardListsDocument()
                : new CardListsDocument();
            document.Normalize();
            return document;
        }
        catch (Exception error)
        {
            LoggerHelper.Error($"读取卡牌黑白名单配置失败：原因={error.Message}", error);
            var document = new CardListsDocument { ReadSucceeded = false };
            document.Normalize();
            return document;
        }
    }

    public static void Save(CardListsDocument document)
    {
        if (!document.ReadSucceeded)
            throw new InvalidDataException("原名单配置读取失败，已保留原文件，请检查配置后重试。");
        document.Normalize();
        Directory.CreateDirectory(Path.GetDirectoryName(SettingsPath)!);
        var temporary = SettingsPath + ".tmp";
        File.WriteAllText(temporary, JsonConvert.SerializeObject(document, Formatting.Indented));
        File.Move(temporary, SettingsPath, true);
    }

    public static bool DebugDiagnosticsEnabled => Load().DebugDiagnosticsEnabled;
    public static bool DetailedLogsEnabled => Load().DetailedLogsEnabled;

    public static void SetDetailedLogsEnabled(bool enabled)
    {
        try
        {
            var document = Load();
            document.DetailedLogsEnabled = enabled;
            Save(document);
        }
        catch (Exception error)
        {
            LoggerHelper.Error("Cannot save detailed log preference", error);
        }
    }

    public static void SetDebugDiagnosticsEnabled(bool enabled)
    {
        try
        {
            var document = Load();
            document.DebugDiagnosticsEnabled = enabled;
            Save(document);
        }
        catch (Exception error)
        {
            LoggerHelper.Error($"保存卡牌调试诊断开关失败：原因={error.Message}", error);
        }
    }
}

public partial class CardListSettingsViewModel : ViewModelBase
{
    private readonly string _libraryPath = Path.Combine(AppPaths.InstallRoot, "config", "card_library.json");
    private readonly List<CardListCard> _allCards = [];
    private bool _loading = true;
    private string _loadedPreset = CardListsDocument.PresetNames[0];

    public ObservableCollection<CardListCard> VisibleCards { get; } = [];
    public ObservableCollection<CardListCard> BlacklistCards { get; } = [];
    public ObservableCollection<CardListCard> WhitelistCards { get; } = [];
    public ObservableCollection<CardListCard> ReplacementCards { get; } = [];
    public ObservableCollection<string> PresetOptions { get; } = new(CardListsDocument.PresetNames);
    public ObservableCollection<string> RarityOptions { get; } = ["全部稀有度", "LR", "SSR", "SR", "R", "N"];
    public ObservableCollection<string> PlanOptions { get; } = ["全部计划", "不限", "感性", "理性", "非凡"];
    public ObservableCollection<string> CategoryOptions { get; } = ["全部分类", "主动技能", "心智技能", "负面卡牌"];
    public ObservableCollection<string> OriginOptions { get; } = ["全部来源", "普通", "角色卡", "支援卡"];
    public ObservableCollection<string> ConversionOptions { get; } = ["全部转换", "非转换卡", "转换卡"];
    public ObservableCollection<string> UnlockSortOptions { get; } = ["解锁等级", "等级升序", "等级降序"];

    [ObservableProperty] private bool _blacklistEnabled;
    [ObservableProperty] private bool _whitelistEnabled;
    [ObservableProperty] private bool _replacementEnabled;
    [ObservableProperty] private string _selectedPreset = CardListsDocument.PresetNames[0];
    [ObservableProperty] private string _searchText = string.Empty;
    [ObservableProperty] private string _selectedRarity = "全部稀有度";
    [ObservableProperty] private string _selectedPlan = "全部计划";
    [ObservableProperty] private string _selectedCategory = "全部分类";
    [ObservableProperty] private string _selectedOrigin = "全部来源";
    [ObservableProperty] private string _selectedConversion = "全部转换";
    [ObservableProperty] private string _selectedUnlockSort = "解锁等级";
    [ObservableProperty] private string _statusText = string.Empty;

    public CardListSettingsViewModel()
    {
        Load();
    }

    partial void OnBlacklistEnabledChanged(bool value) => SaveIfReady();
    partial void OnWhitelistEnabledChanged(bool value) => SaveIfReady();
    partial void OnReplacementEnabledChanged(bool value) => SaveIfReady();
    partial void OnSelectedPresetChanged(string value)
    {
        if (_loading || !CardListsDocument.PresetNames.Contains(value)) return;
        try
        {
            var document = CardListSettingsStore.Load();
            document.Presets[_loadedPreset] = CapturePreset();
            document.ActivatePreset(value);
            CardListSettingsStore.Save(document);
            _loading = true;
            LoadPreset(document);
            StatusText = $"已切换至 {value}：黑名单 {BlacklistCards.Count} 张，白名单 {WhitelistCards.Count} 张，替换名单 {ReplacementCards.Count} 张";
        }
        catch (Exception error)
        {
            _loading = true;
            SelectedPreset = _loadedPreset;
            StatusText = $"切换失败：{error.Message}";
            LoggerHelper.Error("切换卡牌名单预设失败", error);
        }
        finally { _loading = false; }
    }
    partial void OnSearchTextChanged(string value) => ApplyFilter();
    partial void OnSelectedRarityChanged(string value) => ApplyFilter();
    partial void OnSelectedPlanChanged(string value) => ApplyFilter();
    partial void OnSelectedCategoryChanged(string value) => ApplyFilter();
    partial void OnSelectedOriginChanged(string value) => ApplyFilter();
    partial void OnSelectedConversionChanged(string value) => ApplyFilter();
    partial void OnSelectedUnlockSortChanged(string value) => ApplyFilter();

    private void Load()
    {
        string? loadError = null;
        try
        {
            if (!File.Exists(_libraryPath))
            {
                loadError = "未找到卡牌资料，请重新运行卡牌资料构建工具。";
                return;
            }

            var library = JsonConvert.DeserializeObject<CardLibraryDocument>(
                File.ReadAllText(_libraryPath)) ?? new CardLibraryDocument();
            foreach (var card in library.Cards)
            {
                try
                {
                    var imagePath = Path.Combine(
                        AppPaths.InstallRoot,
                        card.Image.Replace('/', Path.DirectorySeparatorChar));
                    if (File.Exists(imagePath))
                        card.Thumbnail = new Bitmap(imagePath);
                }
                catch
                {
                    // A missing thumbnail must not hide the card name or break settings.
                }
                _allCards.Add(card);
            }

            var saved = CardListSettingsStore.Load();
            SelectedPreset = saved.ActivePreset;
            LoadPreset(saved);
            CardListSettingsStore.Save(saved);
            StatusText = $"已载入 {_allCards.Count} 张卡牌";
        }
        catch (Exception error)
        {
            loadError = $"载入失败：{error.Message}";
            LoggerHelper.Error($"载入卡牌黑白名单页面失败：原因={error.Message}", error);
        }
        finally
        {
            _loading = false;
            ApplyFilter();
            if (loadError != null) StatusText = loadError;
        }
    }

    public CardListCard? FindCard(string key) =>
        _allCards.FirstOrDefault(card => string.Equals(card.Key, key, StringComparison.Ordinal));

    private void LoadPreset(CardListsDocument document)
    {
        _loadedPreset = document.ActivePreset;
        BlacklistEnabled = document.Blacklist.Enabled;
        WhitelistEnabled = document.Whitelist.Enabled;
        ReplacementEnabled = document.ReplacementList.Enabled;
        void Fill(ObservableCollection<CardListCard> target, CardListSection section)
        {
            target.Clear();
            foreach (var name in section.Cards)
                target.Add(_allCards.FirstOrDefault(card => card.Name == name)
                    ?? new CardListCard { Key = name, Name = name, Rarity = "未知" });
        }
        Fill(BlacklistCards, document.Blacklist);
        Fill(WhitelistCards, document.Whitelist);
        Fill(ReplacementCards, document.ReplacementList);
    }

    private CardListPreset CapturePreset() => new()
    {
        Blacklist = new() { Enabled = BlacklistEnabled, Cards = BlacklistCards.Select(card => card.Name).ToList() },
        Whitelist = new() { Enabled = WhitelistEnabled, Cards = WhitelistCards.Select(card => card.Name).ToList() },
        ReplacementList = new() { Enabled = ReplacementEnabled, Cards = ReplacementCards.Select(card => card.Name).ToList() }
    };

    public void AddToReplacement(CardListCard card)
    {
        if (!ReplacementCards.Contains(card)) ReplacementCards.Add(card);
        Save();
    }

    public void RemoveFromReplacement(CardListCard card)
    {
        ReplacementCards.Remove(card);
        Save();
    }

    public void AddToBlacklist(CardListCard card)
    {
        WhitelistCards.Remove(card);
        if (!BlacklistCards.Contains(card))
            BlacklistCards.Add(card);
        Save();
    }

    public void AddToWhitelist(CardListCard card)
    {
        BlacklistCards.Remove(card);
        if (!WhitelistCards.Contains(card))
            WhitelistCards.Add(card);
        Save();
    }

    public void RemoveFromBlacklist(CardListCard card)
    {
        BlacklistCards.Remove(card);
        Save();
    }

    public void RemoveFromWhitelist(CardListCard card)
    {
        WhitelistCards.Remove(card);
        Save();
    }

    private void ApplyFilter()
    {
        if (_loading) return;
        var query = (SearchText ?? string.Empty).Trim();
        IEnumerable<CardListCard> filtered = _allCards.Where(card =>
            (string.IsNullOrEmpty(query) || card.Name.Contains(query, StringComparison.OrdinalIgnoreCase)) &&
            (SelectedRarity == "全部稀有度" || card.Rarity == SelectedRarity) &&
            (SelectedPlan == "全部计划" || card.PlanType == SelectedPlan switch
            {
                "不限" => 1,
                "感性" => 2,
                "理性" => 3,
                "非凡" => 4,
                _ => -1
            }) &&
            (SelectedCategory == "全部分类" || card.Category == SelectedCategory switch
            {
                "主动技能" => 1,
                "心智技能" => 2,
                "负面卡牌" => 3,
                _ => -1
            }) &&
            (SelectedOrigin == "全部来源" || card.Origin == SelectedOrigin switch
            {
                "普通" => "none",
                "角色卡" => "idol",
                "支援卡" => "support",
                _ => string.Empty
            }) &&
            (SelectedConversion == "全部转换" || card.IsConversion == (SelectedConversion == "转换卡")));

        filtered = SelectedUnlockSort switch
        {
            "等级升序" => filtered.OrderBy(card => card.UnlockProducerLevel),
            "等级降序" => filtered.OrderByDescending(card => card.UnlockProducerLevel),
            _ => filtered
        };

        var desired = filtered.ToList();
        var wanted = desired.ToHashSet();
        for (int i = VisibleCards.Count - 1; i >= 0; i--)
            if (!wanted.Contains(VisibleCards[i])) VisibleCards.RemoveAt(i);
        for (int i = 0; i < desired.Count; i++)
        {
            if (i < VisibleCards.Count && ReferenceEquals(VisibleCards[i], desired[i])) continue;
            int old = VisibleCards.IndexOf(desired[i]);
            if (old >= 0) VisibleCards.Move(old, i);
            else VisibleCards.Insert(i, desired[i]);
        }
        StatusText = $"显示 {VisibleCards.Count} / {_allCards.Count} 张卡牌";
    }

    private void SaveIfReady()
    {
        if (!_loading) Save();
    }

    private void Save()
    {
        try
        {
            var document = CardListSettingsStore.Load();
            document.Presets[_loadedPreset] = CapturePreset();
            document.ActivatePreset(_loadedPreset);
            CardListSettingsStore.Save(document);
            StatusText = $"已保存 {SelectedPreset}：黑名单 {BlacklistCards.Count} 张，白名单 {WhitelistCards.Count} 张，替换名单 {ReplacementCards.Count} 张";
        }
        catch (Exception error)
        {
            StatusText = $"保存失败：{error.Message}";
            LoggerHelper.Error($"保存卡牌黑白名单失败：原因={error.Message}", error);
        }
    }
}
