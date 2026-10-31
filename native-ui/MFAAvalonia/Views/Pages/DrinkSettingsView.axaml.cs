using MFAAvalonia.Helper;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Microsoft.Extensions.DependencyInjection;
using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.ViewModels.Pages;
using System.Linq;

namespace MFAAvalonia.Views.Pages;

public partial class DrinkSettingsView : UserControl
{
    private void RefreshLanguage(object? sender, LanguageHelper.LanguageEventArgs e)
    { var model = DataContext; DataContext = null; DataContext = model; }

    public DrinkSettingsView()
    {
        InitializeComponent();
        AttachedToVisualTree += (_, _) => LanguageHelper.LanguageChanged += RefreshLanguage;
        DetachedFromVisualTree += (_, _) => LanguageHelper.LanguageChanged -= RefreshLanguage;
        if (!Design.IsDesignMode && App.Services != null) DataContext = App.Services.GetRequiredService<DrinkSettingsViewModel>();
    }

    /// <summary>Card trigger for "指定卡牌前使用"; uses the shared picker over the full card library.</summary>
    private void PickCard(object? sender, RoutedEventArgs e)
    {
        if (sender is not Button { DataContext: DrinkRuleViewModel drink } || DataContext is not DrinkSettingsViewModel vm) return;
        var cards = vm.LoadCards();
        if (cards.Count == 0) { LiquidModal.Notice(this, "卡库读取失败", vm.Status); return; }
        LiquidCardPicker.Open(this, new CardPickerOptions
        {
            Cards = cards,
            Image = c => c.Thumbnail,
            Title = vm.TitleFor(drink),
            Initial = cards.FirstOrDefault(c => c.Name == drink.Card),
            Confirm = card => drink.Card = card.Name,
        });
    }

    private void OpenRules(object? sender, RoutedEventArgs e) => LiquidNavigation.Navigate(LiquidPages.CardRules);
}
