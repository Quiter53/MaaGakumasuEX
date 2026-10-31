using MFAAvalonia.Helper;
using System.Linq;
using MFAAvalonia.ViewModels.Pages;
namespace MFAAvalonia.Views.Pages;

public sealed partial class CardRulesView
{
    /// <summary>
    /// Opens the shared card picker for: a new rule (default), replacing the selected rule's
    /// card, the 国民 combo target, or a simulated hand card.
    /// </summary>
    public void OpenPicker(bool change = false, bool combo = false, bool hand = false)
    {
        var preset = Model.Preset;
        var selected = Model.Selected;
        string title = hand ? "添加模拟手牌" : combo ? "选择要生效的牌" : change ? "更换卡牌" : "选择技能卡";
        LiquidCardPicker.Open(this, new CardPickerOptions
        {
            Cards = Model.Cards,
            Image = Model.Image,
            Title = title,
            ConfirmText = !change && !combo && !hand ? "创建规则" : "使用此卡",
            // 国民配合目标排除国民自身（与原程序候选规则一致）。
            Eligible = combo ? c => PlayCardPolicy.Key(c.Name) != "国民的アイドル" : null,
            Priority = hand ? preset.Rules.Select(r => r.Card).ToList() : null,
            PriorityHeader = "当前预设",
            Badge = hand ? c => preset.Hand.Contains(c.Name) ? "已在模拟手牌" : null : null,
            Blocked = !hand && !combo
                ? c => preset.Rules.Any(r => r != (change ? selected : null) && PlayCardPolicy.Key(r.Card) == PlayCardPolicy.Key(c.Name)) ? "已有规则" : null
                : null,
            Confirm = card =>
            {
                if (hand)
                {
                    if (preset.Hand.Count < 5) Model.SetHand(preset.Hand.Count, card.Name);
                    UpdateHand();
                }
                else if (combo && selected != null)
                {
                    selected.ComboTarget = card.Name; Model.Mark();
                    RenderDetail(false); RefreshRule(selected);
                }
                else if (change)
                {
                    if (Model.ChangeRuleCard(card.Name)) { RenderList(); RenderDetail(true); UpdateHand(); }
                }
                else
                {
                    Model.AddRule(card.Name);
                    RenderList(); foreach (var i in _listItems.Values) UpdateItemState(i);
                    RenderDetail(true); UpdateHand();
                }
                UpdateStatus();
            },
        });
    }
}
