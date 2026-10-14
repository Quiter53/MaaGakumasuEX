using System;
using System.Globalization;
using System.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Media;
using MFAAvalonia.ViewModels.Pages;
namespace MFAAvalonia.Views.Pages;

public sealed class CardRuleCurve : Control
{
    public PlayCardRule? Rule { get; set; }
    public int PreviewTurn { get; set; } = 5;
    public bool Mini { get; set; }
    public bool Dark { get; set; }
    public event Action<int,int,int,bool>? PointChanged;
    private int _drag = -1;
    private int Maximum => Math.Max(12,Rule?.Points.Max(p=>p[0])??12);
    private double Left => Mini?2:43;
    private double Right => Math.Max(Left+10,Bounds.Width-(Mini?2:20));
    private double Bottom => Math.Max(10,Bounds.Height-(Mini?2:36));
    private double Top => Mini?2:22;
    private double X(double t) => Left+(Maximum-t)/(Maximum-1)*(Right-Left);
    private double Y(double v) => Bottom-v/100*(Bottom-Top);
    private IBrush Accent => Brush.Parse(Dark?"#73A3FF":"#2463EB");
    private IBrush Muted => Brush.Parse(Dark?"#A4B0C3":"#667085");
    public CardRuleCurve() { Focusable=true;ClipToBounds=true; }
    public override void Render(DrawingContext context)
    {
        base.Render(context);if(Rule==null)return;var r=Rule;
        var line=new Pen(Brush.Parse(Dark?"#364152":"#E7EAF0"),1);
        if(!Mini)
        {
            void Shade(double a,double b)
            {
                if(b<=a)return;var rect=new Rect(a,Top,b-a,Bottom-Top);
                context.DrawRectangle(Brush.Parse(Dark?"#303B4B":"#EEF0F5"),null,rect);
                using(context.PushClip(rect))for(double x=a-200;x<b+200;x+=9)context.DrawLine(line,new Point(x,Bottom),new Point(x+120,Top));
            }
            if(r.Enabled&&r.Limited){if(r.Max<Maximum)Shade(Left,X(r.Max));if(r.Min>1)Shade(X(r.Min),Right);}
            foreach(int score in new[]{0,25,50,75,100}){context.DrawLine(line,new Point(Left,Y(score)),new Point(Right,Y(score)));Text(context,score.ToString(),new Point(5,Y(score)-7));}
            for(int t=Maximum;t>=1;t--)if(Maximum<=15||t==1||t%2==0)Text(context,t.ToString(),new Point(X(t)-5,Bottom+8));
            Text(context,"剩余回合 →",new Point(Right-80,Bottom+23));
            var preview=Math.Clamp(PreviewTurn,1,Maximum);context.DrawLine(new Pen(Brush.Parse("#55A994"),1,new DashStyle(new double[]{4,4},0)),new Point(X(preview),Top),new Point(X(preview),Bottom));
        }
        var geometry=new StreamGeometry();
        using(var g=geometry.Open())
        {
            g.BeginFigure(new Point(X(Maximum),Y(PlayCardPolicy.Priority(r,Maximum))),false);
            var knots=r.Points.Select(p=>p[0]).Append(Maximum).Append(1).Distinct().OrderByDescending(t=>t).ToArray();
            for(int i=1;i<knots.Length;i++){int t=knots[i];if(r.Mode=="step")g.LineTo(new Point(X(t),Y(PlayCardPolicy.Priority(r,knots[i-1]))));g.LineTo(new Point(X(t),Y(PlayCardPolicy.Priority(r,t))));}g.EndFigure(false);
        }
        context.DrawGeometry(null,new Pen(r.Enabled?Accent:Muted,Mini?1.7:2.5),geometry);
        if(!Mini)foreach(var p in r.Points)context.DrawEllipse(Brush.Parse(Dark?"#202733":"#FFFFFF"),new Pen(Accent,3),new Point(X(p[0]),Y(p[1])),6,6);
    }
    private void Text(DrawingContext context,string value,Point point) => context.DrawText(new FormattedText(value,CultureInfo.CurrentCulture,FlowDirection.LeftToRight,new Typeface("Segoe UI"),11,Muted),point);
    protected override void OnPointerPressed(PointerPressedEventArgs e)
    {
        base.OnPointerPressed(e);if(Mini||Rule==null||!e.GetCurrentPoint(this).Properties.IsLeftButtonPressed)return;
        var point=e.GetPosition(this);_drag=Rule.Points.FindIndex(p=>Math.Abs(X(p[0])-point.X)<14&&Math.Abs(Y(p[1])-point.Y)<14);
        if(_drag<0)return;e.Pointer.Capture(this);Focus();e.Handled=true;
    }
    protected override void OnPointerMoved(PointerEventArgs e)
    {
        base.OnPointerMoved(e);if(_drag<0||Rule==null)return;var pos=e.GetPosition(this);
        int turn=Math.Clamp((int)Math.Round(Maximum-(pos.X-Left)/(Right-Left)*(Maximum-1)),1,Maximum),score=Math.Clamp((int)Math.Round((Bottom-pos.Y)/(Bottom-Top)*100),0,100);
        if(Rule.Points.Where((_,i)=>i!=_drag).Any(p=>p[0]==turn))turn=Rule.Points[_drag][0];PointChanged?.Invoke(_drag,turn,score,false);InvalidateVisual();e.Handled=true;
    }
    protected override void OnPointerReleased(PointerReleasedEventArgs e)
    {
        base.OnPointerReleased(e);if(_drag<0||Rule==null)return;var i=_drag;_drag=-1;e.Pointer.Capture(null);PointChanged?.Invoke(i,Rule.Points[i][0],Rule.Points[i][1],true);e.Handled=true;
    }
    protected override void OnPointerCaptureLost(PointerCaptureLostEventArgs e) { _drag=-1;base.OnPointerCaptureLost(e); }
}
