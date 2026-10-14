using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using MFAAvalonia.Extensions;
using MFAAvalonia.ViewModels.Pages;
using Microsoft.Extensions.DependencyInjection;

namespace MFAAvalonia.Views.Pages;

public partial class CardListSettingsView : UserControl
{
    private Point? _pressedPosition;
    private CardListCard? _dragCard;

    private CardListSettingsViewModel? ViewModel => DataContext as CardListSettingsViewModel;

    public CardListSettingsView()
    {
        InitializeComponent();
        if (!Design.IsDesignMode && App.Services != null)
            DataContext = App.Services.GetRequiredService<CardListSettingsViewModel>();
    }

    private void Card_PointerPressed(object? sender, PointerPressedEventArgs e)
    {
        if (sender is not Control { DataContext: CardListCard card } control)
            return;
        var point = e.GetCurrentPoint(control);
        if (!point.Properties.IsLeftButtonPressed)
            return;
        _dragCard = card;
        _pressedPosition = e.GetPosition(control);
    }

    private void Card_PointerMoved(object? sender, PointerEventArgs e)
    {
        if (sender is not Control control || _dragCard == null || _pressedPosition == null)
            return;
        var point = e.GetCurrentPoint(control);
        if (!point.Properties.IsLeftButtonPressed)
        {
            _dragCard = null;
            _pressedPosition = null;
            return;
        }

        var current = e.GetPosition(control);
        var delta = current - _pressedPosition.Value;
        if (delta.X * delta.X + delta.Y * delta.Y < 25)
            return;

        var data = new DataTransfer();
        var item = new DataTransferItem();
        item.SetText(_dragCard.Key);
        data.Add(item);
        _dragCard = null;
        _pressedPosition = null;
        _ = DragDrop.DoDragDropAsync(e, data, DragDropEffects.Copy);
    }

    private void List_DragOver(object? sender, DragEventArgs e)
    {
        if (ViewModel != null && e.DataTransfer.TryGetText(out var key) && ViewModel.FindCard(key ?? string.Empty) != null)
            e.DragEffects = DragDropEffects.Copy;
        else
            e.DragEffects = DragDropEffects.None;
        e.Handled = true;
    }

    private void List_Drop(object? sender, DragEventArgs e)
    {
        if (ViewModel == null || sender is not Border target ||
            !e.DataTransfer.TryGetText(out var key) || ViewModel.FindCard(key ?? string.Empty) is not { } card)
            return;

        if (target.Tag?.ToString() == "blacklist")
            ViewModel.AddToBlacklist(card);
        else if (target.Tag?.ToString() == "whitelist")
            ViewModel.AddToWhitelist(card);
        else if (target.Tag?.ToString() == "replacement")
            ViewModel.AddToReplacement(card);
        e.Handled = true;
    }

    private void RemoveBlacklist_Click(object? sender, Avalonia.Interactivity.RoutedEventArgs e)
    {
        if (sender is Button { DataContext: CardListCard card })
            ViewModel?.RemoveFromBlacklist(card);
    }

    private void RemoveWhitelist_Click(object? sender, Avalonia.Interactivity.RoutedEventArgs e)
    {
        if (sender is Button { DataContext: CardListCard card })
            ViewModel?.RemoveFromWhitelist(card);
    }

    private void RemoveReplacement_Click(object? sender, Avalonia.Interactivity.RoutedEventArgs e)
    {
        if (sender is Button { DataContext: CardListCard card })
            ViewModel?.RemoveFromReplacement(card);
    }
}
