using System;
using MFAAvalonia.Helper;
using System.Globalization;
using System.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Media;
using MFAAvalonia.ViewModels.Pages;
namespace MFAAvalonia.Views.Pages;

/// <summary>
/// Priority curve of one rule. Drawing, hit testing and dragging share one coordinate
/// transform and the policy's own <see cref="PlayCardPolicy.Priority"/>, so the line,
/// node labels and the hand simulation always agree with real play.
/// </summary>
public sealed class CardRuleCurve : Control
{
    public PlayCardRule? Rule { get; set; }
    public CardRulesViewModel? Model { get; set; }
    public int PreviewTurn { get; set; } = 5;
    public bool Mini { get; set; }
    /// <summary>Kept for compatibility; colours follow the theme resources.</summary>
    public bool Dark { get; set; }

    /// <summary>(index, turn, score, finished) — raised while dragging and on release.</summary>
    public event Action<int,int,int,bool>? PointChanged;
    /// <summary>Raised when a node cannot be deleted (at least two nodes are required).</summary>
    public event Action? DeleteRejected;

    private int[]? _drag;
    private bool _moved;

    private int Maximum => PlayCardPolicy.Total(Rule);
    private double Left => Mini ? 3 : 40;
    private double Right => Math.Max(Left + 10, Bounds.Width - (Mini ? 3 : 22));
    private double Top => Mini ? 3 : 26;
    private double Bottom => Math.Max(Top + 10, Bounds.Height - (Mini ? 3 : 26));
    private double X(double t) => Left + (Maximum - t) / Math.Max(1, Maximum - 1) * (Right - Left);
    private double Y(double v) => Bottom - v / 100 * (Bottom - Top);

    public CardRuleCurve() { Focusable = !Mini; ClipToBounds = false; Cursor = new Cursor(StandardCursorType.Hand); }

    private Color Res(string key, string fallback) =>
        this.TryFindResource(key, ActualThemeVariant, out var v) && v is Color c ? c : Color.Parse(fallback);

    public override void Render(DrawingContext context)
    {
        base.Render(context);
        if (Rule == null) return;
        var r = Rule;
        var accent = Res("LgAccentColor", "#007AFF");
        var muted = Res("LgTextMutedColor", "#94A3B8");
        var main = Res("LgTextMainColor", "#0F172A");
        var divider = Res("LgDividerColor", "#0F0F172A");
        var lineColor = r.Enabled ? accent : muted;
        var knots = r.Points.Select(p => p[0]).Append(Maximum).Append(1).Where(t => t >= 1 && t <= Maximum).Distinct().OrderByDescending(t => t).ToArray();

        // Same vertices as the policy: step mode holds the previous value until the node's turn.
        var vertices = new System.Collections.Generic.List<Point> { new(X(knots[0]), Y(PlayCardPolicy.Priority(r, knots[0]))) };
        for (int i = 1; i < knots.Length; i++)
        {
            if (r.Mode == "step") vertices.Add(new Point(X(knots[i]), Y(PlayCardPolicy.Priority(r, knots[i - 1]))));
            vertices.Add(new Point(X(knots[i]), Y(PlayCardPolicy.Priority(r, knots[i]))));
        }

        if (Mini)
        {
            var mg = new StreamGeometry();
            using (var g = mg.Open()) { g.BeginFigure(vertices[0], false); foreach (var v in vertices.Skip(1)) g.LineTo(v); g.EndFigure(false); }
            context.DrawGeometry(null, new Pen(new SolidColorBrush(lineColor), 2, lineCap: PenLineCap.Round, lineJoin: PenLineJoin.Round), mg);
            return;
        }

        var gridPen = new Pen(new SolidColorBrush(divider), 1);
        var mutedBrush = new SolidColorBrush(muted);
        if (r.Enabled && r.Limited)
        {
            var shade = new SolidColorBrush(Color.FromArgb(18, muted.R, muted.G, muted.B));
            var hatch = new Pen(new SolidColorBrush(Color.FromArgb(40, muted.R, muted.G, muted.B)), 1);
            void Shade(double a, double b)
            {
                if (b <= a) return;
                var rect = new Rect(a, Top, b - a, Bottom - Top);
                context.DrawRectangle(shade, null, rect, 6, 6);
                using (context.PushClip(rect))
                    for (double x = a - 200; x < b + 200; x += 9) context.DrawLine(hatch, new Point(x, Bottom), new Point(x + 120, Top));
            }
            if (r.Max < Maximum) Shade(Left - 6, X(r.Max) - 6);
            if (r.Min > 1) Shade(X(r.Min) + 6, Right + 6);
        }
        foreach (var score in new[] { 0, 50, 100 })
        {
            context.DrawLine(gridPen, new Point(Left, Y(score)), new Point(Right, Y(score)));
            Text(context, score.ToString(), new Point(Left - 8, Y(score)), muted, 11, alignRight: true);
        }
        for (int t = Maximum; t >= 1; t--)
            if (Maximum <= 15 || t == 1 || t == Maximum || t % 2 == 0)
                Text(context, t + "T", new Point(X(t), Bottom + 14), muted, 11, center: true);

        var preview = Math.Clamp(PreviewTurn, 1, Maximum);
        context.DrawLine(new Pen(new SolidColorBrush(Res("LgSuccessColor", "#34C759")), 1, new DashStyle([4, 4], 0)), new Point(X(preview), Top), new Point(X(preview), Bottom));

        var area = new StreamGeometry();
        using (var g = area.Open())
        {
            g.BeginFigure(new Point(vertices[0].X, Bottom), true);
            foreach (var v in vertices) g.LineTo(v);
            g.LineTo(new Point(vertices[^1].X, Bottom));
            g.EndFigure(true);
        }
        context.DrawGeometry(new LinearGradientBrush
        {
            StartPoint = new RelativePoint(0, 0, RelativeUnit.Relative), EndPoint = new RelativePoint(0, 1, RelativeUnit.Relative),
            GradientStops = { new GradientStop(Color.FromArgb(72, lineColor.R, lineColor.G, lineColor.B), 0), new GradientStop(Color.FromArgb(0, lineColor.R, lineColor.G, lineColor.B), 1) }
        }, null, area);
        var line = new StreamGeometry();
        using (var g = line.Open()) { g.BeginFigure(vertices[0], false); foreach (var v in vertices.Skip(1)) g.LineTo(v); g.EndFigure(false); }
        context.DrawGeometry(null, new Pen(new SolidColorBrush(lineColor), 2.8, lineCap: PenLineCap.Round, lineJoin: PenLineJoin.Round), line);

        foreach (var p in r.Points.Where(p => p[0] <= Maximum))
        {
            var c = new Point(X(p[0]), Y(p[1]));
            var dragging = ReferenceEquals(p, _drag);
            context.DrawEllipse(new SolidColorBrush(Color.FromArgb(56, lineColor.R, lineColor.G, lineColor.B)), null, c, dragging ? 12 : 9, dragging ? 12 : 9);
            context.DrawEllipse(new SolidColorBrush(lineColor), new Pen(Brushes.White, 2), c, 5.5, 5.5);
            Text(context, p[1].ToString(), new Point(c.X, c.Y - 16), main, 11, center: true, bold: true);
        }
    }

    private void Text(DrawingContext context, string value, Point anchor, Color color, double size, bool center = false, bool alignRight = false, bool bold = false)
    {
        var ft = new FormattedText(value, CultureInfo.CurrentCulture, FlowDirection.LeftToRight,
            new Typeface(FontFamily.Default, FontStyle.Normal, bold ? FontWeight.SemiBold : FontWeight.Normal), size, new SolidColorBrush(color));
        var x = center ? anchor.X - ft.Width / 2 : alignRight ? anchor.X - ft.Width : anchor.X;
        context.DrawText(ft, new Point(x, anchor.Y - ft.Height / 2));
    }

    private (int Turn, int Score) ToModel(Point p) =>
        (Math.Clamp((int)Math.Round(Maximum - (p.X - Left) / (Right - Left) * (Maximum - 1)), 1, Maximum),
         Math.Clamp((int)Math.Round((Bottom - p.Y) / (Bottom - Top) * 100), 0, 100));

    private int[]? HitNode(Point point) => Rule?.Points
        .Where(p => p[0] <= Maximum)
        .OrderBy(p => Math.Pow(X(p[0]) - point.X, 2) + Math.Pow(Y(p[1]) - point.Y, 2))
        .FirstOrDefault(p => Math.Abs(X(p[0]) - point.X) < 14 && Math.Abs(Y(p[1]) - point.Y) < 14);

    protected override void OnPointerPressed(PointerPressedEventArgs e)
    {
        base.OnPointerPressed(e);
        if (Mini || Rule == null || Model == null) return;
        var props = e.GetCurrentPoint(this).Properties;
        var point = e.GetPosition(this);
        var node = HitNode(point);
        if (props.IsRightButtonPressed || (props.IsLeftButtonPressed && e.ClickCount == 2 && node != null))
        {
            if (node != null)
            {
                if (Model.DeletePoint(Rule, node)) Raise(true);
                else DeleteRejected?.Invoke();
                e.Handled = true;
            }
            return;
        }
        if (!props.IsLeftButtonPressed) return;
        if (node == null)
        {
            if (point.X < Left - 10 || point.X > Right + 10 || point.Y < Top - 14 || point.Y > Bottom + 6) return;
            var (turn, score) = ToModel(point);
            node = Model.AddOrUpdatePoint(Rule, turn, score);
            if (node == null) return;
            _moved = true;
            Raise(false);
        }
        else _moved = false;
        _drag = node;
        e.Pointer.Capture(this);
        Focus();
        InvalidateVisual();
        e.Handled = true;
    }

    protected override void OnPointerMoved(PointerEventArgs e)
    {
        base.OnPointerMoved(e);
        if (_drag == null || Rule == null || Model == null) return;
        var (turn, score) = ToModel(e.GetPosition(this));
        if (Model.MovePoint(Rule, _drag, turn, score)) { _moved = true; Raise(false); InvalidateVisual(); }
        e.Handled = true;
    }

    protected override void OnPointerReleased(PointerReleasedEventArgs e)
    {
        base.OnPointerReleased(e);
        if (_drag == null) return;
        Finish();
        e.Pointer.Capture(null);
        e.Handled = true;
    }

    protected override void OnPointerCaptureLost(PointerCaptureLostEventArgs e)
    {
        Finish();
        base.OnPointerCaptureLost(e);
    }

    private void Finish()
    {
        if (_drag == null || Rule == null || Model == null) { _drag = null; return; }
        var point = _drag;
        _drag = null;
        if (_moved) { Model.CommitPoint(Rule, point); Raise(true); }
        InvalidateVisual();
    }

    private void Raise(bool done)
    {
        if (Rule == null) return;
        var p = _drag ?? Rule.Points.FirstOrDefault();
        var index = p == null ? -1 : Rule.Points.IndexOf(p);
        PointChanged?.Invoke(index, p?[0] ?? 0, p?[1] ?? 0, done);
    }
}
