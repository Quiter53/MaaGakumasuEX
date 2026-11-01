using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Media;
using Avalonia.VisualTree;
using Avalonia.Threading;
using MFAAvalonia.Helper.ValueType;
using MFAAvalonia.Extensions;
using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>Preview the gap with transforms, and change the task collection once, on drop.</summary>
public sealed class TaskReorderBehavior : AvaloniaObject
{
    public static readonly AttachedProperty<bool> EnabledProperty = AvaloniaProperty.RegisterAttached<TaskReorderBehavior, ListBox, bool>("Enabled");
    private static readonly ConditionalWeakTable<ListBox, Session> Sessions = new();
    public static void SetEnabled(ListBox list, bool value) => list.SetValue(EnabledProperty, value);
    public static bool GetEnabled(ListBox list) => list.GetValue(EnabledProperty);
    static TaskReorderBehavior() => EnabledProperty.Changed.AddClassHandler<ListBox>((list, e) =>
    {
        if (e.NewValue is true) Sessions.GetValue(list, l => new Session(l));
        else if (Sessions.TryGetValue(list, out var session)) { session.Dispose(); Sessions.Remove(list); }
    });

    private sealed class Session : IDisposable
    {
        private readonly ListBox _list;
        private readonly Dictionary<Control, double> _offsets = new();
        private object? _item;
        private Point _press;
        private ListBoxItem? _row;
        private PointerGhost? _ghost;
        private IPointer? _pointer;
        private int _target;
        private readonly DispatcherTimer _scrollTimer = new() { Interval = TimeSpan.FromMilliseconds(16) };
        private Point _lastPoint;
        public Session(ListBox list)
        {
            _list = list;
            list.AddHandler(InputElement.PointerPressedEvent, Press, RoutingStrategies.Tunnel, true);
            list.AddHandler(InputElement.PointerMovedEvent, Move, RoutingStrategies.Tunnel, true);
            list.AddHandler(InputElement.PointerReleasedEvent, Release, RoutingStrategies.Tunnel, true);
            list.PointerCaptureLost += CaptureLost;
            list.KeyDown += Key;
            list.DetachedFromVisualTree += Detach;
            list.SizeChanged += Resize;
            list.PropertyChanged += Changed;
            _scrollTimer.Tick += Scroll;
        }
        private void Press(object? sender, PointerPressedEventArgs e)
        {
            if (!DragDrop.GetAllowDrop(_list) || !e.GetCurrentPoint(_list).Properties.IsLeftButtonPressed ||
                (e.Source as Visual)?.FindAncestorOfType<Button>(true) != null) return;
            var row = (e.Source as Visual)?.FindAncestorOfType<ListBoxItem>(true);
            if (row == null || _list.IndexFromContainer(row) < 0 || row.DataContext is DragItemViewModel { IsResourceOptionItem: true }) return;
            Reset(); _row = row; _item = _list.Items[_list.IndexFromContainer(row)]; _press = e.GetPosition(_list);
        }
        private void Move(object? sender, PointerEventArgs e)
        {
            if (_row == null || _item == null || _list.ItemsSource is not IList items) return;
            if (!e.GetCurrentPoint(_list).Properties.IsLeftButtonPressed) { Reset(); return; }
            var point = e.GetPosition(_list);
            if (_ghost == null)
            {
                var delta = point - _press;
                if (delta.X * delta.X + delta.Y * delta.Y < 25) return;
                var layer = OverlayLayer.GetOverlayLayer(_list);
                if (layer == null || _row.TransformToVisual(layer) is not { } transform) return;
                var rect = new Rect(_row.Bounds.Size).TransformToAABB(transform);
                var preview = new GlassPanel { Width = rect.Width, Height = rect.Height, Padding = new Thickness(14, 8),
                    CornerRadius = new CornerRadius(16), Content = new TextBlock { Text = (_item as DragItemViewModel)?.Name ?? _item.ToString(),
                        FontSize = 13, TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = Avalonia.Layout.VerticalAlignment.Center } };
                var cursor = e.GetPosition(layer);
                _ghost = new PointerGhost(_list, preview, new Point(cursor.X - rect.X, cursor.Y - rect.Y));
                _pointer = e.Pointer; e.Pointer.Capture(_list); _row.Opacity = 0.2;
                _scrollTimer.Start();
            }
            _ghost.Move(e.GetPosition(_ghost.Layer));
            _lastPoint = point;
            Preview(point, items);
            e.Handled = true;
        }
        private void Scroll(object? sender, EventArgs e)
        {
            if (_ghost == null || _list.ItemsSource is not IList items) return;
            var scroll = _list.GetVisualDescendants().OfType<ScrollViewer>().FirstOrDefault();
            if (scroll == null) return;
            const double edge = 32;
            var speed = _lastPoint.Y < edge ? -8 : _lastPoint.Y > _list.Bounds.Height - edge ? 8 : 0;
            var y = Math.Clamp(scroll.Offset.Y + speed, 0, Math.Max(0, scroll.Extent.Height - scroll.Viewport.Height));
            if (Math.Abs(y - scroll.Offset.Y) < .01) return;
            scroll.Offset = new Vector(scroll.Offset.X, y);
            _list.UpdateLayout();
            Preview(_lastPoint, items);
        }
        private void Preview(Point point, IList items)
        {
            if (_row == null || _item == null) return;
            var from = items.IndexOf(_item);
            if (from < 0) { Reset(); return; }
            _target = items.Count;
            for (var i = 0; i < items.Count; i++)
            {
                if (_list.ContainerFromIndex(i) is not Control row || row.TranslatePoint(default, _list) is not { } origin) continue;
                var offset = row.RenderTransform?.Value.M32 ?? 0;
                if (point.Y < origin.Y - offset + row.Bounds.Height / 2) { _target = i; break; }
            }
            if (_target == 0 && items[0] is DragItemViewModel { IsResourceOptionItem: true }) _target = 1;
            var destination = _target > from ? _target - 1 : _target;
            var step = _row.Bounds.Height + 8;
            for (var i = 0; i < items.Count; i++)
            {
                if (_list.ContainerFromIndex(i) is not Control row) continue;
                var offset = i > from && i <= destination ? -step : i >= destination && i < from ? step : 0;
                if (_offsets.GetValueOrDefault(row) == offset) continue;
                _offsets[row] = offset;
                row.RenderTransform = LiquidTransforms.Translate(0, offset);
            }
        }
        private void Release(object? sender, PointerReleasedEventArgs e)
        {
            var item = _item; var target = _target; var moved = _ghost != null;
            Reset();
            if (moved && item != null && _list.ItemsSource is IList items && items.IndexOf(item) is var from && from >= 0)
            {
                items.MoveTo(from, Math.Clamp(target, 0, items.Count));
                _list.SelectedItem = item;
                e.Handled = true;
            }
        }
        private void Key(object? sender, KeyEventArgs e) { if (e.Key == Avalonia.Input.Key.Escape && _ghost != null) { Reset(); e.Handled = true; } }
        private void CaptureLost(object? sender, PointerCaptureLostEventArgs e) => Reset();
        private void Detach(object? sender, VisualTreeAttachmentEventArgs e) => Reset();
        private void Resize(object? sender, SizeChangedEventArgs e) { if (_ghost != null) Reset(); }
        private void Changed(object? sender, AvaloniaPropertyChangedEventArgs e)
        {
            if (e.Property == ItemsControl.ItemsSourceProperty ||
                e.Property == DragDrop.AllowDropProperty && e.NewValue is false) Reset();
        }
        private void Reset()
        {
            _scrollTimer.Stop();
            var pointer = _pointer; _pointer = null;
            _ghost?.Dispose(); _ghost = null;
            if (_row != null) _row.Opacity = 1;
            foreach (var row in _offsets.Keys) row.RenderTransform = LiquidTransforms.Translate(0, 0);
            _offsets.Clear(); _row = null; _item = null;
            if (pointer?.Captured == _list) pointer.Capture(null);
        }
        public void Dispose()
        {
            Reset();
            _list.RemoveHandler(InputElement.PointerPressedEvent, Press);
            _list.RemoveHandler(InputElement.PointerMovedEvent, Move);
            _list.RemoveHandler(InputElement.PointerReleasedEvent, Release);
            _list.PointerCaptureLost -= CaptureLost; _list.KeyDown -= Key; _list.DetachedFromVisualTree -= Detach;
            _list.SizeChanged -= Resize; _list.PropertyChanged -= Changed;
            _scrollTimer.Tick -= Scroll;
        }
    }
}
