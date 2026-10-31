using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Helper;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// In-window glass dialog (prototype modal): blurred scrim, pop-in card, Escape and
/// scrim click cancel, Tab cycles inside the dialog and focus returns to the opener.
/// Reopening while an exit animation runs cancels the exit; the closing copy is not
/// interactive and is removed when its animation finishes.
/// </summary>
public sealed class LiquidModal
{
    private static readonly List<LiquidModal> Open = new();

    private readonly OverlayLayer _layer;
    private readonly Panel _root;
    private readonly Border _scrim;
    private readonly GlassPanel _card;
    private readonly IInputElement? _returnFocus;
    private bool _closing;

    /// <summary>Called for Escape, scrim click and the close (×) button. Default closes.</summary>
    public Action? Cancel { get; set; }
    public event Action? Closed;
    public bool IsOpen => !_closing && _root.Parent != null;
    public GlassPanel Card => _card;

    private LiquidModal(OverlayLayer layer, Control content, double width, double? height, bool closeOnScrim)
    {
        _layer = layer;
        var top = TopLevel.GetTopLevel(layer);
        _returnFocus = top?.FocusManager?.GetFocusedElement();
        _scrim = new Border { Opacity = 0 };
        _scrim.Bind(Border.BackgroundProperty, _scrim.GetResourceObservable("LgModalScrimBrush"));
        _scrim.Transitions = new Avalonia.Animation.Transitions
        {
            new Avalonia.Animation.DoubleTransition { Property = Visual.OpacityProperty, Duration = TimeSpan.FromMilliseconds(220) }
        };
        if (closeOnScrim) _scrim.PointerPressed += (_, e) => { if (e.Source == _scrim) { RequestCancel(); e.Handled = true; } };
        _card = new GlassPanel
        {
            Classes = { "elevated", "dialog" },
            BlurRadius = 44,
            Padding = new Thickness(20),
            Width = width,
            HorizontalAlignment = HorizontalAlignment.Center,
            VerticalAlignment = VerticalAlignment.Center,
            Content = content,
            ClipContent = true,
            RenderTransformOrigin = RelativePoint.Center,
        };
        if (height is { } h) _card.Height = h;
        KeyboardNavigation.SetTabNavigation(_card, KeyboardNavigationMode.Cycle);
        var scrimBlur = new BackdropBlur { Radius = 20, Saturation = 1.65 };
        scrimBlur.Bind(Visual.OpacityProperty, _scrim.GetObservable(Visual.OpacityProperty));
        var scaled = new LayoutTransformControl { Child = new Panel { Children = { scrimBlur, _scrim, _card } } };
        scaled.Bind(LayoutTransformControl.LayoutTransformProperty, new Avalonia.Data.Binding(nameof(FontService.ScaleTransform)) { Source = FontService.Instance });
        _root = new Panel { Children = { scaled } };
        _root.AddHandler(InputElement.KeyDownEvent, OnKeyDown, RoutingStrategies.Tunnel);
        AutomationHelper(_card);
    }

    private static void AutomationHelper(Control card) =>
        Avalonia.Automation.AutomationProperties.SetIsOffscreenBehavior(card, Avalonia.Automation.IsOffscreenBehavior.Default);

    /// <summary>Shows <paramref name="content"/> in a dialog over the window that hosts <paramref name="anchor"/>.</summary>
    public static LiquidModal? Show(Visual anchor, Control content, double width = 520, double? height = null, bool closeOnScrim = true)
    {
        var layer = OverlayLayer.GetOverlayLayer(anchor);
        if (layer == null) return null;
        var modal = new LiquidModal(layer, content, width, height, closeOnScrim);
        modal.Attach();
        return modal;
    }

    private void Attach()
    {
        void Size() { _root.Width = _layer.Bounds.Width; _root.Height = _layer.Bounds.Height; _card.MaxHeight = Math.Max(240, _layer.Bounds.Height - 60); _card.MaxWidth = Math.Max(300, _layer.Bounds.Width - 40); }
        Size();
        _layer.PropertyChanged += OnLayerChanged;
        _layer.Children.Add(_root);
        Open.Add(this);
        _scrim.Opacity = LiquidTheme.ReduceMotion ? 1 : 0;
        Dispatcher.UIThread.Post(() => _scrim.Opacity = 1, DispatcherPriority.Render);
        _ = LiquidStage.PlayPop(_card);
        Dispatcher.UIThread.Post(FocusFirst, DispatcherPriority.Loaded);

        void OnLayerChanged(object? s, AvaloniaPropertyChangedEventArgs e) { if (e.Property == Visual.BoundsProperty) Size(); }
        Closed += () => _layer.PropertyChanged -= OnLayerChanged;
    }

    private void FocusFirst()
    {
        var target = _card.GetVisualDescendants().OfType<InputElement>()
            .FirstOrDefault(c => c.Focusable && c.IsEffectivelyEnabled && c.IsEffectivelyVisible && c is TextBox)
            ?? _card.GetVisualDescendants().OfType<InputElement>().FirstOrDefault(c => c.Focusable && c.IsEffectivelyEnabled && c.IsEffectivelyVisible);
        target?.Focus(NavigationMethod.Tab);
    }

    private void OnKeyDown(object? sender, KeyEventArgs e)
    {
        if (e.Key == Key.Escape && Open.LastOrDefault() == this)
        {
            // Let an open dropdown inside the dialog close first.
            if (_card.GetVisualDescendants().OfType<ComboBox>().Any(c => c.IsDropDownOpen)) return;
            RequestCancel();
            e.Handled = true;
        }
    }

    public void RequestCancel()
    {
        if (_closing) return;
        if (Cancel != null) Cancel();
        else Close();
    }

    /// <summary>Closes the dialog. The state change is immediate; the exit animation plays on a non interactive copy.</summary>
    public void Close()
    {
        if (_closing) return;
        _closing = true;
        Open.Remove(this);
        _root.IsHitTestVisible = false;
        _scrim.Opacity = 0;
        var exit = LiquidStage.PlayPopOut(_card);
        Closed?.Invoke();
        if (_returnFocus is InputElement focus && focus.IsAttachedToVisualTree()) focus.Focus();
        _ = Remove(exit);
    }

    private async Task Remove(Task exit)
    {
        try { await exit; } catch { }
        _layer.Children.Remove(_root);
    }

    /// <summary>Standard dialog layout: title, optional subtitle, close button, scrolling body and footer actions.</summary>
    public static Control Layout(string title, string? subtitle, Control body, Control? footer, Action onClose, bool scrollBody = true)
    {
        var grid = new Grid { RowDefinitions = new RowDefinitions("Auto,*,Auto"), RowSpacing = 14 };
        var head = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto") };
        var titles = new StackPanel { Spacing = 2 };
        titles.Children.Add(new TextBlock { Text = ExText.T(title), FontSize = 16, FontWeight = FontWeight.Bold });
        if (!string.IsNullOrEmpty(subtitle)) titles.Children.Add(new TextBlock { Text = ExText.T(subtitle), Classes = { "lg-sub" }, TextWrapping = TextWrapping.Wrap });
        head.Children.Add(titles);
        var close = new Button { Classes = { "lg-icon", "lg-sm" }, Content = new LiquidIcon { Kind = "x", Size = 13 }, VerticalAlignment = VerticalAlignment.Top };
        Avalonia.Automation.AutomationProperties.SetName(close, ExText.T("关闭"));
        ToolTip.SetTip(close, ExText.T("关闭"));
        close.Click += (_, _) => onClose();
        Grid.SetColumn(close, 1);
        head.Children.Add(close);
        grid.Children.Add(head);
        Control content = scrollBody ? new ScrollViewer { Content = body, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, Padding = new Thickness(0, 0, 4, 0) } : body;
        Grid.SetRow(content, 1);
        grid.Children.Add(content);
        if (footer != null) { Grid.SetRow(footer, 2); grid.Children.Add(footer); }
        return grid;
    }

    /// <summary>Confirmation dialog; <paramref name="danger"/> styles the confirm button red.</summary>
    public static void Confirm(Visual anchor, string title, string message, string confirm, Action onConfirm, bool danger = false)
    {
        LiquidModal? modal = null;
        var ok = new Button { Classes = { danger ? "lg-danger" : "lg-primary" }, Content = ExText.T(confirm), MinWidth = 88 };
        var cancel = new Button { Content = ExText.T("取消"), MinWidth = 88 };
        ok.Click += (_, _) => { modal?.Close(); onConfirm(); };
        cancel.Click += (_, _) => modal?.Close();
        var footer = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, HorizontalAlignment = HorizontalAlignment.Right, Children = { cancel, ok } };
        var body = new TextBlock { Text = ExText.T(message), TextWrapping = TextWrapping.Wrap, Classes = { "lg-body" } };
        modal = Show(anchor, Layout(title, null, body, footer, () => modal?.RequestCancel(), false), 420);
    }

    /// <summary>Single action notice.</summary>
    public static void Notice(Visual anchor, string title, string message)
    {
        LiquidModal? modal = null;
        var ok = new Button { Classes = { "lg-primary" }, Content = ExText.T("确定"), MinWidth = 88, HorizontalAlignment = HorizontalAlignment.Right };
        ok.Click += (_, _) => modal?.Close();
        var body = new TextBlock { Text = ExText.T(message), TextWrapping = TextWrapping.Wrap, Classes = { "lg-body" } };
        modal = Show(anchor, Layout(title, null, body, ok, () => modal?.RequestCancel(), false), 420);
    }
}
