using System;
using System.Collections.Generic;
using System.Diagnostics.CodeAnalysis;
using System.IO;
using System.Linq;
using MFAAvalonia.ViewModels.Pages;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Helper;

/// <summary>Shares only one profession's battle settings. Catalog data always comes from the receiving installation.</summary>
public static class PlayCardProfessionShare
{
    public const string Format = "maagakumasu-ex-profession";
    public const int MaximumBytes = 2 * 1024 * 1024;
    private static readonly string[] RuleFields =
    [
        "card", "enabled", "limited", "singleCopy", "customExtraAction", "comboTarget", "min", "max", "tier", "mode", "points",
        "totalTurns", "conditions", "priorityOverrides", "followUp"
    ];

    public static string Export(PlayCardPreset preset, IReadOnlyList<CardListCard> cards)
    {
        var document = ValidationDocument(preset);
        PlayCardPolicy.Validate(document, cards);
        var rules = JArray.FromObject(preset.Rules, JsonSerializer.Create(PlayCardPolicy.Json));
        foreach (var rule in rules.Cast<JObject>())
        {
            rule.Remove("id");
        }
        return new JObject
        {
            ["format"] = Format,
            ["version"] = 1,
            ["career"] = preset.Career,
            ["rules"] = rules,
            ["hold"] = JObject.FromObject(preset.Hold, JsonSerializer.Create(PlayCardPolicy.Json))
        }.ToString(Formatting.Indented);
    }

    public static PlayCardPreset Read(string text, string career, IReadOnlyList<CardListCard> cards)
    {
        if (System.Text.Encoding.UTF8.GetByteCount(text) > MaximumBytes) throw new InvalidDataException("职业分享文件过大");
        var share = JObject.Parse(text, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
        var fields = new[] { "format", "version", "career", "rules", "hold" };
        Require(share.Properties().Count() == fields.Length && share.Properties().All(property => fields.Contains(property.Name)), "职业分享字段无效");
        Require(share["format"]?.Type == JTokenType.String && (string?)share["format"] == Format
            && share["version"]?.Type == JTokenType.Integer && (long)share["version"]! == 1, "不支持的职业分享格式或版本");
        Require(share["career"]?.Type == JTokenType.String && (string?)share["career"] == career, "分享文件职业与当前预设不一致");
        Require(share["rules"] is JArray, "职业分享规则列表无效");
        PlayCardPolicy.ValidateHoldToken(share["hold"]);
        var rules = (JArray)share["rules"]!.DeepClone();
        foreach (var token in rules)
        {
            Require(token is JObject, "职业分享规则格式无效");
            var rule = (JObject)token;
            Require(rule.Properties().All(property => RuleFields.Contains(property.Name)), "职业分享规则含未知字段");
            rule["id"] = Guid.NewGuid().ToString("N");
        }
        // Reuse the normal reader and validator, including strict field types and cycle checks.
        var preset = new PlayCardPreset { Name = "Imported", Career = career, Hand = [cards.First().Name] };
        var document = ValidationDocument(preset);
        var json = JObject.FromObject(document, JsonSerializer.Create(PlayCardPolicy.Json));
        var current = ((JArray)json["presets"]!).OfType<JObject>().First(item => (string?)item["id"] == preset.Id);
        current["rules"] = rules;
        current["hold"] = share["hold"]!.DeepClone();
        var parsed = PlayCardPolicy.Read(json.ToString());
        PlayCardPolicy.Validate(parsed, cards);
        return parsed.Presets.First(item => item.Id == preset.Id);
    }

    private static PlayCardDocument ValidationDocument(PlayCardPreset preset)
    {
        // The document validator also protects the six built-in profession identities.
        var presets = PlayCardPolicy.Careers.Select((career, index) => new PlayCardPreset
        {
            Id = "career-" + index, Name = career, Career = career, Builtin = true, Hand = [preset.Hand[0]]
        }).ToList();
        var copy = PlayCardPolicy.Clone(preset);
        var existing = presets.FindIndex(item => item.Id == copy.Id);
        if (existing >= 0) presets[existing] = copy;
        else presets.Add(copy);
        return new PlayCardDocument { Active = copy.Id, Presets = presets };
    }

    private static void Require([DoesNotReturnIf(false)] bool valid, string message)
    {
        if (!valid) throw new InvalidDataException(message);
    }
}
