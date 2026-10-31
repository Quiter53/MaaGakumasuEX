using Avalonia;
using Avalonia.Animation;
using Avalonia.Animation.Easings;
using Avalonia.Controls;
using Avalonia.Media;
using Avalonia.Styling;
using System;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Threading.Tasks;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// Shared enter / exit motion (prototype stageGlide, modalPop, glassDismiss). Every run
/// cancels the previous one on the same element, so quick repeated toggles never leave
/// a half faded control behind. Reduced motion completes immediately.
/// </summary>
public static class LiquidStage
{
    public static readonly Easing Spring = new SplineEasing(0.22, 1, 0.36, 1);
    public static readonly Easing EaseIn = new SplineEasing(0.32, 0, 0.67, 0);
    private static readonly ConditionalWeakTable<Visual, CancellationTokenSource> Running = new();

    private static CancellationToken Restart(Visual target)
    {
        if (Running.TryGetValue(target, out var old)) { old.Cancel(); Running.Remove(target); }
        var cts = new CancellationTokenSource();
        Running.Add(target, cts);
        return cts.Token;
    }

    public static void Cancel(Visual target)
    {
        if (Running.TryGetValue(target, out var old)) { old.Cancel(); Running.Remove(target); }
        target.Opacity = 1;
    }

    private static Animation Build(TimeSpan duration, Easing easing, (double Opacity, double X, double Y, double Scale) from, (double Opacity, double X, double Y, double Scale) to)
    {
        KeyFrame Frame(double cue, (double Opacity, double X, double Y, double Scale) v) => new()
        {
            Cue = new Cue(cue),
            Setters =
            {
                new Setter(Visual.OpacityProperty, v.Opacity),
                new Setter(TranslateTransform.XProperty, v.X),
                new Setter(TranslateTransform.YProperty, v.Y),
                new Setter(ScaleTransform.ScaleXProperty, v.Scale),
                new Setter(ScaleTransform.ScaleYProperty, v.Scale),
            }
        };
        return new Animation { Duration = duration, Easing = easing, FillMode = FillMode.None, Children = { Frame(0, from), Frame(1, to) } };
    }

    /// <summary>Page switch: fade in while gliding 14px from the right.</summary>
    public static void PlayEnter(Visual target) =>
        _ = Run(target, Build(TimeSpan.FromMilliseconds(360), Spring, (0, 14, 0, 0.992), (1, 0, 0, 1)));

    /// <summary>Dialog / popover entrance.</summary>
    public static Task PlayPop(Visual target) =>
        Run(target, Build(TimeSpan.FromMilliseconds(340), Spring, (0, 0, 10, 0.92), (1, 0, 0, 1)));

    /// <summary>Dialog / menu exit. The caller completes the state change after the task.</summary>
    public static Task PlayPopOut(Visual target) =>
        Run(target, Build(TimeSpan.FromMilliseconds(200), EaseIn, (1, 0, 0, 1), (0, 0, 8, 0.93)), keepFinal: true);

    /// <summary>Removal of an item (rules, list cards, tasks): fade, shrink and slide left.</summary>
    public static Task PlayRemove(Visual target) =>
        Run(target, Build(TimeSpan.FromMilliseconds(220), EaseIn, (1, 0, 0, 1), (0, -18, 0, 0.88)), keepFinal: true);

    /// <summary>Sub panel unfolding below a toggle.</summary>
    public static void PlayExpand(Visual target) =>
        _ = Run(target, Build(TimeSpan.FromMilliseconds(300), Spring, (0, 0, -6, 0.97), (1, 0, 0, 1)));

    /// <summary>Toast slide up.</summary>
    public static void PlayToast(Visual target) =>
        _ = Run(target, Build(TimeSpan.FromMilliseconds(360), Spring, (0, 0, 18, 0.92), (1, 0, 0, 1)));

    private static async Task Run(Visual target, Animation animation, bool keepFinal = false)
    {
        var token = Restart(target);
        if (LiquidTheme.ReduceMotion)
        {
            target.Opacity = keepFinal ? 0 : 1;
            return;
        }
        try
        {
            await animation.RunAsync(target, token);
            if (keepFinal && !token.IsCancellationRequested) target.Opacity = 0;
        }
        catch (OperationCanceledException) { }
        finally
        {
            if (Running.TryGetValue(target, out var cts) && cts.Token == token) Running.Remove(target);
        }
    }
}
