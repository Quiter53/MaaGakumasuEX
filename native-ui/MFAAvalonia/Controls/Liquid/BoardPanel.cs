using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Metadata;
using Avalonia.Controls.Primitives;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Media;
using Avalonia.VisualTree;
using System;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// A workbench panel: glass surface, draggable header (handle + title), header actions and
/// a maximize toggle. The content stays mounted while the board rearranges panels.
/// </summary>
[TemplatePart("PART_Header", typeof(Control))]
[TemplatePart("PART_DragHandle", typeof(Button))]
[TemplatePart("PART_MaximizeButton", typeof(Button))]
[PseudoClasses(":placeholder", ":maximized")]
public class BoardPanel : ContentControl
{
    public static readonly StyledProperty<string> PanelIdProperty = AvaloniaProperty.Register<BoardPanel, string>(nameof(PanelId), "");
    public static readonly StyledProperty<string?> TitleProperty = AvaloniaProperty.Register<BoardPanel, string?>(nameof(Title));
    public static readonly StyledProperty<string?> SubtitleProperty = AvaloniaProperty.Register<BoardPanel, string?>(nameof(Subtitle));
    public static readonly StyledProperty<object?> HeaderActionsProperty = AvaloniaProperty.Register<BoardPanel, object?>(nameof(HeaderActions));
    public static readonly StyledProperty<bool> IsMaximizedProperty = AvaloniaProperty.Register<BoardPanel, bool>(nameof(IsMaximized));
    public static readonly StyledProperty<bool> CanMaximizeProperty = AvaloniaProperty.Register<BoardPanel, bool>(nameof(CanMaximize), true);
    public static readonly StyledProperty<BoxShadows> BoxShadowProperty = Border.BoxShadowProperty.AddOwner<BoardPanel>();

    public string PanelId { get => GetValue(PanelIdProperty); set => SetValue(PanelIdProperty, value); }
    public string? Title { get => GetValue(TitleProperty); set => SetValue(TitleProperty, value); }
    public string? Subtitle { get => GetValue(SubtitleProperty); set => SetValue(SubtitleProperty, value); }
    public object? HeaderActions { get => GetValue(HeaderActionsProperty); set => SetValue(HeaderActionsProperty, value); }
    public bool IsMaximized { get => GetValue(IsMaximizedProperty); set => SetValue(IsMaximizedProperty, value); }
    public bool CanMaximize { get => GetValue(CanMaximizeProperty); set => SetValue(CanMaximizeProperty, value); }
    public BoxShadows BoxShadow { get => GetValue(BoxShadowProperty); set => SetValue(BoxShadowProperty, value); }

    /// <summary>Raised when the header is pressed outside interactive controls.</summary>
    public event EventHandler<PointerPressedEventArgs>? MoveRequested;
    /// <summary>Raised by arrow keys on the drag handle (dx, dy).</summary>
    public event Action<BoardPanel, int, int>? KeyboardMoveRequested;
    public event Action<BoardPanel>? MaximizeToggleRequested;

    private Control? _header;
    private Button? _handle, _maximize;

    internal bool IsPlaceholder { set => PseudoClasses.Set(":placeholder", value); }

    protected override void OnApplyTemplate(TemplateAppliedEventArgs e)
    {
        if (_header != null) _header.PointerPressed -= OnHeaderPressed;
        if (_handle != null) { _handle.PointerPressed -= OnHeaderPressed; _handle.KeyDown -= OnHandleKeyDown; }
        if (_maximize != null) _maximize.Click -= OnMaximizeClick;
        base.OnApplyTemplate(e);
        _header = e.NameScope.Find<Control>("PART_Header");
        _handle = e.NameScope.Find<Button>("PART_DragHandle");
        _maximize = e.NameScope.Find<Button>("PART_MaximizeButton");
        if (_header != null) _header.AddHandler(PointerPressedEvent, OnHeaderPressed, RoutingStrategies.Bubble, handledEventsToo: false);
        if (_handle != null)
        {
            // Buttons swallow pointer presses, so the handle reports them during tunnelling.
            _handle.AddHandler(PointerPressedEvent, OnHandlePressed, RoutingStrategies.Tunnel);
            _handle.KeyDown += OnHandleKeyDown;
        }
        if (_maximize != null) _maximize.Click += OnMaximizeClick;
    }

    private void OnHandlePressed(object? sender, PointerPressedEventArgs e)
    {
        if (!e.GetCurrentPoint(this).Properties.IsLeftButtonPressed || IsMaximized) return;
        MoveRequested?.Invoke(this, e);
    }

    private void OnHeaderPressed(object? sender, PointerPressedEventArgs e)
    {
        if (!e.GetCurrentPoint(this).Properties.IsLeftButtonPressed || IsMaximized) return;
        // Interactive header controls keep their own behaviour.
        if (e.Source is Visual v && v.FindAncestorOfType<Button>(true) is { } b) return; // includes the handle, which reports via tunnelling
        if (e.Source is Visual v2 && (v2.FindAncestorOfType<TextBox>(true) != null || v2.FindAncestorOfType<ToggleButton>(true) != null || v2.FindAncestorOfType<ComboBox>(true) != null)) return;
        MoveRequested?.Invoke(this, e);
    }

    private void OnHandleKeyDown(object? sender, KeyEventArgs e)
    {
        var (dx, dy) = e.Key switch { Key.Left => (-1, 0), Key.Right => (1, 0), Key.Up => (0, -1), Key.Down => (0, 1), _ => (0, 0) };
        if (dx == 0 && dy == 0 || IsMaximized) return;
        KeyboardMoveRequested?.Invoke(this, dx, dy);
        e.Handled = true;
        _handle?.Focus(NavigationMethod.Directional);
    }

    private void OnMaximizeClick(object? sender, RoutedEventArgs e) => MaximizeToggleRequested?.Invoke(this);

    protected override void OnPropertyChanged(AvaloniaPropertyChangedEventArgs change)
    {
        base.OnPropertyChanged(change);
        if (change.Property == IsMaximizedProperty) PseudoClasses.Set(":maximized", IsMaximized);
    }
}
