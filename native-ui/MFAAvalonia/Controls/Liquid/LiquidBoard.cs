using Avalonia;
using Avalonia.Animation;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Media.Transformation;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Helper;
using System;
using System.Collections.Generic;
using System.Linq;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// Workbench surface. Panels are arranged by <see cref="BoardSolver"/>; dragging a header
/// previews the move live (neighbours glide aside) and commits on release. Panels are
/// never re-created, so inputs, focus, scroll positions and subscriptions survive.
/// </summary>
public class LiquidBoard : Panel
{
    public static readonly StyledProperty<BoardLayout> LayoutProperty =
        AvaloniaProperty.Register<LiquidBoard, BoardLayout>(nameof(Layout), BoardLayout.Default());
    public static readonly StyledProperty<bool> LiveViewOpenProperty =
        AvaloniaProperty.Register<LiquidBoard, bool>(nameof(LiveViewOpen));
    public static readonly StyledProperty<string?> MaximizedIdProperty =
        AvaloniaProperty.Register<LiquidBoard, string?>(nameof(MaximizedId));

    public BoardLayout Layout { get => GetValue(LayoutProperty); set => SetValue(LayoutProperty, value); }
    public bool LiveViewOpen { get => GetValue(LiveViewOpenProperty); set => SetValue(LiveViewOpenProperty, value); }
    public string? MaximizedId { get => GetValue(MaximizedIdProperty); set => SetValue(MaximizedIdProperty, value); }

    /// <summary>Raised after a move, resize or keyboard move has been committed.</summary>
    public event Action<BoardLayout>? LayoutCommitted;
    /// <summary>Status text for screen readers and the floating hint (empty when idle).</summary>
    public event Action<string>? Announce;

    private readonly Dictionary<string, Rect> _lastRects = new();
    private readonly List<Border> _splitters = new();
    private readonly Image _ghost = new() { IsHitTestVisible = false, IsVisible = false, Stretch = Stretch.Fill };
    private readonly Border _ghostFrame;
    private readonly Border _hint;
    private readonly TextBlock _hintText = new() { FontSize = 12 };
    private BoardGeometry? _geometry;
    private BoardLayout? _preview;
    private Gesture? _gesture;
    private bool _animateNext;
    private Point _ghostPos;

    private sealed class Gesture
    {
        public required string Kind; // move | column | row
        public required IPointer Pointer;
        public required Point Start;
        public required BoardLayout Base;
        public required BoardGeometry Geometry;
        public BoardPanel? Panel;
        public string A = "", B = "";
        public double AnchorX, OffsetY;
        public bool Started;
        public BoardDropTarget? Target;
        public string Zone = "";
    }

    public LiquidBoard()
    {
        ClipToBounds = false;
        _ghostFrame = new Border
        {
            Child = _ghost, IsHitTestVisible = false, IsVisible = false, CornerRadius = new CornerRadius(22), ClipToBounds = true,
            BorderThickness = new Thickness(1.5), Opacity = 0.9, ZIndex = 100,
        };
        _ghostFrame.Bind(Border.BorderBrushProperty, this.GetResourceObservable("LgAccentBrush"));
        _ghostFrame.Bind(Border.BoxShadowProperty, this.GetResourceObservable("LgGhostShadow"));
        _hint = new Border
        {
            Child = _hintText, IsHitTestVisible = false, IsVisible = false, Padding = new Thickness(18, 10), CornerRadius = new CornerRadius(18),
            BorderThickness = new Thickness(1), ZIndex = 101, HorizontalAlignment = HorizontalAlignment.Center,
        };
        _hint.Bind(Border.BackgroundProperty, this.GetResourceObservable("LgGlassDropdownBrush"));
        _hint.Bind(Border.BorderBrushProperty, this.GetResourceObservable("LgBorderOuterBrush"));
        _hint.Bind(Border.BoxShadowProperty, this.GetResourceObservable("LgDropdownShadow"));
        _hintText.Bind(TextBlock.ForegroundProperty, this.GetResourceObservable("LgTextMainBrush"));
        Children.Add(_ghostFrame);
        Children.Add(_hint);
        AddHandler(KeyDownEvent, OnKeyDown, RoutingStrategies.Tunnel);
    }

    private IEnumerable<BoardPanel> Panels => Children.OfType<BoardPanel>();

    private bool Visible(string id) => id != "live_view" || LiveViewOpen;

    protected override void OnPropertyChanged(AvaloniaPropertyChangedEventArgs change)
    {
        base.OnPropertyChanged(change);
        if (change.Property == LayoutProperty || change.Property == LiveViewOpenProperty || change.Property == MaximizedIdProperty)
        {
            if (change.Property != LayoutProperty) CancelGesture();
            _animateNext = true;
            foreach (var p in Panels) p.IsMaximized = p.PanelId == MaximizedId;
            InvalidateMeasure();
        }
    }

    protected override void ChildrenChanged(object? sender, System.Collections.Specialized.NotifyCollectionChangedEventArgs e)
    {
        base.ChildrenChanged(sender, e);
        foreach (var p in e.OldItems?.OfType<BoardPanel>() ?? [])
        {
            p.MoveRequested -= OnMoveRequested;
            p.KeyboardMoveRequested -= OnKeyboardMove;
            p.MaximizeToggleRequested -= OnMaximizeToggle;
        }
        foreach (var p in e.NewItems?.OfType<BoardPanel>() ?? [])
        {
            p.MoveRequested -= OnMoveRequested;
            p.MoveRequested += OnMoveRequested;
            p.KeyboardMoveRequested += OnKeyboardMove;
            p.MaximizeToggleRequested += OnMaximizeToggle;
        }
    }

    private BoardLayout Effective => _preview ?? Layout;

    private BoardGeometry ComputeGeometry(Size size, BoardLayout? layout = null)
    {
        if (MaximizedId is { } max && Panels.Any(p => p.PanelId == max))
        {
            var rects = new Dictionary<string, BoardRect> { [max] = new(0, 0, size.Width, size.Height, "max") };
            return new BoardGeometry(size.Width, size.Height, rects, []);
        }
        return BoardSolver.Solve(layout ?? Effective, size.Width, size.Height, Visible);
    }

    private Size _viewport;

    protected override Size MeasureOverride(Size availableSize)
    {
        // Inside a ScrollViewer the available size is infinite; fall back to the viewport.
        var w = double.IsInfinity(availableSize.Width) ? (_viewport.Width > 0 ? _viewport.Width : 1200) : availableSize.Width;
        var h = double.IsInfinity(availableSize.Height) ? (_viewport.Height > 0 ? _viewport.Height : 800) : availableSize.Height;
        var g = ComputeGeometry(new Size(w, h));
        foreach (var p in Panels)
        {
            if (g.Rects.TryGetValue(p.PanelId, out var r)) p.Measure(new Size(r.Width, r.Height));
            else p.Measure(default);
        }
        _hint.Measure(Size.Infinity);
        _ghostFrame.Measure(Size.Infinity);
        return new Size(g.Width, g.Height);
    }

    /// <summary>Viewport of the hosting scroll viewer; lets the board fill it and grow beyond when too small.</summary>
    public void SetViewport(Size viewport)
    {
        if (_viewport == viewport) return;
        _viewport = viewport;
        CancelGesture();
        InvalidateMeasure();
    }

    protected override Size ArrangeOverride(Size finalSize)
    {
        var g = ComputeGeometry(finalSize);
        if (_gesture == null) _geometry = g;
        var animate = _animateNext && !LiquidTheme.ReduceMotion && _lastRects.Count > 0;
        _animateNext = false;
        foreach (var p in Panels)
        {
            if (!g.Rects.TryGetValue(p.PanelId, out var r))
            {
                p.IsVisible = false;
                _lastRects.Remove(p.PanelId);
                continue;
            }
            var rect = new Rect(r.X, r.Y, r.Width, r.Height);
            var wasVisible = p.IsVisible && _lastRects.ContainsKey(p.PanelId);
            p.IsVisible = true;
            p.Arrange(rect);
            if (animate && wasVisible && _lastRects.TryGetValue(p.PanelId, out var old) && old.Position != rect.Position)
                Flip(p, old.Position - rect.Position);
            _lastRects[p.PanelId] = rect;
        }
        ArrangeSplitters(g);
        if (_ghostFrame.IsVisible) _ghostFrame.Arrange(new Rect(_ghostPos, new Size(_ghostFrame.Width, _ghostFrame.Height)));
        if (_hint.IsVisible)
        {
            var hs = _hint.DesiredSize;
            var y = Math.Max(0, Math.Min(finalSize.Height, _viewport.Height > 0 ? _viewport.Height : finalSize.Height) - hs.Height - 18);
            _hint.Arrange(new Rect((Math.Min(finalSize.Width, _viewport.Width > 0 ? _viewport.Width : finalSize.Width) - hs.Width) / 2, y, hs.Width, hs.Height));
        }
        return finalSize;
    }

    /// <summary>FLIP: start from the old offset and glide to the new position. Size is applied at once.</summary>
    private static void Flip(Control panel, Vector offset)
    {
        var transitions = panel.Transitions;
        panel.Transitions = null;
        panel.RenderTransform = TransformOperations.Parse($"translate({offset.X.ToString(System.Globalization.CultureInfo.InvariantCulture)}px, {offset.Y.ToString(System.Globalization.CultureInfo.InvariantCulture)}px)");
        panel.Transitions = transitions ?? new Transitions
        {
            new TransformOperationsTransition { Property = Visual.RenderTransformProperty, Duration = TimeSpan.FromMilliseconds(240), Easing = LiquidStage.Spring }
        };
        Dispatcher.UIThread.Post(() => panel.RenderTransform = TransformOperations.Identity, DispatcherPriority.Render);
    }

    #region Splitters

    private void ArrangeSplitters(BoardGeometry g)
    {
        var specs = new List<(string Kind, string A, string B, Rect Rect)>();
        if (MaximizedId == null && _gesture?.Kind != "move")
        {
            for (int i = 0; i < g.Columns.Count; i++)
            {
                var col = g.Columns[i];
                if (i < g.Columns.Count - 1)
                    specs.Add(("column", col.Id, g.Columns[i + 1].Id, new Rect(col.X + col.Width, 0, BoardSolver.Gap, g.Height)));
                for (int j = 0; j < col.Items.Count - 1; j++)
                {
                    var r = g.Rects[col.Items[j]];
                    specs.Add(("row", col.Items[j], col.Items[j + 1], new Rect(r.X, r.Bottom, r.Width, BoardSolver.Gap)));
                }
            }
        }
        while (_splitters.Count < specs.Count)
        {
            var grip = CreateSplitter();
            _splitters.Add(grip);
            Children.Add(grip);
        }
        for (int i = 0; i < _splitters.Count; i++)
        {
            var s = _splitters[i];
            if (i >= specs.Count) { s.IsVisible = false; continue; }
            var (kind, a, b, rect) = specs[i];
            s.IsVisible = true;
            s.Tag = (kind, a, b);
            s.Classes.Set("column", kind == "column");
            s.Classes.Set("row", kind == "row");
            s.Cursor = new Cursor(kind == "column" ? StandardCursorType.SizeWestEast : StandardCursorType.SizeNorthSouth);
            ToolTip.SetTip(s, ExText.T(kind == "column" ? "拖动调整相邻列宽度" : "拖动调整同列面板高度"));
            s.Measure(rect.Size);
            s.Arrange(rect);
        }
    }

    private Border CreateSplitter()
    {
        var pill = new Border { CornerRadius = new CornerRadius(99), HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, Classes = { "lg-splitter-pill" } };
        var grip = new Border { Background = Brushes.Transparent, Child = pill, ZIndex = 10, Classes = { "lg-splitter" } };
        grip.PointerPressed += OnSplitterPressed;
        return grip;
    }

    private void OnSplitterPressed(object? sender, PointerPressedEventArgs e)
    {
        if (sender is not Border grip || grip.Tag is not ValueTuple<string, string, string> tag || _geometry == null) return;
        if (!e.GetCurrentPoint(this).Properties.IsLeftButtonPressed) return;
        BeginGesture(new Gesture { Kind = tag.Item1, A = tag.Item2, B = tag.Item3, Pointer = e.Pointer, Start = e.GetPosition(this), Base = Layout.Clone(), Geometry = _geometry }, e);
        grip.Classes.Set("dragging", true);
        e.Handled = true;
    }

    #endregion

    #region Move gesture

    private void OnMoveRequested(object? sender, PointerPressedEventArgs e)
    {
        if (sender is not BoardPanel panel || MaximizedId != null || _geometry == null) return;
        var bounds = panel.Bounds;
        var p = e.GetPosition(this);
        BeginGesture(new Gesture
        {
            Kind = "move", Panel = panel, Pointer = e.Pointer, Start = p, Base = Layout.Clone(), Geometry = _geometry,
            AnchorX = bounds.Width > 0 ? (p.X - bounds.X) / bounds.Width : 0.5, OffsetY = p.Y - bounds.Y,
        }, e);
    }

    private void BeginGesture(Gesture gesture, PointerEventArgs e)
    {
        CancelGesture();
        _gesture = gesture;
        e.Pointer.Capture(this);
        if (TopLevel.GetTopLevel(this) is Window w) w.Deactivated += OnWindowDeactivated;
    }

    protected override void OnPointerMoved(PointerEventArgs e)
    {
        base.OnPointerMoved(e);
        var g = _gesture;
        if (g == null || e.Pointer != g.Pointer) return;
        var p = e.GetPosition(this);
        if (!g.Started)
        {
            if (Math.Sqrt(Math.Pow(p.X - g.Start.X, 2) + Math.Pow(p.Y - g.Start.Y, 2)) < 6) return;
            g.Started = true;
            if (g.Kind == "move") StartGhost(g);
        }
        e.Handled = true;
        if (g.Kind == "column")
        {
            _preview = BoardSolver.ResizeColumns(g.Base, g.Geometry, g.A, g.B, p.X - g.Start.X);
            InvalidateMeasure();
            return;
        }
        if (g.Kind == "row")
        {
            _preview = BoardSolver.ResizeRows(g.Base, g.Geometry, g.A, g.B, p.Y - g.Start.Y);
            InvalidateMeasure();
            return;
        }
        AutoScroll(e);
        var inside = new Rect(Bounds.Size).Contains(p);
        var target = inside ? BoardSolver.DropTarget(g.Geometry, g.Panel!.PanelId, p.X, p.Y) : null;
        var zone = target is { } t ? t.Id + ":" + t.Edge : "";
        if (zone != g.Zone || _preview == null)
        {
            g.Zone = zone;
            g.Target = target;
            _preview = target is { } tt ? BoardSolver.Move(g.Base, g.Panel!.PanelId, tt.Id, tt.Edge) : g.Base.Clone();
            _animateNext = true;
            ShowHint(target);
            InvalidateMeasure();
        }
        var size = ComputeGeometry(Bounds.Size).Rects.TryGetValue(g.Panel!.PanelId, out var r) ? new Size(r.Width, r.Height) : g.Panel.Bounds.Size;
        _ghostFrame.Width = size.Width;
        _ghostFrame.Height = size.Height;
        _ghostPos = new Point(p.X - size.Width * g.AnchorX, p.Y - Math.Min(g.OffsetY, 40));
        InvalidateArrange();
    }

    protected override void OnPointerReleased(PointerReleasedEventArgs e)
    {
        base.OnPointerReleased(e);
        var g = _gesture;
        if (g == null || e.Pointer != g.Pointer) return;
        var p = e.GetPosition(this);
        var commit = g.Started && _preview != null &&
                     (g.Kind != "move" || new Rect(Bounds.Size).Contains(p) && g.Target != null);
        var result = commit ? _preview : null;
        // End the transaction before releasing capture so the capture-lost handler sees no gesture.
        EndGesture();
        e.Pointer.Capture(null);
        if (result != null)
        {
            Layout = result;
            LayoutCommitted?.Invoke(result);
            if (g.Kind == "move") Announce?.Invoke(ExText.T("面板位置已更新"));
        }
        e.Handled = g.Started;
    }

    protected override void OnPointerCaptureLost(PointerCaptureLostEventArgs e)
    {
        base.OnPointerCaptureLost(e);
        if (_gesture != null && e.Pointer == _gesture.Pointer) CancelGesture();
    }

    private void OnWindowDeactivated(object? sender, EventArgs e) => CancelGesture();

    private void OnKeyDown(object? sender, KeyEventArgs e)
    {
        if (e.Key == Key.Escape && _gesture != null)
        {
            CancelGesture();
            e.Handled = true;
        }
    }

    /// <summary>Restores the committed layout and clears ghost, hint and pointer capture.</summary>
    public void CancelGesture()
    {
        if (_gesture == null) return;
        var pointer = _gesture.Pointer;
        EndGesture();
        _animateNext = true;
        if (pointer.Captured == this) pointer.Capture(null);
        InvalidateMeasure();
    }

    private void EndGesture()
    {
        var g = _gesture;
        if (g == null) return;
        _gesture = null;
        _preview = null;
        if (g.Panel != null) g.Panel.IsPlaceholder = false;
        foreach (var s in _splitters) s.Classes.Set("dragging", false);
        _ghostFrame.IsVisible = false;
        _ghost.Source = null;
        _hint.IsVisible = false;
        Announce?.Invoke("");
        if (TopLevel.GetTopLevel(this) is Window w) w.Deactivated -= OnWindowDeactivated;
        InvalidateArrange();
    }

    private void StartGhost(Gesture g)
    {
        var panel = g.Panel!;
        try
        {
            // A static snapshot: the ghost never hosts live business controls or bindings.
            var scale = TopLevel.GetTopLevel(this)?.RenderScaling ?? 1;
            var size = new PixelSize(Math.Max(1, (int)(panel.Bounds.Width * scale)), Math.Max(1, (int)(panel.Bounds.Height * scale)));
            var bitmap = new RenderTargetBitmap(size, new Vector(96 * scale, 96 * scale));
            bitmap.Render(panel);
            _ghost.Source = bitmap;
        }
        catch (Exception ex)
        {
            LoggerHelper.Warning($"面板拖动预览截图失败，使用占位框：{ex.Message}");
        }
        _ghostFrame.Width = panel.Bounds.Width;
        _ghostFrame.Height = panel.Bounds.Height;
        _ghostPos = panel.Bounds.Position;
        _ghostFrame.IsVisible = true;
        panel.IsPlaceholder = true;
        ShowHint(null);
    }

    private void ShowHint(BoardDropTarget? target)
    {
        string Name(string id) => ExText.T(id switch
        {
            "task_list" => "任务列表", "task_desc" => "任务说明", "task_settings" => "任务设置", "log" => "运行日志", _ => "实时画面"
        });
        _hintText.Text = target is { } t
            ? ExText.T(Name(t.Id) + (t.Edge switch { BoardEdge.Left => "左侧新列", BoardEdge.Right => "右侧新列", BoardEdge.Top => "上方", _ => "下方" })) + " · " + ExText.T("松开放置")
            : ExText.T("拖向面板上／下方叠放，左／右侧分列 · Esc 取消");
        _hint.IsVisible = true;
        _hint.InvalidateMeasure();
        Announce?.Invoke(_hintText.Text);
    }

    private void AutoScroll(PointerEventArgs e)
    {
        if (this.FindAncestorOfType<ScrollViewer>() is not { } sv) return;
        var p = e.GetPosition(sv);
        var dx = p.X < 28 ? -16 : p.X > sv.Bounds.Width - 28 ? 16 : 0;
        var dy = p.Y < 28 ? -16 : p.Y > sv.Bounds.Height - 28 ? 16 : 0;
        if (dx != 0 || dy != 0) sv.Offset = new Vector(Math.Max(0, sv.Offset.X + dx), Math.Max(0, sv.Offset.Y + dy));
    }

    #endregion

    private void OnKeyboardMove(BoardPanel panel, int dx, int dy)
    {
        if (MaximizedId != null || _gesture != null) return;
        var next = BoardSolver.KeyboardMove(Layout, panel.PanelId, dx, dy, Visible);
        if (next == null) return;
        Layout = next;
        LayoutCommitted?.Invoke(next);
        Announce?.Invoke(ExText.T("面板位置已更新"));
    }

    private void OnMaximizeToggle(BoardPanel panel)
    {
        CancelGesture();
        MaximizedId = MaximizedId == panel.PanelId ? null : panel.PanelId;
    }
}
