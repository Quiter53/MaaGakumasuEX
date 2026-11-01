using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Headless;
using Avalonia.Media;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Controls.Liquid;

internal static class MotionChecks
{
    private static void Pump(int milliseconds)
    {
        var end = DateTime.UtcNow.AddMilliseconds(milliseconds);
        do { Dispatcher.UIThread.RunJobs(); AvaloniaHeadlessPlatform.ForceRenderTimerTick(); Thread.Sleep(8); }
        while (DateTime.UtcNow < end);
    }

    public static void Run(Action<bool, string> check)
    {
        LiquidTheme.ReduceMotion = false;
        var host = new Panel();
        var window = new Window { Width = 800, Height = 600, Content = host };
        window.Show(); Pump(40);
        var editor = new TextBox { Width = 230, Text = "preserved text" };
        var label = new TextBlock { Text = "任务参数及中文标签", MinWidth = 120 };
        var row = new ResponsiveOptionGrid { ColumnSpacing = 12 };
        row.Children.Add(label); row.Children.Add(editor); row.Configure(label, editor);
        host.Children.Add(row);
        foreach (var width in new[] { 650, 270, 650, 270 })
        {
            window.Width = width; Pump(30);
            check(Grid.GetRow(editor) == (width < 400 ? 1 : 0), $"responsive option uses normal measure at width {width}");
            host.Children.Remove(row);
            Pump(30); // No queued NaturalWidth callback can remeasure detached templates.
            host.Children.Add(row); Pump(30);
            check(editor.Text == "preserved text", "detach and reattach preserve the original editor and text");
        }
        host.Children.Clear(); window.Width = 800;

        foreach (var scale in new[] { 1.0, 1.25, 1.5 })
        {
            var chip = new Border { Width = 180, Height = 36, CornerRadius = new CornerRadius(18),
                Background = Brushes.DodgerBlue, Child = new TextBlock { Text = "卡牌 · 缩放验证", Foreground = Brushes.White } };
            var holder = new StackPanel { HorizontalAlignment = Avalonia.Layout.HorizontalAlignment.Left,
                VerticalAlignment = Avalonia.Layout.VerticalAlignment.Top, Margin = new Thickness(25, 70, 0, 0), Children = { chip } };
            var scaled = new LayoutTransformControl { LayoutTransform = new ScaleTransform(scale, scale), Child = holder };
            host.Children.Add(scaled); Pump(40);
            var overlay = OverlayLayer.GetOverlayLayer(chip)!;
            var expected = new Rect(chip.Bounds.Size).TransformToAABB(chip.TransformToVisual(overlay)!.Value);
            LiquidStage.PlayDeparture(chip);
            holder.Children.Remove(chip); Pump(16);
            var placement = overlay.Children.OfType<Canvas>().Single(c => c.Name == "LiquidDeparture");
            var actual = new Rect(placement.Bounds.Size).TransformToAABB(placement.TransformToVisual(overlay)!.Value);
            check(Math.Abs(actual.X - expected.X) < 0.1 && Math.Abs(actual.Y - expected.Y) < 0.1 &&
                  Math.Abs(actual.Width - expected.Width) < 0.1 && Math.Abs(actual.Height - expected.Height) < 0.1,
                  $"departure preserves source rectangle at font scale {scale}");
            check(!placement.IsHitTestVisible && placement.Children.Single() is Image { Source: not null }, "departure is a noninteractive frozen image");
            using (var stream = new MemoryStream())
            {
                ((Avalonia.Media.Imaging.Bitmap)((Image)placement.Children.Single()).Source!).Save(stream);
                using var pixels = SkiaSharp.SKBitmap.Decode(stream.ToArray());
                var middle = pixels.GetPixel(160, 18);
                check(middle.Blue > 200 && middle.Green > 100 && middle.Red < 80 && middle.Alpha > 240,
                    $"departure snapshot keeps the far edge of the card content at scale {scale}");
                check(pixels.GetPixel(0, 0).Alpha < 30, "departure snapshot preserves rounded transparent corners");
            }
            Pump(350);
            check(!overlay.Children.Contains(placement), "departure is cleaned up after exit");
            host.Children.Remove(scaled);
        }

        var tiles = new AdaptiveTilePanel { MinItemWidth = 64, Gap = 10 };
        for (var i = 0; i < 13; i++) tiles.Children.Add(new Border { Height = 78 });
        host.Children.Add(tiles); Pump(30);
        check(tiles.Children.All(c => c.Bounds.Width >= 64), "idol grid respects minimum tile width");
        check(tiles.Children.Select(c => c.Bounds.Width).Distinct().Count() == 1, "idol grid distributes equal widths");
        tiles.MaxColumns = 2; tiles.InvalidateMeasure(); Pump(30);
        check(tiles.Children[0].Bounds.Width > 380 && tiles.Children[2].Bounds.Y > 0,
            "two-column item panels fill the row and wrap remaining children");
        host.Children.Clear();

        // Radius-zero panels must not pin the first modal's page snapshot for the whole session.
        var idleBlur = new BackdropBlur { Radius = 0 };
        host.Children.Add(idleBlur);
        var layer = OverlayLayer.GetOverlayLayer(host)!;
        var liveBlur = new BackdropBlur { Radius = 20 };
        var backdropField = typeof(BackdropBlur).GetField("_backdrop", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!;
        SkiaSharp.SKColor BackdropPixel()
        {
            var owner = backdropField.GetValue(liveBlur)!;
            var image = (SkiaSharp.SKImage)owner.GetType().GetProperty("Image")!.GetValue(owner)!;
            using var pixels = SkiaSharp.SKBitmap.FromImage(image);
            return pixels.GetPixel(200, 200);
        }
        host.Background = Brushes.Red;
        layer.Children.Add(liveBlur); Pump(80);
        check(backdropField.GetValue(idleBlur) == null, "zero-radius glass does not retain a backdrop registration");
        check(BackdropPixel().Red > 240, "first modal captures the current red page");
        layer.Children.Remove(liveBlur);
        host.Background = Brushes.Blue; Pump(40);
        layer.Children.Add(liveBlur); Pump(80);
        check(BackdropPixel().Blue > 240 && BackdropPixel().Red < 20, "reopened modal captures the new page instead of the previous background");
        liveBlur.IsVisible = false; Pump(20);
        check(backdropField.GetValue(liveBlur) == null, "hidden scrim releases its backdrop registration");
        layer.Children.Remove(liveBlur); host.Children.Clear(); host.Background = null;

        var button = new Button { Content = "普通玻璃按钮", Width = 180, Height = 36,
            HorizontalAlignment = Avalonia.Layout.HorizontalAlignment.Left, VerticalAlignment = Avalonia.Layout.VerticalAlignment.Top };
        host.Children.Add(button); Pump(30);
        window.MouseMove(button.TranslatePoint(new Point(90, 18), window)!.Value); Pump(260);
        check(button.RenderTransform != null && button.RenderTransform.Value.M11 > 1, "ordinary button has geometric hover feedback");
        window.MouseMove(new Point(700, 500)); Pump(260);
        check(Math.Abs(button.RenderTransform!.Value.M11 - 1) < .001, "hover exit returns to identity");
        button.Classes.Add("lg-winctl"); button.Classes.Add("close");
        window.MouseMove(button.TranslatePoint(new Point(90, 18), window)!.Value); Pump(600);
        check(button.IsPointerOver && button.Background is ISolidColorBrush closeColor && closeColor.Color.R > 200 && closeColor.Color.G < 120,
            $"close control settles to red on hover (pointer={button.IsPointerOver}, background={button.Background})");
        LiquidTheme.ReduceMotion = true;
        LiquidStage.PlayDeparture(button); Pump(10);
        check(!OverlayLayer.GetOverlayLayer(button)!.Children.OfType<Canvas>().Any(c => c.Name == "LiquidDeparture"), "reduced motion leaves no removal snapshot");
        LiquidTheme.ReduceMotion = false;
        window.Close();
    }
}
