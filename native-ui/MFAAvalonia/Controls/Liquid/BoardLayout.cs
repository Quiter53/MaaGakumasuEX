using Newtonsoft.Json;
using System;
using System.Collections.Generic;
using System.Linq;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>Persisted workbench layout: columns of stacked panels with relative weights.</summary>
public sealed class BoardLayout
{
    public const int CurrentVersion = 1;
    public static readonly string[] PanelIds = ["task_list", "task_desc", "task_settings", "log", "live_view"];

    [JsonProperty("version")] public int Version { get; set; } = CurrentVersion;
    [JsonProperty("columns")] public List<BoardColumn> Columns { get; set; } = [];

    public static BoardLayout Default() => new()
    {
        Columns =
        [
            new() { Id = "left", Weight = 24, Items = [new() { Id = "task_list", Weight = 1 }, new() { Id = "task_desc", Weight = 1 }] },
            new() { Id = "center", Weight = 42, Items = [new() { Id = "task_settings", Weight = 1 }] },
            new() { Id = "right", Weight = 34, Items = [new() { Id = "live_view", Weight = 0.7 }, new() { Id = "log", Weight = 1 }] },
        ]
    };

    public BoardLayout Clone() => JsonConvert.DeserializeObject<BoardLayout>(JsonConvert.SerializeObject(this))!;

    public bool IsValid()
    {
        if (Version != CurrentVersion || Columns is not { Count: > 0 and <= 5 }) return false;
        var columns = new HashSet<string>();
        var ids = new List<string>();
        foreach (var c in Columns)
        {
            if (c == null || string.IsNullOrEmpty(c.Id) || !columns.Add(c.Id) || !double.IsFinite(c.Weight) || c.Weight <= 0 || c.Items is not { Count: > 0 }) return false;
            foreach (var i in c.Items)
            {
                if (i == null || !PanelIds.Contains(i.Id) || !double.IsFinite(i.Weight) || i.Weight <= 0) return false;
                ids.Add(i.Id);
            }
        }
        return ids.Count == PanelIds.Length && ids.Distinct().Count() == PanelIds.Length;
    }

    public string? ColumnOf(string id) => Columns.FirstOrDefault(c => c.Items.Any(i => i.Id == id))?.Id;
}

public sealed class BoardColumn
{
    [JsonProperty("id")] public string Id { get; set; } = "";
    [JsonProperty("weight")] public double Weight { get; set; } = 1;
    [JsonProperty("items")] public List<BoardItem> Items { get; set; } = [];
}

public sealed class BoardItem
{
    [JsonProperty("id")] public string Id { get; set; } = "";
    [JsonProperty("weight")] public double Weight { get; set; } = 1;
}

public enum BoardEdge { Left, Right, Top, Bottom }

public readonly record struct BoardRect(double X, double Y, double Width, double Height, string Column)
{
    public double Right => X + Width;
    public double Bottom => Y + Height;
}

public sealed record BoardColumnGeometry(string Id, double X, double Width, double MinWidth, IReadOnlyList<string> Items);

public sealed record BoardGeometry(double Width, double Height, IReadOnlyDictionary<string, BoardRect> Rects, IReadOnlyList<BoardColumnGeometry> Columns);

public readonly record struct BoardDropTarget(string Id, BoardEdge Edge);

/// <summary>
/// Layout solver shared by arrangement, splitters, drag preview and keyboard moves.
/// Mirrors the prototype: minimum sizes are satisfied first, the remaining space is
/// split by weight, so panels can never overlap.
/// </summary>
public static class BoardSolver
{
    public const double Gap = 14;
    public const double MinHeight = 146;

    public static readonly IReadOnlyDictionary<string, double> MinWidths = new Dictionary<string, double>
    {
        ["task_list"] = 245, ["task_desc"] = 220, ["task_settings"] = 320, ["log"] = 260, ["live_view"] = 240,
    };

    public static double[] Allocate(double total, IReadOnlyList<double> weights, IReadOnlyList<double> minimums)
    {
        var sizes = new double[weights.Count];
        var pending = Enumerable.Range(0, weights.Count).ToList();
        var remaining = total;
        while (pending.Count > 0)
        {
            var sum = pending.Sum(i => weights[i]);
            var constrained = pending.Where(i => remaining * weights[i] / sum < minimums[i]).ToList();
            if (constrained.Count == 0)
            {
                foreach (var i in pending) sizes[i] = remaining * weights[i] / sum;
                break;
            }
            foreach (var i in constrained) { sizes[i] = minimums[i]; remaining -= minimums[i]; }
            pending.RemoveAll(constrained.Contains);
        }
        return sizes;
    }

    public static BoardGeometry Solve(BoardLayout layout, double width, double height, Func<string, bool> visible)
    {
        var columns = layout.Columns
            .Select(c => (c.Id, c.Weight, Items: c.Items.Where(i => visible(i.Id)).ToList()))
            .Where(c => c.Items.Count > 0).ToList();
        var mins = columns.Select(c => c.Items.Max(i => MinWidths[i.Id])).ToList();
        width = Math.Max(width, mins.Sum() + Gap * Math.Max(0, columns.Count - 1));
        if (columns.Count > 0)
            height = Math.Max(height, columns.Max(c => c.Items.Count * MinHeight + Gap * (c.Items.Count - 1)));
        var widths = Allocate(width - Gap * Math.Max(0, columns.Count - 1), columns.Select(c => c.Weight).ToList(), mins);
        var rects = new Dictionary<string, BoardRect>();
        var cols = new List<BoardColumnGeometry>();
        double x = 0;
        for (int index = 0; index < columns.Count; index++)
        {
            var col = columns[index];
            var heights = Allocate(height - Gap * (col.Items.Count - 1), col.Items.Select(i => i.Weight).ToList(), col.Items.Select(_ => MinHeight).ToList());
            double y = 0;
            for (int i = 0; i < col.Items.Count; i++)
            {
                rects[col.Items[i].Id] = new BoardRect(x, y, widths[index], heights[i], col.Id);
                y += heights[i] + Gap;
            }
            cols.Add(new BoardColumnGeometry(col.Id, x, widths[index], mins[index], col.Items.Select(i => i.Id).ToList()));
            x += widths[index] + Gap;
        }
        return new BoardGeometry(width, height, rects, cols);
    }

    /// <summary>Moves a panel relative to a target. Left/right create a new column, top/bottom stack.</summary>
    public static BoardLayout Move(BoardLayout baseLayout, string id, string target, BoardEdge edge)
    {
        var next = baseLayout.Clone();
        if (id == target) return next;
        var source = next.Columns.First(c => c.Items.Any(i => i.Id == id));
        var dest = next.Columns.First(c => c.Items.Any(i => i.Id == target));
        var item = source.Items.First(i => i.Id == id);
        source.Items.Remove(item);
        if (edge is BoardEdge.Left or BoardEdge.Right)
        {
            var column = new BoardColumn { Id = "column-" + id, Weight = dest.Weight / 2, Items = [new BoardItem { Id = id, Weight = 1 }] };
            while (next.Columns.Any(c => c.Id == column.Id)) column.Id += "-new";
            dest.Weight /= 2;
            next.Columns.Insert(next.Columns.IndexOf(dest) + (edge == BoardEdge.Right ? 1 : 0), column);
        }
        else
        {
            var at = dest.Items.FindIndex(i => i.Id == target);
            if (!ReferenceEquals(source, dest)) { dest.Items[at].Weight /= 2; item.Weight = dest.Items[at].Weight; }
            dest.Items.Insert(at + (edge == BoardEdge.Bottom ? 1 : 0), item);
        }
        next.Columns.RemoveAll(c => c.Items.Count == 0);
        return next;
    }

    /// <summary>Hit test against the stable pre-drag geometry so animated neighbours never chase the pointer.</summary>
    public static BoardDropTarget? DropTarget(BoardGeometry geometry, string dragging, double x, double y)
    {
        string? best = null; double distance = double.MaxValue; BoardRect bestRect = default;
        foreach (var (id, r) in geometry.Rects)
        {
            var dx = Math.Max(Math.Max(r.X - x, 0), x - r.Right);
            var dy = Math.Max(Math.Max(r.Y - y, 0), y - r.Bottom);
            var d = dx * dx + dy * dy;
            if (d < distance) { best = id; distance = d; bestRect = r; }
        }
        if (best == null || best == dragging) return null;
        var relative = (x - bestRect.X) / bestRect.Width;
        var edge = relative < .22 ? BoardEdge.Left : relative > .78 ? BoardEdge.Right : y < bestRect.Y + bestRect.Height / 2 ? BoardEdge.Top : BoardEdge.Bottom;
        return new BoardDropTarget(best, edge);
    }

    /// <summary>Column splitter: weights become current pixel widths, then the pair shares its total.</summary>
    public static BoardLayout ResizeColumns(BoardLayout baseLayout, BoardGeometry geometry, string a, string b, double delta)
    {
        var next = baseLayout.Clone();
        foreach (var c in geometry.Columns) next.Columns.First(col => col.Id == c.Id).Weight = c.Width;
        var ca = geometry.Columns.First(c => c.Id == a);
        var cb = geometry.Columns.First(c => c.Id == b);
        var total = ca.Width + cb.Width;
        var width = Math.Max(ca.MinWidth, Math.Min(total - cb.MinWidth, ca.Width + delta));
        next.Columns.First(c => c.Id == a).Weight = width;
        next.Columns.First(c => c.Id == b).Weight = total - width;
        return next;
    }

    /// <summary>Row splitter inside one column.</summary>
    public static BoardLayout ResizeRows(BoardLayout baseLayout, BoardGeometry geometry, string a, string b, double delta)
    {
        var next = baseLayout.Clone();
        var ra = geometry.Rects[a];
        var rb = geometry.Rects[b];
        var column = next.Columns.First(c => c.Id == ra.Column);
        foreach (var i in column.Items)
            if (geometry.Rects.TryGetValue(i.Id, out var r)) i.Weight = r.Height;
        var total = ra.Height + rb.Height;
        var height = Math.Max(MinHeight, Math.Min(total - MinHeight, ra.Height + delta));
        column.Items.First(i => i.Id == a).Weight = height;
        column.Items.First(i => i.Id == b).Weight = total - height;
        return next;
    }

    /// <summary>Keyboard alternative to dragging: arrows move within or across columns.</summary>
    public static BoardLayout? KeyboardMove(BoardLayout layout, string id, int dx, int dy, Func<string, bool> visible)
    {
        var columns = layout.Columns.Where(c => c.Items.Any(i => visible(i.Id))).ToList();
        var ci = columns.FindIndex(c => c.Items.Any(i => i.Id == id));
        if (ci < 0) return null;
        var items = columns[ci].Items.Where(i => visible(i.Id)).ToList();
        var at = items.FindIndex(i => i.Id == id);
        string? target = null; var edge = BoardEdge.Bottom;
        if (dy != 0)
        {
            var index = at + Math.Sign(dy);
            if (index >= 0 && index < items.Count) { target = items[index].Id; edge = dy < 0 ? BoardEdge.Top : BoardEdge.Bottom; }
        }
        else if (dx != 0)
        {
            var index = ci + Math.Sign(dx);
            if (index >= 0 && index < columns.Count) target = columns[index].Items.FirstOrDefault(i => visible(i.Id))?.Id;
        }
        return target == null ? null : Move(layout, id, target, edge);
    }

    /// <summary>
    /// Converts the 12×8 grid layout saved by 2.1.x into columns: cards sharing a start
    /// column become one stack ordered by row, spans become weights. Unknown or
    /// overlapping data falls back to the default layout.
    /// </summary>
    public static BoardLayout FromLegacy(IEnumerable<(string Id, int Col, int Row, int ColSpan, int RowSpan)> cells)
    {
        var map = new Dictionary<string, string> { ["settings"] = "task_settings", ["task_list"] = "task_list", ["task_desc"] = "task_desc", ["log"] = "log", ["live_view"] = "live_view" };
        var list = cells.Where(c => map.ContainsKey(c.Id)).Select(c => (Id: map[c.Id], c.Col, c.Row, ColSpan: Math.Max(1, c.ColSpan), RowSpan: Math.Max(1, c.RowSpan))).ToList();
        if (list.Select(c => c.Id).Distinct().Count() != list.Count) return BoardLayout.Default();
        var layout = new BoardLayout();
        foreach (var group in list.GroupBy(c => c.Col).OrderBy(g => g.Key))
        {
            layout.Columns.Add(new BoardColumn
            {
                Id = "col-" + group.Key,
                Weight = group.Max(c => c.ColSpan),
                Items = group.OrderBy(c => c.Row).Select(c => new BoardItem { Id = c.Id, Weight = c.RowSpan }).ToList()
            });
        }
        // Panels absent from the old layout keep their default placement.
        foreach (var id in BoardLayout.PanelIds.Where(id => list.All(c => c.Id != id)))
        {
            var target = id == "live_view" ? "log" : id == "task_desc" ? "task_list" : "task_settings";
            var host = layout.Columns.FirstOrDefault(c => c.Items.Any(i => i.Id == target)) ?? layout.Columns.LastOrDefault();
            if (host == null) return BoardLayout.Default();
            host.Items.Insert(id == "live_view" ? 0 : host.Items.Count, new BoardItem { Id = id, Weight = id == "live_view" ? 0.7 : 1 });
        }
        while (layout.Columns.Count > 5)
        {
            var last = layout.Columns[^1];
            layout.Columns.RemoveAt(layout.Columns.Count - 1);
            layout.Columns[^1].Items.AddRange(last.Items);
            layout.Columns[^1].Weight += last.Weight;
        }
        return layout.IsValid() ? layout : BoardLayout.Default();
    }
}
