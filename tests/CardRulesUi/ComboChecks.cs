using Avalonia;
using Avalonia.Automation;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Media.Imaging;
using Avalonia.Styling;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.Views.Pages;

internal static class ComboChecks
{
    public static void Run(CardRulesViewModel model,string output,Action<bool,string> check)
    {
        model.AddRule("国民的アイドル");
        var rule=model.Selected!;
        check(new PlayCardRule().ComboTarget=="","existing rules default no pairing");
        rule.ComboTarget="";
        foreach(var theme in new[]{ThemeVariant.Light,ThemeVariant.Dark})
        {
            Application.Current!.RequestedThemeVariant=theme;
            var view=new CardRulesView(model);
            var window=new Window{Width=1120,Height=1100,Content=view};window.Show();Dispatcher.UIThread.RunJobs();
            var slider=view.GetVisualDescendants().OfType<Slider>().First(s=>AutomationProperties.GetName(s)=="调整出牌档位");
            slider.Value=1;Dispatcher.UIThread.RunJobs();
            check(rule.Tier=="R"&&PlayCardPolicy.EffectiveTier("SSR",rule)==1,"SSR R battle slider "+theme);
            slider.Value=2;Dispatcher.UIThread.RunJobs();
            check(rule.Tier=="SR","SSR SR battle slider "+theme);
            var picker=view.GetVisualDescendants().OfType<Button>().First(b=>AutomationProperties.GetName(b)=="选择要生效的牌");
            picker.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));Dispatcher.UIThread.RunJobs();
            var search=view.GetVisualDescendants().OfType<TextBox>().Last();search.Text="スポットライト";Dispatcher.UIThread.RunJobs();
            var card=view.GetVisualDescendants().OfType<Button>().First(b=>b.IsEffectivelyEnabled&&b.GetVisualDescendants().OfType<TextBlock>().Any(t=>t.Text=="スポットライト"));
            card.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));Dispatcher.UIThread.RunJobs();
            var use=view.GetVisualDescendants().OfType<Button>().First(b=>b.Content is string s&&s=="使用此卡");
            use.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));Dispatcher.UIThread.RunJobs();
            check(rule.ComboTarget=="スポットライト","pair target selected "+theme);
            check(model.Save(),model.Status);
            using(var reload=new CardRulesViewModel(Path.GetDirectoryName(Path.GetDirectoryName(model.ConfigPath))!))
                check(reload.Preset.Rules.Any(r=>r.Card=="国民的アイドル"&&r.ComboTarget=="スポットライト"&&r.Tier=="SR"),"pair and demotion persisted "+theme);
            view.Measure(new Size(1120,1100));view.Arrange(new Rect(0,0,1120,1100));Dispatcher.UIThread.RunJobs();
            using(var image=new RenderTargetBitmap(new PixelSize(1120,1100))){image.Render(view);image.Save(Path.Combine(output,$"national-{theme}.png"));}
            var clear=view.GetVisualDescendants().OfType<Button>().First(b=>b.Content is string s&&s=="清除");
            clear.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));Dispatcher.UIThread.RunJobs();
            check(rule.ComboTarget=="","clear target "+theme);
            window.Close();
        }
    }
}
