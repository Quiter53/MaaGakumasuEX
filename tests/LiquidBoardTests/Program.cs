using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Headless;
using Avalonia.Input;
using Avalonia.Themes.Fluent;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Controls.Liquid;
using SukiUI;

// Workbench layout model and native board interaction checks.
int count = 0;
void Check(bool ok, string name) { if (!ok) throw new Exception("FAIL " + name); count++; Console.WriteLine("PASS " + name); }
bool All(string id) => true;
bool NoLive(string id) => id != "live_view";

void ValidGeometry(BoardGeometry g, BoardLayout layout, double w, double h, Func<string, bool> visible, string name)
{
    var expected = BoardLayout.PanelIds.Where(visible).ToList();
    Check(g.Rects.Keys.OrderBy(x => x).SequenceEqual(expected.OrderBy(x => x)), name + " panels present once");
    foreach (var (id, r) in g.Rects)
    {
        Check(double.IsFinite(r.X) && double.IsFinite(r.Y) && r.Width >= BoardSolver.MinWidths[id] - 0.01 && r.Height >= BoardSolver.MinHeight - 0.01, name + " min size " + id);
        Check(r.X >= -0.01 && r.Y >= -0.01 && r.Right <= g.Width + 0.01 && r.Bottom <= g.Height + 0.01, name + " inside bounds " + id);
    }
    var list = g.Rects.Values.ToList();
    for (int i = 0; i < list.Count; i++) for (int j = i + 1; j < list.Count; j++)
    {
        var a = list[i]; var b = list[j];
        var overlap = a.X < b.Right - 0.01 && b.X < a.Right - 0.01 && a.Y < b.Bottom - 0.01 && b.Y < a.Bottom - 0.01;
        Check(!overlap, name + " no overlap");
    }
    Check(g.Width >= w - 0.01 && g.Height >= h - 0.01, name + " fills viewport");
}

// Allocation: minimums first, then weights.
var sizes = BoardSolver.Allocate(1000, [1, 1, 1], [500, 100, 100]);
Check(Math.Abs(sizes.Sum() - 1000) < 0.01 && sizes[0] == 500 && Math.Abs(sizes[1] - 250) < 0.01, "allocate satisfies minimum then splits");

foreach (var (w, h) in new[] { (1340.0, 860.0), (1020.0, 660.0), (700.0, 500.0) })
{
    var d = BoardLayout.Default();
    var g = BoardSolver.Solve(d, w, h, NoLive);
    ValidGeometry(g, d, w, h, NoLive, $"default {w}x{h}");
    Check(g.Rects["task_list"].Column == g.Rects["task_desc"].Column && g.Rects["task_list"].Y < g.Rects["task_desc"].Y, "default: list above description " + w);
    Check(g.Rects["task_settings"].X > g.Rects["task_list"].X && g.Rects["log"].X > g.Rects["task_settings"].X, "default: settings centre, log right " + w);
    Check(!g.Rects.ContainsKey("live_view"), "default: live view hidden " + w);
}
var withLive = BoardSolver.Solve(BoardLayout.Default(), 1340, 860, All);
Check(withLive.Rects["live_view"].Column == withLive.Rects["log"].Column && withLive.Rects["live_view"].Y < withLive.Rects["log"].Y, "live view above log when shown");

// Moves.
var m = BoardSolver.Move(BoardLayout.Default(), "log", "task_list", BoardEdge.Bottom);
Check(m.IsValid() && m.Columns.Count == 3 && m.Columns[0].Items.Select(i => i.Id).SequenceEqual(new[] { "task_list", "log", "task_desc" }), "bottom drop stacks in the same column");
var c = BoardSolver.Move(BoardLayout.Default(), "task_desc", "task_settings", BoardEdge.Left);
Check(c.IsValid() && c.Columns.Count == 4 && c.Columns[1].Items.Single().Id == "task_desc", "left drop creates a column");
var e = BoardSolver.Move(BoardLayout.Default(), "task_settings", "log", BoardEdge.Top);
Check(e.IsValid() && e.Columns.Count == 2 && e.Columns.All(col => col.Items.Count > 0), "empty column removed after move");
var k = BoardSolver.KeyboardMove(BoardLayout.Default(), "task_list", 0, 1, NoLive)!;
Check(k.Columns[0].Items.Select(i => i.Id).SequenceEqual(new[] { "task_desc", "task_list" }), "keyboard down reorders");
var kr = BoardSolver.KeyboardMove(BoardLayout.Default(), "task_desc", 1, 0, NoLive)!;
Check(kr.ColumnOf("task_desc") == "center", "keyboard right moves to next column");

// Resize respects minimum widths and heights.
var g0 = BoardSolver.Solve(BoardLayout.Default(), 1340, 860, NoLive);
var rc = BoardSolver.ResizeColumns(BoardLayout.Default(), g0, "left", "center", -2000);
var g1 = BoardSolver.Solve(rc, 1340, 860, NoLive);
Check(Math.Abs(g1.Rects["task_list"].Width - 245) < 0.5, "column resize clamps at minimum width");
var rr = BoardSolver.ResizeRows(BoardLayout.Default(), g0, "task_list", "task_desc", 5000);
var g2 = BoardSolver.Solve(rr, 1340, 860, NoLive);
Check(Math.Abs(g2.Rects["task_desc"].Height - BoardSolver.MinHeight) < 0.5, "row resize clamps at minimum height");

// Fixed-seed random sequence of moves/resizes keeps the model valid.
var rnd = new Random(81031);
var layout = BoardLayout.Default();
for (int i = 0; i < 400; i++)
{
    var visible = rnd.Next(2) == 0 ? (Func<string, bool>)All : NoLive;
    var w = rnd.Next(700, 2200); var h = rnd.Next(500, 1300);
    var g = BoardSolver.Solve(layout, w, h, visible);
    var ids = g.Rects.Keys.ToArray();
    var id = ids[rnd.Next(ids.Length)]; var target = ids[rnd.Next(ids.Length)];
    if (rnd.Next(4) == 0 && g.Columns.Count > 1)
    {
        var ci = rnd.Next(g.Columns.Count - 1);
        layout = BoardSolver.ResizeColumns(layout, g, g.Columns[ci].Id, g.Columns[ci + 1].Id, rnd.Next(-400, 400));
    }
    else if (id != target) layout = BoardSolver.Move(layout, id, target, (BoardEdge)rnd.Next(4));
    if (!layout.IsValid() || layout.Columns.Count > 5) layout = BoardLayout.Default();
    var after = BoardSolver.Solve(layout, w, h, visible);
    ValidGeometry(after, layout, w, h, visible, "random step " + i);
}

// Legacy 12x8 grid migration (2.1.x default positions).
var legacy = BoardSolver.FromLegacy([("task_list", 0, 0, 4, 8), ("settings", 4, 0, 4, 5), ("task_desc", 4, 5, 4, 3), ("log", 8, 4, 4, 4), ("live_view", 8, 0, 4, 4)]);
Check(legacy.IsValid() && legacy.ColumnOf("task_settings") == legacy.ColumnOf("task_desc") && legacy.ColumnOf("log") == legacy.ColumnOf("live_view"), "legacy grid layout migrates by column");
Check(BoardSolver.FromLegacy([("task_list", 0, 0, 4, 8), ("task_list", 4, 0, 4, 8)]).IsValid(), "corrupt legacy layout falls back to default");
var json = Newtonsoft.Json.JsonConvert.SerializeObject(legacy);
Check(Newtonsoft.Json.JsonConvert.DeserializeObject<BoardLayout>(json)!.IsValid(), "layout round-trips JSON");

// Native board: drag preview, commit, cancel; content controls survive.
AppBuilder.Configure<TestApp>().UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing = false }).UseSkia().SetupWithoutStarting();
var board = new LiquidBoard { Layout = BoardLayout.Default() };
var inputs = new Dictionary<string, TextBox>();
foreach (var id in BoardLayout.PanelIds)
{
    var box = new TextBox { Text = "draft " + id };
    inputs[id] = box;
    board.Children.Add(new BoardPanel { PanelId = id, Title = id, Content = box });
}
var window = new Window { Width = 1400, Height = 900, Content = board };
window.Show(); Dispatcher.UIThread.RunJobs();
void Pump() { for (int i = 0; i < 4; i++) { Dispatcher.UIThread.RunJobs(); AvaloniaHeadlessPlatform.ForceRenderTimerTick(); } }
Pump();
BoardPanel Panel(string id) => board.Children.OfType<BoardPanel>().First(p => p.PanelId == id);
Point Handle(string id) => Panel(id).GetVisualDescendants().OfType<Button>().First(b => b.Name == "PART_DragHandle").TranslatePoint(new Point(12, 15), window)!.Value;
Point Center(string id) { var p = Panel(id); return p.TranslatePoint(new Point(p.Bounds.Width / 2, p.Bounds.Height * 0.8), window)!.Value; }
inputs["task_settings"].Focus(); inputs["task_settings"].Text = "unsaved edit";
var committed = 0; board.LayoutCommitted += _ => committed++;
// Drag the log below the task list.
var from = Handle("log"); var to = Center("task_list");
window.MouseDown(from, MouseButton.Left); window.MouseMove(from + new Vector(20, 0)); window.MouseMove(to); Pump();
Check(board.Children.OfType<Border>().Any(b => b.Child is Image && b.IsVisible) && Panel("log").Opacity < 1, "drag shows a non-interactive ghost and a placeholder");
Check(board.Layout.ColumnOf("log") == "right", "layout not committed while dragging");
window.MouseUp(to, MouseButton.Left); Pump();
Check(committed == 1 && board.Layout.ColumnOf("log") == board.Layout.ColumnOf("task_list"), "drop commits the previewed move");
Check(ReferenceEquals(Panel("task_settings").Content, inputs["task_settings"]) && inputs["task_settings"].Text == "unsaved edit", "content control and draft survive the move");
// Escape cancels and restores.
var before = Newtonsoft.Json.JsonConvert.SerializeObject(board.Layout);
from = Handle("task_desc"); to = Center("task_settings");
window.MouseDown(from, MouseButton.Left); window.MouseMove(from + new Vector(30, 0)); window.MouseMove(to); Pump();
window.KeyPress(Key.Escape, RawInputModifiers.None, PhysicalKey.Escape, null); Pump();
window.MouseUp(to, MouseButton.Left); Pump();
Check(Newtonsoft.Json.JsonConvert.SerializeObject(board.Layout) == before && committed == 1, "escape cancels drag without committing");
// Release outside the board cancels.
from = Handle("task_desc");
window.MouseDown(from, MouseButton.Left); window.MouseMove(from + new Vector(30, 0)); window.MouseMove(new Point(-50, -50)); window.MouseUp(new Point(-50, -50), MouseButton.Left); Pump();
Check(Newtonsoft.Json.JsonConvert.SerializeObject(board.Layout) == before, "release outside the workspace cancels");
// Splitter changes column widths through the same model.
var splitter = board.Children.OfType<Border>().First(b => b.Classes.Contains("lg-splitter") && b.Classes.Contains("column") && b.IsVisible);
var sp = splitter.TranslatePoint(new Point(splitter.Bounds.Width / 2, splitter.Bounds.Height / 2), window)!.Value;
var leftWidth = Panel("task_list").Bounds.Width;
window.MouseDown(sp, MouseButton.Left); window.MouseMove(sp + new Vector(60, 0)); window.MouseUp(sp + new Vector(60, 0), MouseButton.Left); Pump();
Check(Panel("task_list").Bounds.Width > leftWidth + 30 && committed == 2, "column splitter resizes and commits");
// Keyboard move on the drag handle.
var handle = Panel("task_desc").GetVisualDescendants().OfType<Button>().First(b => b.Name == "PART_DragHandle");
handle.Focus(); window.KeyPress(Key.Right, RawInputModifiers.None, PhysicalKey.ArrowRight, null); Pump();
Check(committed == 3, "keyboard move commits");
// Live view toggles reallocate space; maximize shows only one panel.
board.LiveViewOpen = true; Pump();
Check(Panel("live_view").IsVisible && Panel("live_view").Bounds.Width > 0, "live view panel shown when enabled");
board.MaximizedId = "task_settings"; Pump();
Check(board.Children.OfType<BoardPanel>().Count(p => p.IsVisible) == 1 && Math.Abs(Panel("task_settings").Bounds.Width - board.Bounds.Width) < 1, "maximize fills the board");
board.MaximizedId = null; Pump();
Check(board.Children.OfType<BoardPanel>().Count(p => p.IsVisible) == 5, "restore brings all panels back");
// Capture loss mid-drag cancels.
before = Newtonsoft.Json.JsonConvert.SerializeObject(board.Layout);
from = Handle("log");
window.MouseDown(from, MouseButton.Left); window.MouseMove(from + new Vector(30, 0)); window.MouseMove(Center("task_settings")); Pump();
board.CancelGesture(); Pump();
window.MouseUp(Center("task_settings"), MouseButton.Left); Pump();
Check(Newtonsoft.Json.JsonConvert.SerializeObject(board.Layout) == before, "cancelled gesture leaves the layout unchanged");
Console.WriteLine($"LIQUID_BOARD_TESTS_PASSED {count} checks");

class TestApp : Application
{
    public override void Initialize()
    {
        Styles.Add(new FluentTheme());
        Styles.Add(new SukiTheme { ThemeColor = SukiUI.Enums.SukiColor.Blue });
        LiquidTheme.Install(this);
    }
}
