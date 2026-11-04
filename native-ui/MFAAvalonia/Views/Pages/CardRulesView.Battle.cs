using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Automation;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Platform.Storage;
using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.Helper;
using MFAAvalonia.ViewModels.Pages;

namespace MFAAvalonia.Views.Pages;

public sealed partial class CardRulesView
{
    private bool _sharing;

    private Control AdvancedRuleBox(PlayCardRule rule)
    {
        return new StackPanel
        {
            Spacing = 10,
            Children =
            {
                Text("状态条件与优先级", 12.5, true),
                Text($"使用条件 {rule.Conditions.Count} 组 · 条件优先级 {rule.PriorityOverrides.Count} 项 · 后续候选 {rule.FollowUp?.Cards.Count ?? 0} 张", 12, false, "lg-sub"),
                new WrapPanel
                {
                    ItemSpacing = 8, LineSpacing = 8,
                    Children = { Btn("编辑状态条件", () => OpenRuleConditions(rule), "lg-sm"), Btn("编辑后续候选", () => OpenFollowUp(rule), "lg-sm") }
                }
            }
        };
    }

    private void ShowBattleEditor(string title, Control body, Action apply, double width = 720)
    {
        LiquidModal? modal = null;
        var error = Text("", 12, false, "lg-danger");
        var applyButton = Btn("应用到草稿", () =>
        {
            try
            {
                apply();
                modal?.Close();
                RenderDetail(false); RenderList(); UpdateHand();
            }
            catch (Exception exception) { error.Text = ExText.T(exception.Message); }
        }, "lg-primary");
        var footer = new StackPanel
        {
            Spacing = 8,
            Children =
            {
                error,
                new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, HorizontalAlignment = HorizontalAlignment.Right,
                    Children = { Btn("取消", () => modal?.RequestCancel()), applyButton } }
            }
        };
        modal = LiquidModal.Show(this, LiquidModal.Layout(title, "应用到草稿后，点击页面保存生效", body, footer, () => modal?.RequestCancel()), width, 680);
    }

    private void ValidateRuleDraft(PlayCardRule draft)
    {
        var document = PlayCardPolicy.Clone(Model.Document);
        var preset = document.Presets.First(item => item.Id == Model.Preset.Id);
        preset.Rules[preset.Rules.FindIndex(item => item.Id == draft.Id)] = draft;
        PlayCardPolicy.Validate(document, Model.Cards);
    }

    public void OpenRuleConditions(PlayCardRule rule)
    {
        var draft = PlayCardPolicy.Clone(rule);
        var body = new StackPanel { Spacing = 14 };
        body.Children.Add(Text("每组条件全部满足即可命中；任一组命中即生效。未知状态不命中对应条件。", 12, false, "lg-sub"));
        body.Children.Add(Text("使用条件", 13, true));
        body.Children.Add(ConditionEditor(draft.Conditions));
        body.Children.Add(new Border { Classes = { "lg-divider" } });
        body.Children.Add(Text("条件优先级", 13, true));
        body.Children.Add(Text("从上到下使用首个命中项，覆盖同档位曲线权重。", 12, false, "lg-sub"));
        var overrides = new StackPanel { Spacing = 10 };
        body.Children.Add(overrides);
        void RenderOverrides()
        {
            overrides.Children.Clear();
            foreach (var item in draft.PriorityOverrides)
            {
                var current = item;
                var position = draft.PriorityOverrides.IndexOf(item);
                var up = Btn("上移", () => { draft.PriorityOverrides.Remove(current); draft.PriorityOverrides.Insert(position - 1, current); RenderOverrides(); }, "lg-sm");
                up.IsEnabled = position > 0;
                var row = new WrapPanel { ItemSpacing = 8, LineSpacing = 6, Children =
                {
                    Text("命中权重", 12), Stepper("命中权重", item.Priority, 0, 100, value => current.Priority = value), up,
                    Btn("删除优先级", () => { draft.PriorityOverrides.Remove(current); RenderOverrides(); }, "lg-sm")
                } };
                overrides.Children.Add(Box(new StackPanel { Spacing = 8, Children = { row, ConditionEditor(item.Conditions) } }));
            }
            var add = Btn("添加条件优先级", () =>
            {
                draft.PriorityOverrides.Add(new PlayCardPriorityOverride { Conditions = [[new PlayCardCondition { Min = 1, Max = 3 }]] });
                RenderOverrides();
            }, "lg-sm");
            add.IsEnabled = draft.PriorityOverrides.Count < 16;
            overrides.Children.Add(add);
        }
        RenderOverrides();
        ShowBattleEditor("状态条件与优先级", body, () =>
        {
            ValidateRuleDraft(draft);
            rule.Conditions = draft.Conditions;
            rule.PriorityOverrides = draft.PriorityOverrides;
            Model.Mark();
        });
    }

    private Control ConditionEditor(List<List<PlayCardCondition>> groups)
    {
        var host = new StackPanel { Spacing = 8 };
        void Render()
        {
            host.Children.Clear();
            foreach (var group in groups)
            {
                var currentGroup = group;
                var atoms = new StackPanel { Spacing = 8 };
                atoms.Children.Add(Text($"条件组 {groups.IndexOf(group) + 1} · 组内全部满足", 12, true));
                foreach (var atom in group)
                {
                    var current = atom;
                    var fields = new[] { "remaining", "fullPower", "stance" };
                    var field = new ComboBox { ItemsSource = new[] { ExText.T("剩余回合"), ExText.T("全力值"), ExText.T("指针状态") }, SelectedIndex = Array.IndexOf(fields, atom.Field), MinWidth = 116 };
                    AutomationProperties.SetName(field, ExText.T("条件字段"));
                    field.SelectionChanged += (_, _) =>
                    {
                        if (field.SelectedIndex < 0) return;
                        current.Field = fields[field.SelectedIndex];
                        current.Min = current.Field == "remaining" ? 1 : current.Field == "fullPower" ? 0 : null;
                        current.Max = current.Field == "remaining" ? 30 : current.Field == "fullPower" ? 999 : null;
                        current.Value = current.Field == "stance" ? "全力" : null;
                        Render();
                    };
                    var row = new WrapPanel { ItemSpacing = 8, LineSpacing = 6, Children = { field } };
                    if (atom.Field == "stance")
                    {
                        var stance = new ComboBox { ItemsSource = PlayCardPolicy.Stances, SelectedItem = atom.Value, MinWidth = 110 };
                        AutomationProperties.SetName(stance, ExText.T("指针状态"));
                        stance.SelectionChanged += (_, _) => current.Value = stance.SelectedItem as string;
                        row.Children.Add(stance);
                    }
                    else
                    {
                        var minimum = atom.Field == "remaining" ? 1 : 0;
                        var maximum = atom.Field == "remaining" ? 30 : 999;
                        NumericUpDown Bound(string label, int? value, Action<int?> changed)
                        {
                            var input = new NumericUpDown { Value = value, Minimum = minimum, Maximum = maximum, Increment = 1, FormatString = "0", Width = 108, ClipValueToMinMax = true, Watermark = ExText.T("不限") };
                            AutomationProperties.SetName(input, ExText.T(label));
                            input.ValueChanged += (_, change) =>
                            {
                                if (change.NewValue == null) changed(null);
                                else if (decimal.Truncate(change.NewValue.Value) == change.NewValue.Value) changed((int)change.NewValue.Value);
                            };
                            return input;
                        }
                        row.Children.Add(Bound("条件下限，留空不限", atom.Min, value => current.Min = value));
                        row.Children.Add(Text("至", 12));
                        row.Children.Add(Bound("条件上限，留空不限", atom.Max, value => current.Max = value));
                    }
                    row.Children.Add(Btn("移除条件", () => { currentGroup.Remove(current); if (currentGroup.Count == 0) groups.Remove(currentGroup); Render(); }, "lg-sm"));
                    atoms.Children.Add(row);
                }
                var addAtom = Btn("添加且条件", () => { currentGroup.Add(new PlayCardCondition { Min = 1, Max = 30 }); Render(); }, "lg-sm");
                addAtom.IsEnabled = group.Count < 8;
                atoms.Children.Add(addAtom);
                host.Children.Add(Box(atoms, new Thickness(10)));
            }
            if (groups.Count == 0) host.Children.Add(Text("未设置条件", 12, false, "lg-sub"));
            var addGroup = Btn("添加或条件组", () => { groups.Add([new PlayCardCondition { Min = 1, Max = 30 }]); Render(); }, "lg-sm");
            addGroup.IsEnabled = groups.Count < 8;
            host.Children.Add(addGroup);
        }
        Render();
        return host;
    }

    private Control BattleCardList(List<string> cards, string? source = null)
    {
        var host = new StackPanel { Spacing = 8 };
        void Render()
        {
            host.Children.Clear();
            for (var index = 0; index < cards.Count; index++)
            {
                var position = index;
                var name = cards[index];
                var row = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), ColumnSpacing = 8 };
                row.Children.Add(Text($"{index + 1}. {name}", 12));
                var up = Btn("上移", () => { (cards[position - 1], cards[position]) = (cards[position], cards[position - 1]); Render(); }, "lg-sm");
                up.IsEnabled = index > 0;
                var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { up, Btn("移除", () => { cards.RemoveAt(position); Render(); }, "lg-sm") } };
                Grid.SetColumn(buttons, 1); row.Children.Add(buttons);
                host.Children.Add(row);
            }
            if (cards.Count == 0) host.Children.Add(Text("名单为空", 12, false, "lg-sub"));
            var add = Btn("添加候选卡", () => LiquidCardPicker.Open(this, new CardPickerOptions
            {
                Cards = Model.Cards, Image = Model.Image, Title = "添加候选卡",
                Eligible = card => source == null || PlayCardPolicy.Key(card.Name) != PlayCardPolicy.Key(source),
                Blocked = card => cards.Any(name => PlayCardPolicy.Key(name) == PlayCardPolicy.Key(card.Name)) ? "已在名单" : null,
                Confirm = card => { cards.Add(card.Name); Render(); }
            }), "lg-sm");
            add.IsEnabled = cards.Count < 50;
            host.Children.Add(add);
        }
        Render();
        return host;
    }

    public void OpenFollowUp(PlayCardRule rule)
    {
        var draft = PlayCardPolicy.Clone(rule);
        var followUp = draft.FollowUp ?? new PlayCardFollowUp();
        var policies = new[] { "normal", "reserveFirst", "comboOnly" };
        var policy = new ComboBox { ItemsSource = new[] { ExText.T("普通使用"), ExText.T("首次连携前保留"), ExText.T("仅连携使用") }, SelectedIndex = Array.IndexOf(policies, followUp.TargetPolicy), HorizontalAlignment = HorizontalAlignment.Stretch };
        AutomationProperties.SetName(policy, ExText.T("后续候选使用方式"));
        policy.SelectionChanged += (_, _) => { if (policy.SelectedIndex >= 0) followUp.TargetPolicy = policies[policy.SelectedIndex]; };
        var body = new StackPanel { Spacing = 12, Children =
        {
            Text(rule.Card, 13, true),
            Text("来源卡打出后，按名单顺序尝试可用候选，任一候选打出即完成连携。", 12, false, "lg-sub"),
            BattleCardList(followUp.Cards, rule.Card),
            Text("等待回合", 12, true), Stepper("连携等待回合", followUp.WaitTurns, 0, 6, value => followUp.WaitTurns = value),
            Text("等待 0 回合时仅尝试当回合；回合下降后解除。", 12, false, "lg-sub"),
            Text("后续候选使用方式", 12, true), policy,
            Text("首次连携前保留会在成功或超时后释放；仅连携使用只允许当前连携的目标出牌。", 12, false, "lg-sub")
        } };
        ShowBattleEditor("编辑后续候选", body, () =>
        {
            draft.FollowUp = followUp.Cards.Count == 0 ? null : followUp;
            ValidateRuleDraft(draft);
            rule.FollowUp = draft.FollowUp;
            Model.Mark();
        });
    }

    public void OpenPresetBattle()
    {
        var hold = PlayCardPolicy.Clone(Model.Preset.Hold);
        var ordered = new ToggleSwitch { Content = ExText.T("按名单顺序保留选牌"), IsChecked = hold.Ordered };
        AutomationProperties.SetName(ordered, ExText.T("按名单顺序保留选牌"));
        ordered.IsCheckedChanged += (_, _) => hold.Ordered = ordered.IsChecked == true;
        var body = new StackPanel { Spacing = 12, Children = { Text("保留选牌名单", 13, true), Text("用于全力、强气职业和热绿茶的保留选牌", 12, false, "lg-sub"), ordered, BattleCardList(hold.Cards), new Border { Classes = { "lg-divider" } }, Text("通用连携", 13, true) } };
        body.Children.Add(Text("在卡牌详情的「编辑后续候选」中设置来源卡与候选顺序。", 12, false, "lg-sub"));
        foreach (var rule in Model.Preset.Rules.Where(item => item.FollowUp != null))
            body.Children.Add(Text(rule.Card + " → " + string.Join(" / ", rule.FollowUp!.Cards), 12));
        ShowBattleEditor("保留选牌与连携", body, () =>
        {
            var document = PlayCardPolicy.Clone(Model.Document);
            document.Presets.First(item => item.Id == Model.Preset.Id).Hold = hold;
            PlayCardPolicy.Validate(document, Model.Cards);
            Model.Preset.Hold = hold;
            Model.Mark();
        });
    }

    public void OpenPreviewState()
    {
        var known = new ToggleSwitch { Content = ExText.T("剩余回合已识别"), IsChecked = Model.PreviewTurnKnown };
        var powerKnown = new ToggleSwitch { Content = ExText.T("全力值已识别"), IsChecked = Model.PreviewState.FullPower.HasValue };
        var power = Model.PreviewState.FullPower ?? 0;
        var powerInput = Stepper("模拟全力值", power, 0, 999, value => power = value);
        powerInput.IsEnabled = powerKnown.IsChecked == true;
        powerKnown.IsCheckedChanged += (_, _) => powerInput.IsEnabled = powerKnown.IsChecked == true;
        var stances = new string?[] { null, "全力", "強気", "温存" };
        var stance = new ComboBox { ItemsSource = new[] { ExText.T("未知"), "全力", "強気", "温存" }, SelectedIndex = Math.Max(0, Array.IndexOf(stances, Model.PreviewState.Stance)), HorizontalAlignment = HorizontalAlignment.Stretch };
        AutomationProperties.SetName(known, ExText.T("剩余回合已识别"));
        AutomationProperties.SetName(powerKnown, ExText.T("全力值已识别"));
        AutomationProperties.SetName(stance, ExText.T("模拟指针状态"));
        LiquidModal? modal = null;
        var body = new StackPanel { Spacing = 12, Children =
        {
            Text("模拟使用条件与优先级；连携进度由实际战斗记录。", 12, false, "lg-sub"), known, powerKnown, powerInput, Text("指针状态", 12, true), stance
        } };
        var done = Btn("应用模拟状态", () =>
        {
            Model.PreviewTurnKnown = known.IsChecked == true;
            Model.PreviewState = new PlayCardState { FullPower = powerKnown.IsChecked == true ? power : null, Stance = stances[Math.Max(0, stance.SelectedIndex)] };
            UpdateHand(); UpdateCurveStatus(); modal?.Close();
        }, "lg-primary");
        modal = LiquidModal.Show(this, LiquidModal.Layout("模拟状态", null, body, done, () => modal?.RequestCancel()), 430);
    }

    public async Task ExportProfession(IStorageProvider? storage = null)
    {
        storage ??= TopLevel.GetTopLevel(this)?.StorageProvider;
        if (_sharing || storage == null) return;
        _sharing = true;
        try
        {
            var text = Model.ExportShare();
            var file = await storage.SaveFilePickerAsync(new FilePickerSaveOptions
            {
                Title = ExText.T("导出当前职业规则"), SuggestedFileName = "EX-" + Model.Preset.Career + ".json", DefaultExtension = "json",
                FileTypeChoices = [new FilePickerFileType("JSON") { Patterns = ["*.json"] }]
            });
            if (file == null) return;
            using var stream = await file.OpenWriteAsync();
            if (stream.CanSeek) stream.SetLength(0);
            using var writer = new StreamWriter(stream);
            await writer.WriteAsync(text);
            await writer.FlushAsync();
            LiquidToast.Success(ExText.T("职业规则已导出"), ExText.T("导出内容来自当前编辑草稿"));
        }
        catch (Exception exception) { LiquidModal.Notice(this, "导出失败", exception.Message); }
        finally { _sharing = false; }
    }

    public async Task ImportProfession(IStorageProvider? storage = null)
    {
        storage ??= TopLevel.GetTopLevel(this)?.StorageProvider;
        if (_sharing || storage == null) return;
        _sharing = true;
        var presetId = Model.Preset.Id;
        try
        {
            var files = await storage.OpenFilePickerAsync(new FilePickerOpenOptions
            {
                Title = ExText.T("导入职业规则"), AllowMultiple = false,
                FileTypeFilter = [new FilePickerFileType("JSON") { Patterns = ["*.json"] }]
            });
            if (files.Count == 0) return;
            using var stream = await files[0].OpenReadAsync();
            using var reader = new StreamReader(stream);
            var buffer = new char[PlayCardProfessionShare.MaximumBytes + 1];
            var count = await reader.ReadBlockAsync(buffer, 0, buffer.Length);
            if (count > PlayCardProfessionShare.MaximumBytes) throw new InvalidDataException("职业分享文件过大");
            var text = new string(buffer, 0, count);
            if (Model.Preset.Id != presetId) throw new InvalidDataException("当前预设已切换，请重新导入");
            PlayCardProfessionShare.Read(text, Model.Preset.Career, Model.Cards);
            LiquidModal.Confirm(this, "导入职业规则", "替换当前职业的出牌规则、限一张和保留名单，保存后生效", "导入到草稿", () =>
            {
                try
                {
                    if (Model.Preset.Id != presetId) throw new InvalidDataException("当前预设已切换，请重新导入");
                    Model.ImportShare(text);
                    _listItems.Clear(); RenderList(); RenderDetail(true); UpdateHand();
                }
                catch (Exception exception) { LiquidModal.Notice(this, "导入失败", exception.Message); }
            });
        }
        catch (Exception exception) { LiquidModal.Notice(this, "导入失败", exception.Message); }
        finally { _sharing = false; }
    }
}
