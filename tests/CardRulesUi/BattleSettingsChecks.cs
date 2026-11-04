using Avalonia;
using Avalonia.Automation;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Media.Imaging;
using Avalonia.Platform.Storage;
using Avalonia.Styling;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.Helper;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.Views.Pages;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using System.Reflection;

internal static class BattleSettingsChecks
{
    public static void Run(CardRulesViewModel model, string output, Action<bool, string> check, JArray vectors)
    {
        var oracle = JObject.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "card_rule_policy_oracle.json")));
        JObject Rule(JToken patch)
        {
            var rule = (JObject)oracle["baseRule"]!.DeepClone();
            foreach (var property in ((JObject)patch).Properties()) rule[property.Name] = property.Value.DeepClone();
            return rule;
        }
        foreach (var item in oracle["policyCases"]!)
        {
            var ruleJson = Rule(item["rule"]!);
            var rule = ruleJson.ToObject<PlayCardRule>()!;
            var turn = (int?)item["turn"];
            var state = item["state"]!.Type == JTokenType.Null ? null : item["state"]!.ToObject<PlayCardState>();
            var rarity = (string)item["rarity"]!;
            var extra = (bool)item["extra"]!;
            var expected = item["expected"]!;
            var allowed = PlayCardPolicy.Allowed(rule, turn, state);
            var tier = PlayCardPolicy.EffectiveTier(rarity, rule);
            var priority = PlayCardPolicy.Priority(rule, turn, state);
            var effectiveExtra = PlayCardPolicy.EffectiveExtraAction(extra, rule);
            check(allowed == (bool)expected["allowed"]! && tier == (int)expected["tier"]!
                && Math.Abs(priority - (double)expected["priority"]!) < .000001 && effectiveExtra == (bool)expected["effectiveExtra"]!, "oracle policy " + item["id"]);
            vectors.Add(new JObject
            {
                ["id"] = item["id"]!.DeepClone(), ["rarity"] = rarity, ["extra"] = extra, ["effectiveExtra"] = effectiveExtra,
                ["rule"] = ruleJson, ["turn"] = turn, ["state"] = item["state"]!.DeepClone(), ["result"] = new JArray(allowed, tier, priority)
            });
        }
        foreach (var item in oracle["validationCases"]!)
        {
            var document = JObject.FromObject(model.Document, JsonSerializer.Create(PlayCardPolicy.Json));
            var preset = document["presets"]!.First(value => (string?)value["id"] == model.Preset.Id);
            var rules = new JArray(Rule(item["rule"]!));
            foreach (var additional in item["additionalRules"]!) rules.Add(Rule(additional));
            preset["rules"] = rules;
            foreach (var property in ((JObject)item["preset"]!).Properties()) preset[property.Name] = property.Value.DeepClone();
            bool valid;
            try { PlayCardPolicy.Validate(PlayCardPolicy.Read(document.ToString()), model.Cards); valid = true; }
            catch (Exception exception) when (exception is InvalidDataException or JsonException or ArgumentException or OverflowException) { valid = false; }
            check(valid == (bool)item["valid"]!, "oracle validation " + item["id"]);
        }
        var originalRules = model.Preset.Rules;
        var originalSelection = model.SelectedId;
        var changedRule = new PlayCardRule { Card = "イメトレ", FollowUp = new PlayCardFollowUp { Cards = ["届いて！"] } };
        model.Preset.Rules = [changedRule];
        model.SelectedId = changedRule.Id;
        check(!model.ChangeRuleCard("届いて！") && changedRule.Card == "イメトレ", "changing source rejects a self follow-up without mutation");
        model.Preset.Rules.Add(new PlayCardRule { Card = "届いて！", FollowUp = new PlayCardFollowUp { Cards = ["せのびの魔法"] } });
        check(!model.ChangeRuleCard("せのびの魔法") && changedRule.Card == "イメトレ", "changing source rejects an indirect cycle without mutation");
        check(model.ChangeRuleCard("スポットライト") && changedRule.Card == "スポットライト" && changedRule.FollowUp.Cards[0] == "届いて！", "valid source change preserves the follow-up candidates");
        model.Preset.Rules = originalRules;
        model.SelectedId = originalSelection;

        var source = PlayCardPolicy.Clone(model.Preset);
        source.Rules =
        [
            Rule(oracle["policyCases"]!.First(item => (string?)item["id"] == "first-priority-override-wins")["rule"]!).ToObject<PlayCardRule>()!,
            new PlayCardRule { Card = "届いて！", Enabled = false }
        ];
        source.Rules[0].SingleCopy = true;
        source.Rules[0].Conditions = [[new PlayCardCondition { Min = 3, Max = 7 }]];
        source.Rules[0].FollowUp = new PlayCardFollowUp { Cards = ["届いて！"], WaitTurns = 2, TargetPolicy = "reserveFirst" };
        source.Hold = new PlayCardHold { Cards = ["届いて！"], Ordered = true };
        var text = PlayCardProfessionShare.Export(source, model.Cards);
        var share = JObject.Parse(text);
        check(share.Properties().Select(property => property.Name).Order().SequenceEqual(new[] { "career", "format", "hold", "rules", "version" }), "share contains only profession battle fields");
        check(share["rules"]!.All(rule => rule["id"] == null) && (bool)share["rules"]![0]!["singleCopy"]!, "share excludes local identities and includes single-copy");
        void Reject(string input, string name)
        {
            var rejected = false;
            try { PlayCardProfessionShare.Read(input, model.Preset.Career, model.Cards); }
            catch (Exception exception) when (exception is InvalidDataException or JsonException or ArgumentException or OverflowException) { rejected = true; }
            check(rejected, name);
        }
        Reject(text.Replace("\"version\": 1", "\"version\": 2"), "share rejects unsupported version");
        Reject(text.Replace("\"version\": 1", "\"version\": 1, \"version\": 1"), "share rejects duplicate properties");
        var bad = (JObject)share.DeepClone(); bad["career"] = model.Preset.Career == "全力" ? "集中" : "全力";
        Reject(bad.ToString(), "share rejects profession mismatch");
        bad = (JObject)share.DeepClone(); bad["personal"] = "unexpected";
        Reject(bad.ToString(), "share rejects unrelated settings");
        bad = (JObject)share.DeepClone(); bad["rules"]![0]!["card"] = "unknown card";
        Reject(bad.ToString(), "share rejects unknown catalog card");
        bad = (JObject)share.DeepClone(); ((JArray)bad["rules"]!).Add(bad["rules"]![0]!.DeepClone());
        Reject(bad.ToString(), "share rejects duplicate normalized card");
        bad = (JObject)share.DeepClone(); bad["rules"]![0]!["enabled"] = "true";
        Reject(bad.ToString(), "share rejects coerced booleans");
        bad = (JObject)share.DeepClone(); bad["rules"]![0]!["conditions"] = new JArray(new JArray(new JObject { ["field"] = "fullPower", ["min"] = 1.5 }));
        Reject(bad.ToString(), "share rejects fractional bounds");
        var draftBeforeFailure = JsonConvert.SerializeObject(model.Document);
        try { model.ImportShare(bad.ToString()); }
        catch (InvalidDataException) { }
        check(JsonConvert.SerializeObject(model.Document) == draftBeforeFailure, "invalid import preserves the entire editing draft");

        check(model.Save(), "pre-share fixture saves");
        var disk = File.ReadAllText(model.ConfigPath);
        var before = PlayCardPolicy.Clone(model.Document);
        File.WriteAllText(Path.Combine(output, "profession-before-document.json"), disk);
        File.WriteAllText(Path.Combine(output, "profession-share.json"), text);
        model.ImportShare(text);
        check(model.Dirty && File.ReadAllText(model.ConfigPath) == disk, "import only changes draft until explicit save");
        check(before.Presets.Where(preset => preset.Id != before.Active).All(preset =>
            JsonConvert.SerializeObject(preset) == JsonConvert.SerializeObject(model.Document.Presets.First(other => other.Id == preset.Id))), "import preserves other professions");
        check(model.Preset.Name == source.Name && model.Preset.Id == source.Id && model.Preset.Hand.SequenceEqual(source.Hand), "import preserves current preset metadata and preview hand");
        check(model.Preset.Rules[0].SingleCopy && !model.Preset.Rules[1].Enabled && model.Preset.Hold.Ordered, "share round trip preserves single-copy enabled and hold settings");
        check(model.Preset.Rules[0].Id != source.Rules[0].Id, "import regenerates local rule identities");
        check(model.Save(), "import saves through native atomic persistence");
        File.Copy(model.ConfigPath, Path.Combine(output, "profession-import-document.json"), true);
        using (var reload = new CardRulesViewModel(Path.GetDirectoryName(Path.GetDirectoryName(model.ConfigPath))!))
            check(reload.Preset.Rules[0].PriorityOverrides.Count == 2 && reload.Preset.Rules[0].FollowUp?.WaitTurns == 2, "advanced settings survive reload");
        UiChecks(model, output, check);
    }

    private static void UiChecks(CardRulesViewModel model, string output, Action<bool, string> check)
    {
        model.Preset.Hand = ["イメトレ", "届いて！"];
        var previousMotion = LiquidTheme.ReduceMotion;
        LiquidTheme.ReduceMotion = true;
        foreach (var theme in new[] { ThemeVariant.Light, ThemeVariant.Dark })
        {
            Application.Current!.RequestedThemeVariant = theme;
            var view = new CardRulesView(model);
            var window = new Window { Width = 1120, Height = 1000, Content = view };
            window.Bind(Window.ForegroundProperty, window.GetResourceObservable("LgTextMainBrush"));
            window.Bind(Window.BackgroundProperty, window.GetResourceObservable("LgCanvasBrush"));
            window.Show(); Dispatcher.UIThread.RunJobs();
            T Named<T>(string name) where T : Control => window.GetVisualDescendants().OfType<T>().Last(control => control.IsEffectivelyVisible && AutomationProperties.GetName(control) == ExText.T(name));
            void Click(string name)
            {
                Named<Button>(name).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                Dispatcher.UIThread.RunJobs();
            }
            void Shot(string name)
            {
                Dispatcher.UIThread.RunJobs();
                using var image = new RenderTargetBitmap(new PixelSize(1120, 1000));
                image.Render(window); image.Save(Path.Combine(output, name + "-" + theme + ".png"));
            }
            check(Named<Button>("管理与分享当前预设").IsVisible, "builtin profession exposes sharing " + theme);
            var beforeCancel = JsonConvert.SerializeObject(model.Document);
            var cancelledStorage = DispatchProxy.Create<IStorageProvider, CancelledStorage>();
            view.ImportProfession(cancelledStorage).GetAwaiter().GetResult();
            view.ExportProfession(cancelledStorage).GetAwaiter().GetResult();
            check(JsonConvert.SerializeObject(model.Document) == beforeCancel, "cancelled import and export pickers preserve the draft " + theme);
            Click("模拟状态");
            Named<ToggleSwitch>("全力值已识别").IsChecked = true;
            Named<NumericUpDown>("模拟全力值").Value = 75;
            Named<ComboBox>("模拟指针状态").SelectedIndex = 1;
            Shot("battle-state");
            Click("应用模拟状态");
            check(model.PreviewState.FullPower == 75 && model.PreviewState.Stance == "全力", "simulation applies explicit state " + theme);
            check(model.Preview().First(item => item.Card.Name == "イメトレ").Priority == 73, "simulation evaluates first matching override " + theme);
            var rule = model.Selected!;
            view.OpenRuleConditions(rule); Dispatcher.UIThread.RunJobs();
            var modal = window.GetVisualDescendants().OfType<GlassPanel>().Last(panel => panel.Classes.Contains("dialog"));
            modal.GetVisualDescendants().OfType<Button>().First(button => AutomationProperties.GetName(button) == ExText.T("添加或条件组"))
                .RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Dispatcher.UIThread.RunJobs();
            Shot("battle-conditions");
            Click("应用到草稿");
            check(rule.Conditions.Count > 0 && model.Dirty, "conditions editor applies draft " + theme);
            var conditionCount = rule.Conditions.Count;
            view.OpenRuleConditions(rule); Dispatcher.UIThread.RunJobs();
            Named<Button>("添加或条件组").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Click("取消");
            check(rule.Conditions.Count == conditionCount, "cancelled condition editor discards its private draft " + theme);
            view.OpenFollowUp(rule); Dispatcher.UIThread.RunJobs();
            Named<NumericUpDown>("连携等待回合").Value = 4;
            Shot("battle-followup");
            Click("应用到草稿");
            check(rule.FollowUp?.WaitTurns == 4, "follow-up editor applies draft " + theme);
            view.OpenPresetBattle(); Dispatcher.UIThread.RunJobs();
            Named<ToggleSwitch>("按名单顺序保留选牌").IsChecked = false;
            Shot("battle-hold");
            Click("应用到草稿");
            check(!model.Preset.Hold.Ordered, "hold editor applies draft " + theme);
            window.Close();
        }
        LiquidTheme.ReduceMotion = previousMotion;
    }

    public class CancelledStorage : DispatchProxy
    {
        protected override object? Invoke(MethodInfo? method, object?[]? arguments) => method?.Name switch
        {
            nameof(IStorageProvider.OpenFilePickerAsync) => Task.FromResult<IReadOnlyList<IStorageFile>>([]),
            nameof(IStorageProvider.SaveFilePickerAsync) => Task.FromResult<IStorageFile?>(null),
            _ => throw new NotSupportedException(method?.Name)
        };
    }
}
