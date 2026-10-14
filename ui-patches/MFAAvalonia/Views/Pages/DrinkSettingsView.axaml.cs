using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Interactivity;
using Microsoft.Extensions.DependencyInjection;
using MFAAvalonia.ViewModels.Pages;

namespace MFAAvalonia.Views.Pages;

public partial class DrinkSettingsView : UserControl
{
    private Button? _opener;
    private void FilterDrinks(object? sender,RoutedEventArgs e)
    {
        if(sender is Button button && int.TryParse(button.Tag?.ToString(),out var index) && DataContext is DrinkSettingsViewModel vm) vm.TargetFilter=index;
    }
    public DrinkSettingsView()
    {
        InitializeComponent();
        if(!Design.IsDesignMode && App.Services != null) DataContext=App.Services.GetRequiredService<DrinkSettingsViewModel>();
    }
    private void PickCard(object? sender,RoutedEventArgs e)
    {
        if(sender is Button { DataContext: DrinkRuleViewModel drink } button && DataContext is DrinkSettingsViewModel vm)
        { _opener=button; vm.OpenPicker(drink); this.FindControl<TextBox>("CardSearchInput")?.Focus(); }
    }
    private void ClosePicker(object? sender,RoutedEventArgs e) { (DataContext as DrinkSettingsViewModel)?.ClosePicker(); _opener?.Focus(); }
    private void ChooseCard(object? sender,RoutedEventArgs e)
    { if(sender is Button { DataContext: CardListCard card } && DataContext is DrinkSettingsViewModel vm) { vm.ChooseCard(card); _opener?.Focus(); } }
    private void PickerKeyDown(object? sender,KeyEventArgs e)
    { if(e.Key==Key.Escape) { ClosePicker(sender,e); e.Handled=true; } }
}
