using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Controls.Presenters;
using Avalonia.Headless;
using Avalonia.Input;
using Avalonia.Media;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.Helper;
using MFAAvalonia.Views.Pages;
using MFAAvalonia.Views.UserControls;
using System.Collections;
using System.Reflection;

internal static class Round4Checks
{
    public static bool Run(Window w, string output)
    {
        var failures = new List<string>();
        void Check(bool value, string name) { Console.WriteLine((value ? "PASS " : "FAIL ") + name); if (!value) failures.Add(name); }
        LiquidNavigation.Navigate(LiquidPages.Tasks); Program.Pump(300);
        Shots.SelectTask(w, "开始培育"); Program.Pump(400);
        var scaleHost = w.GetVisualDescendants().OfType<UniformScaleHost>().First();
        foreach (var size in new[] { new Size(1600, 1000), new Size(1280, 800), new Size(1600, 1000) })
        {
            w.Width = size.Width; w.Height = size.Height;
            foreach (var scale in new[] { 1.0, 0.8, 1.5, 0.9, 1.2, 1.0 })
            {
                Instances.GuiSettingsUserControlModel.FontScale = scale; Program.Pump(60);
                var child = scaleHost.Child!;
                var rect = new Rect(child.Bounds.Size).TransformToAABB(child.TransformToVisual(scaleHost)!.Value);
                Check(Math.Abs(rect.Width + child.Margin.Left * scale + child.Margin.Right * scale - scaleHost.Bounds.Width) < 2 &&
                      Math.Abs(rect.Height + child.Margin.Top * scale + child.Margin.Bottom * scale - scaleHost.Bounds.Height) < 2 &&
                      Math.Abs(rect.X - child.Margin.Left * scale) < 2,
                    $"scale {scale} fills {size} without stale side gutters: {rect.Size}");
            }
        }
        Program.Save(w, Path.Combine(output, "scale-restored.png"));
        var segments = w.GetVisualDescendants().OfType<SegmentedControl>().Where(s => s.IsEffectivelyVisible && s.SelectedIndex >= 0).ToArray();
        foreach (var segment in segments)
        {
            segment.InvalidateArrange(); w.UpdateLayout();
            var thumb = segment.GetVisualDescendants().OfType<Border>().First(b => b.Name == "PART_Thumb");
            Check(thumb.Opacity == 1 && thumb.Width > 0, "selected segment has a thumb in the current layout pass");
        }
        var list = w.GetVisualDescendants().OfType<ListBox>().First(l => l.Name == "TaskListBox" && l.IsEffectivelyVisible);
        list.GetVisualDescendants().OfType<ScrollViewer>().First().Offset = default;
        Program.Pump(150);
        var items = (IList)list.ItemsSource!;
        if (items.Count >= 4)
        {
            var row = (ListBoxItem)list.ContainerFromIndex(1)!;
            var target = (ListBoxItem)list.ContainerFromIndex(3)!;
            var item = items[1];
            var origin = row.TranslatePoint(new Point(24, row.Bounds.Height / 2), w)!.Value;
            var end = target.TranslatePoint(new Point(24, target.Bounds.Height - 3), w)!.Value;
            w.MouseDown(origin, MouseButton.Left); w.MouseMove(new Point(origin.X, origin.Y + 8), RawInputModifiers.LeftMouseButton); Program.Pump(30);
            w.MouseMove(end, RawInputModifiers.LeftMouseButton); Program.Pump(200);
            Check(list.GetVisualDescendants().OfType<ListBoxItem>().Any(r => Math.Abs(r.RenderTransform?.Value.M32 ?? 0) > 1), "task rows make room during the drag");
            Program.Save(w, Path.Combine(output, "task-reorder.png"));
            w.MouseUp(end, MouseButton.Left); Program.Pump(200);
            Check(ReferenceEquals(items[3], item), "drop commits the intended task order exactly once");
            var before = items.Cast<object>().ToArray();
            row = (ListBoxItem)list.ContainerFromIndex(1)!;
            origin = row.TranslatePoint(new Point(24, row.Bounds.Height / 2), w)!.Value;
            w.MouseDown(origin, MouseButton.Left); w.MouseMove(new Point(origin.X, origin.Y + 70), RawInputModifiers.LeftMouseButton); Program.Pump(40);
            w.KeyPress(Key.Escape, RawInputModifiers.None, PhysicalKey.Escape, null);
            w.MouseUp(new Point(origin.X, origin.Y + 70), MouseButton.Left); Program.Pump(200);
            Check(items.Cast<object>().SequenceEqual(before), "Escape cancels the reorder without changing task order");
            Check(list.GetVisualDescendants().OfType<ListBoxItem>().All(r => Math.Abs(r.RenderTransform?.Value.M32 ?? 0) < .01), "cancel clears every row displacement");
            var scroll = list.GetVisualDescendants().OfType<ScrollViewer>().First();
            if (scroll.Extent.Height > scroll.Viewport.Height)
            {
                row = (ListBoxItem)list.ContainerFromIndex(1)!;
                origin = row.TranslatePoint(new Point(24, row.Bounds.Height / 2), w)!.Value;
                var edge = list.TranslatePoint(new Point(30, list.Bounds.Height - 5), w)!.Value;
                w.MouseDown(origin, MouseButton.Left);
                w.MouseMove(edge, RawInputModifiers.LeftMouseButton);
                var timerTicked = false;
                using var timerProbe = DispatcherTimer.RunOnce(() => timerTicked = true, TimeSpan.FromMilliseconds(16));
                Program.Pump(220);
                Check(timerTicked, "headless pump executes dispatcher timers");
                Check(scroll.Offset.Y > 0, "task drag scrolls the long list at its bottom edge");
                w.KeyPress(Key.Escape, RawInputModifiers.None, PhysicalKey.Escape, null);
                w.MouseUp(edge, MouseButton.Left);
                scroll.Offset = default;
            }
        }
        Shots.SelectTask(w, "开始培育"); Program.Pump(300);
        var help = w.GetVisualDescendants().OfType<TooltipBlock>().FirstOrDefault(t => t.IsEffectivelyVisible && t.TooltipText.Contains("完成指定次数"));
        Check(help != null, "produce count help contains actual instructions rather than a translation key");
        if (help != null)
        {
            var popup = (Popup?)typeof(TooltipBlock).GetField("_templatePopup", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(help);
            popup!.IsOpen = true; Program.Pump(400);
            Check(popup.IsUsingOverlayLayer && BackdropBlur.LastFrameSupported == true, "help uses an in-window blurred glass surface");
            Program.Save(w, Path.Combine(output, "help.png")); popup.IsOpen = false;
        }
        LiquidNavigation.Navigate(LiquidPages.CardRules); Program.Pump(400);
        var expander = w.GetVisualDescendants().OfType<Expander>().First(e => e.IsEffectivelyVisible);
        expander.IsExpanded = true; Program.Pump(150);
        Check(expander.GetVisualDescendants().Any(v => v is ContentPresenter { Name: "ExpandedContent", IsVisible: true }), "precise editor expands inside the new single rounded shell");
        Program.Save(w, Path.Combine(output, "rules.png"));
        LiquidNavigation.Navigate(LiquidPages.Settings); Program.Pump(400);
        var settings = w.GetVisualDescendants().OfType<SettingsView>().First(v => v.IsEffectivelyVisible);
        settings.SelectSection("gui"); Program.Pump(300);
        var slider = settings.GetVisualDescendants().OfType<Slider>().First(s => s.IsEffectivelyVisible);
        var handle = slider.GetVisualDescendants().OfType<Thumb>().First();
        var start = handle.TranslatePoint(new Point(handle.Bounds.Width / 2, handle.Bounds.Height / 2), w)!.Value;
        w.MouseDown(start, MouseButton.Left);
        var changedScale = false;
        foreach (var dx in new[] { 40, 100, 180, 60, -40 })
        {
            w.MouseMove(new Point(start.X + dx, start.Y), RawInputModifiers.LeftMouseButton); Program.Pump(60);
            changedScale |= Math.Abs(Instances.GuiSettingsUserControlModel.FontScale - 1) > .001;
            var child = scaleHost.Child!;
            var rect = new Rect(child.Bounds.Size).TransformToAABB(child.TransformToVisual(scaleHost)!.Value);
            Check(Math.Abs(rect.Width + (child.Margin.Left + child.Margin.Right) * scaleHost.Scale - scaleHost.Bounds.Width) < 2,
                $"slider pointer drag at {scaleHost.Scale:F2} continues filling the current viewport");
        }
        w.MouseUp(new Point(start.X - 40, start.Y), MouseButton.Left);
        Check(changedScale, "pointer input really changes the font scale");
        Instances.GuiSettingsUserControlModel.FontScale = 1; Program.Pump(200);
        foreach (var section in new[] { "gui", "update", "start" })
        {
            settings.SelectSection(section); Program.Pump(500);
            Program.Save(w, Path.Combine(output, "settings-" + section + ".png"));
        }
        Application.Current!.RequestedThemeVariant = Avalonia.Styling.ThemeVariant.Dark;
        settings.SelectSection("gui"); Program.Pump(400);
        Program.Save(w, Path.Combine(output, "settings-dark.png"));
        LiquidNavigation.Navigate(LiquidPages.CardRules); Program.Pump(400);
        Program.Save(w, Path.Combine(output, "rules-dark.png"));
        Console.WriteLine(failures.Count == 0 ? "ROUND4_CHECKS_PASSED" : $"ROUND4_CHECKS_FAILED {failures.Count}");
        return failures.Count == 0;
    }
}
