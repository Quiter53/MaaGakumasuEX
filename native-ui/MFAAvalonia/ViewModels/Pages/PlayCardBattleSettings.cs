using System;
using System.Collections.Generic;
using System.Diagnostics.CodeAnalysis;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.ViewModels.Pages;

public sealed class PlayCardState
{
    public int? FullPower { get; set; }
    public string? Stance { get; set; }
}

public sealed class PlayCardCondition
{
    public string Field { get; set; } = "remaining";
    [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
    public int? Min { get; set; }
    [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
    public int? Max { get; set; }
    [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
    public string? Value { get; set; }
}

public sealed class PlayCardPriorityOverride
{
    [JsonProperty(ObjectCreationHandling = ObjectCreationHandling.Replace)]
    public List<List<PlayCardCondition>> Conditions { get; set; } = [];
    public int Priority { get; set; } = 50;
}

public sealed class PlayCardFollowUp
{
    [JsonProperty(ObjectCreationHandling = ObjectCreationHandling.Replace)]
    public List<string> Cards { get; set; } = [];
    public int WaitTurns { get; set; }
    public string TargetPolicy { get; set; } = "normal";
}

public sealed class PlayCardHold
{
    [JsonProperty(ObjectCreationHandling = ObjectCreationHandling.Replace)]
    public List<string> Cards { get; set; } = [];
    public bool Ordered { get; set; }
}

public static partial class PlayCardPolicy
{
    public static readonly string[] Stances = ["全力", "強気", "温存"];

    public static bool Matches(List<List<PlayCardCondition>> conditions, int? remaining, PlayCardState? state = null)
    {
        return conditions.Count == 0 || conditions.Any(group => group.Count > 0 && group.All(atom =>
        {
            if (atom.Field == "stance") return Stances.Contains(state?.Stance) && state!.Stance == atom.Value;
            var value = atom.Field == "remaining" ? remaining : atom.Field == "fullPower" ? state?.FullPower : null;
            var maximum = atom.Field == "remaining" ? 30 : 999;
            var minimum = atom.Field == "remaining" ? 1 : 0;
            return value.HasValue && value >= minimum && value <= maximum
                && (!atom.Min.HasValue || value >= atom.Min) && (!atom.Max.HasValue || value <= atom.Max);
        }));
    }

    private static void RequireBattle([DoesNotReturnIf(false)] bool valid, string message)
    {
        if (!valid) throw new InvalidDataException(message);
    }

    private static void RequireProperties(JObject item, params string[] allowed)
    {
        RequireBattle(item.Properties().All(property => allowed.Contains(property.Name)), "战斗设置含未知字段");
    }

    private static void ValidateConditionToken(JToken? token)
    {
        RequireBattle(token is JArray, "状态条件必须是条件组列表");
        foreach (var group in (JArray)token!)
        {
            RequireBattle(group is JArray, "状态条件组必须是列表");
            foreach (var atom in (JArray)group)
            {
                RequireBattle(atom is JObject && atom["field"]?.Type == JTokenType.String, "状态条件字段无效");
                RequireProperties((JObject)atom, "field", "min", "max", "value");
                foreach (var key in new[] { "min", "max" })
                    RequireBattle(atom[key] == null || atom[key]!.Type == JTokenType.Integer, "状态条件边界必须是整数");
                RequireBattle(atom["value"] == null || atom["value"]!.Type == JTokenType.String, "指针状态必须是文本");
            }
        }
    }

    internal static void ValidateHoldToken(JToken? token)
    {
        if (token == null) return;
        RequireBattle(token is JObject, "保留选牌名单格式无效");
        RequireProperties((JObject)token, "cards", "ordered");
        RequireBattle(token["ordered"]?.Type == JTokenType.Boolean && token["cards"] is JArray, "保留选牌名单格式无效");
        RequireBattle(((JArray)token["cards"]!).All(card => card.Type == JTokenType.String), "保留选牌名单卡名无效");
    }

    internal static void ValidateAdvancedTokens(JObject rule)
    {
        if (rule["conditions"] != null) ValidateConditionToken(rule["conditions"]);
        if (rule["priorityOverrides"] != null)
        {
            RequireBattle(rule["priorityOverrides"] is JArray, "条件优先级必须是列表");
            foreach (var item in (JArray)rule["priorityOverrides"]!)
            {
                RequireBattle(item is JObject && item["priority"]?.Type == JTokenType.Integer, "条件优先级格式无效");
                RequireProperties((JObject)item, "conditions", "priority");
                ValidateConditionToken(item["conditions"]);
            }
        }
        if (rule["followUp"] is { Type: not JTokenType.Null } followUp)
        {
            RequireBattle(followUp is JObject, "连携设置格式无效");
            RequireProperties((JObject)followUp, "cards", "waitTurns", "targetPolicy");
            RequireBattle(followUp["cards"] is JArray && ((JArray)followUp["cards"]!).All(card => card.Type == JTokenType.String)
                && followUp["waitTurns"]?.Type == JTokenType.Integer && followUp["targetPolicy"]?.Type == JTokenType.String, "连携设置格式无效");
        }
    }

    public static void ValidateConditions(List<List<PlayCardCondition>> conditions, bool required = false)
    {
        RequireBattle(conditions != null && conditions.Count <= 8 && (!required || conditions.Count > 0), "状态条件组数量无效");
        foreach (var group in conditions!)
        {
            RequireBattle(group is { Count: > 0 and <= 8 }, "每个条件组应包含 1–8 个条件");
            foreach (var atom in group)
            {
                RequireBattle(atom != null, "状态条件字段无效");
                if (atom.Field == "stance")
                {
                    RequireBattle(Stances.Contains(atom.Value) && atom.Min == null && atom.Max == null, "指针状态条件无效");
                    continue;
                }
                RequireBattle(atom.Field is "remaining" or "fullPower", "状态条件字段无效");
                var minimum = atom.Field == "remaining" ? 1 : 0;
                var maximum = atom.Field == "remaining" ? 30 : 999;
                RequireBattle(atom.Value == null && (atom.Min.HasValue || atom.Max.HasValue)
                    && (!atom.Min.HasValue || atom.Min >= minimum && atom.Min <= maximum)
                    && (!atom.Max.HasValue || atom.Max >= minimum && atom.Max <= maximum)
                    && (!atom.Min.HasValue || !atom.Max.HasValue || atom.Min <= atom.Max), "状态条件边界无效");
            }
        }
    }

    internal static void ValidateAdvancedRule(PlayCardRule rule, HashSet<string> catalog)
    {
        ValidateConditions(rule.Conditions);
        RequireBattle(rule.PriorityOverrides != null && rule.PriorityOverrides.Count <= 16, "条件优先级数量无效");
        foreach (var item in rule.PriorityOverrides!)
        {
            RequireBattle(item != null && item.Priority is >= 0 and <= 100, "条件优先级应为 0–100");
            ValidateConditions(item.Conditions, true);
        }
        if (rule.FollowUp is not { } followUp) return;
        ValidateBattleCards(followUp.Cards, catalog, false);
        RequireBattle(followUp.Cards.All(card => Key(card) != Key(rule.Card)), "连携目标不能包含自身");
        RequireBattle(followUp.WaitTurns is >= 0 and <= 6 && followUp.TargetPolicy is "normal" or "reserveFirst" or "comboOnly", "连携等待回合或目标策略无效");
    }

    private static void ValidateBattleCards(List<string> cards, HashSet<string> catalog, bool allowEmpty)
    {
        RequireBattle(cards != null && cards.Count <= 50 && (allowEmpty || cards.Count > 0)
            && cards.All(catalog.Contains) && cards.Select(Key).Distinct().Count() == cards.Count, "战斗卡牌名单含未知、重复或过多卡牌");
    }

    internal static void ValidatePresetBattle(PlayCardPreset preset, HashSet<string> catalog)
    {
        RequireBattle(preset.Hold != null, "保留选牌名单格式无效");
        ValidateBattleCards(preset.Hold.Cards, catalog, true);
        var rules = preset.Rules.ToDictionary(rule => Key(rule.Card));
        var visiting = new HashSet<string>();
        var visited = new HashSet<string>();
        void Visit(string key)
        {
            if (visited.Contains(key) || !rules.TryGetValue(key, out var rule)) return;
            RequireBattle(visiting.Add(key), "连携目标存在循环");
            foreach (var target in rule.FollowUp?.Cards ?? []) Visit(Key(target));
            visiting.Remove(key);
            visited.Add(key);
        }
        foreach (var rule in preset.Rules) Visit(Key(rule.Card));
    }
}
