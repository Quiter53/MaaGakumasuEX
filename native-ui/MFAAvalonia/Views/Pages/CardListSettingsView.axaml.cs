using MFAAvalonia.Helper;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.Extensions;
using MFAAvalonia.ViewModels.Pages;
using Microsoft.Extensions.DependencyInjection;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;

namespace MFAAvalonia.Views.Pages;

/// <summary>One virtualized row of catalog tiles.</summary>
public sealed class CardRow(IReadOnlyList<CardListCard> cards)
{
    public IReadOnlyList<CardListCard> Cards { get; } = cards;
}

public partial class CardListSettingsView : UserControl
{
    private const double TileWidth = 110, TileSpacing = 10;

    private void RefreshLanguage(object? sender, LanguageHelper.LanguageEventArgs e)
    { var model = DataContext; DataContext = null; DataContext = model; }

    private Point? _pressedPosition;
    private CardListCard? _dragCard;
    private int _columns = 1;
    private readonly ObservableCollection<CardRow> _rows = new();
    private bool _rowsQueued;

    private CardListSettingsViewModel? ViewModel => DataContext as CardListSettingsViewModel;
    private CardListSettingsViewModel? _subscribed;
    private Button? _dragSource;

    public CardListSettingsView()
    {
        InitializeComponent();
        AttachedToVisualTree += (_, _) => LanguageHelper.LanguageChanged += RefreshLanguage;
        DetachedFromVisualTree += (_, _) => LanguageHelper.LanguageChanged -= RefreshLanguage;
        CatalogRows.ItemsSource = _rows;
        CatalogScroll.SizeChanged += (_, _) => QueueRows();
        BucketSelector.SelectionChanged += (_, _) => UpdateActiveBucket();
        foreach (var bucket in new[] { WhitelistBucket, BlacklistBucket, ReplacementBucket })
        {
            bucket.AddHandler(DragDrop.DragOverEvent, List_DragOver);
            bucket.AddHandler(DragDrop.DropEvent, List_Drop);
            bucket.AddHandler(DragDrop.DragLeaveEvent, (_, _) => bucket.Classes.Set("dragover", false));
        }
        // Release the previous view model's subscription, not the new one's.
        DataContextChanged += (_, _) =>
        {
            if (_subscribed != null) _subscribed.VisibleCards.CollectionChanged -= OnVisibleChanged;
            _subscribed = ViewModel;
            if (_subscribed != null) _subscribed.VisibleCards.CollectionChanged += OnVisibleChanged;
            QueueRows();
        };
        // Catalog tiles are Buttons: Button marks PointerPressed handled (and captures the pointer), so
        // ordinary handlers never saw the press and dragging never started. Listen with handledEventsToo.
        CatalogRows.AddHandler(PointerPressedEvent, Card_PointerPressed, RoutingStrategies.Bubble, handledEventsToo: true);
        CatalogRows.AddHandler(PointerMovedEvent, Card_PointerMoved, RoutingStrategies.Bubble, handledEventsToo: true);
        if (!Design.IsDesignMode && App.Services != null)
            DataContext = App.Services.GetRequiredService<CardListSettingsViewModel>();
        UpdateActiveBucket();
    }

    private void OnVisibleChanged(object? sender, System.Collections.Specialized.NotifyCollectionChangedEventArgs e) => QueueRows();

    /// <summary>Re-chunks the filtered cards into rows that fit the current width (rows are virtualized).</summary>
    private void QueueRows()
    {
        if (_rowsQueued) return;
        _rowsQueued = true;
        Dispatcher.UIThread.Post(() =>
        {
            _rowsQueued = false;
            var width = CatalogScroll.Bounds.Width - 12;
            _columns = Math.Max(1, (int)((width + TileSpacing) / (TileWidth + TileSpacing)));
            var cards = ViewModel?.VisibleCards.ToList() ?? [];
            _rows.Clear();
            for (int i = 0; i < cards.Count; i += _columns) _rows.Add(new CardRow(cards.Skip(i).Take(_columns).ToList()));
        }, DispatcherPriority.Background);
    }

    private string ActiveBucket => BucketSelector.SelectedIndex switch { 1 => "blacklist", 2 => "replacement", _ => "whitelist" };

    private void UpdateActiveBucket()
    {
        WhitelistBucket.Classes.Set("active", ActiveBucket == "whitelist");
        BlacklistBucket.Classes.Set("active", ActiveBucket == "blacklist");
        ReplacementBucket.Classes.Set("active", ActiveBucket == "replacement");
    }

    private void Add(string bucket, CardListCard card)
    {
        if (ViewModel == null) return;
        if (bucket == "blacklist") ViewModel.AddToBlacklist(card);
        else if (bucket == "replacement") ViewModel.AddToReplacement(card);
        else ViewModel.AddToWhitelist(card);
    }

    private void CatalogCard_Click(object? sender, RoutedEventArgs e)
    {
        if (sender is Button { DataContext: CardListCard card }) Add(ActiveBucket, card);
    }

    private void RemoveChip_Click(object? sender, RoutedEventArgs e)
    {
        if (sender is not Button { DataContext: CardListCard card } button || ViewModel == null) return;
        var bucket = button.FindAncestorOfType<ItemsControl>()?.Tag?.ToString();
        if (button.FindAncestorOfType<Border>() is { } chip) LiquidStage.PlayDeparture(chip);
        if (bucket == "blacklist") ViewModel.RemoveFromBlacklist(card);
        else if (bucket == "replacement") ViewModel.RemoveFromReplacement(card);
        else ViewModel.RemoveFromWhitelist(card);
    }

    private void Card_PointerPressed(object? sender, PointerPressedEventArgs e)
    {
        if ((e.Source as Visual)?.FindAncestorOfType<Button>(true) is not { DataContext: CardListCard card } tile || !tile.Classes.Contains("lg-catalog-card")) return;
        if (!e.GetCurrentPoint(tile).Properties.IsLeftButtonPressed) return;
        _dragCard = card;
        _dragSource = tile;
        _pressedPosition = e.GetPosition(tile);
    }

    private void Card_PointerMoved(object? sender, PointerEventArgs e)
    {
        if (_dragSource is not { } tile || _dragCard == null || _pressedPosition == null) return;
        if (!e.GetCurrentPoint(tile).Properties.IsLeftButtonPressed) { ResetDrag(); return; }
        var delta = e.GetPosition(tile) - _pressedPosition.Value;
        if (delta.X * delta.X + delta.Y * delta.Y < 25) return;
        var data = new DataTransfer();
        var item = new DataTransferItem();
        item.SetText(_dragCard.Key);
        data.Add(item);
        // Past the threshold this is a drag, not a click: releasing the button's capture clears its
        // pressed state, so dropping cannot also fire Click and add the card a second time.
        e.Pointer.Capture(null);
        ResetDrag();
        _ = DragDrop.DoDragDropAsync(e, data, DragDropEffects.Copy);
    }

    private void ResetDrag() { _dragCard = null; _dragSource = null; _pressedPosition = null; }

    private void List_DragOver(object? sender, DragEventArgs e)
    {
        var ok = ViewModel != null && e.DataTransfer.TryGetText(out var key) && ViewModel.FindCard(key ?? string.Empty) != null;
        e.DragEffects = ok ? DragDropEffects.Copy : DragDropEffects.None;
        if (sender is Control c) c.Classes.Set("dragover", ok);
        e.Handled = true;
    }

    private void List_Drop(object? sender, DragEventArgs e)
    {
        if (sender is Control c) c.Classes.Set("dragover", false);
        if (ViewModel == null || sender is not Control target ||
            !e.DataTransfer.TryGetText(out var key) || ViewModel.FindCard(key ?? string.Empty) is not { } card)
            return;
        Add(target.Tag?.ToString() ?? "whitelist", card);
        e.Handled = true;
    }
}
