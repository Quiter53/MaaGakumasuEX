using MFAAvalonia.Controls.Liquid;
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
            var window=new Window{Width=1120,Height=1000,Content=view};window.Show();Dispatcher.UIThread.RunJobs();
            T Named<T>(string name) where T:Control=>window.GetVisualDescendants().OfType<T>().First(c=>c.IsEffectivelyVisible&&AutomationProperties.GetName(c)==name);
            var tier=Named<SegmentedControl>("调整出牌档位");
            check(tier.Items.OfType<ListBoxItem>().Count(i=>i.IsEnabled)==3&&!tier.Items.OfType<ListBoxItem>().First().IsEnabled,"SSR may only choose R/SR/SSR "+theme);
            tier.SelectedItem=tier.Items.OfType<ListBoxItem>().First(i=>(string?)i.Tag=="R");Dispatcher.UIThread.RunJobs();
            check(rule.Tier=="R"&&PlayCardPolicy.EffectiveTier("SSR",rule)==1,"SSR R battle tier "+theme);
            tier=Named<SegmentedControl>("调整出牌档位");
            tier.SelectedItem=tier.Items.OfType<ListBoxItem>().First(i=>(string?)i.Tag=="SR");Dispatcher.UIThread.RunJobs();
            check(rule.Tier=="SR","SSR SR battle tier "+theme);
            Named<Button>("选择要生效的牌").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));Dispatcher.UIThread.RunJobs();
            var search=window.GetVisualDescendants().OfType<TextBox>().First(t=>t.Watermark?.ToString()=="搜索日文卡牌名称…");search.Text="スポットライト";
            Dispatcher.UIThread.RunJobs();Dispatcher.UIThread.RunJobs(DispatcherPriority.Background);
            check(!window.GetVisualDescendants().OfType<Button>().Any(b=>b.Classes.Contains("lg-picker-card")&&AutomationProperties.GetName(b)=="国民的アイドル"),"national excluded from its own pairing "+theme);
            var card=window.GetVisualDescendants().OfType<Button>().First(b=>b.Classes.Contains("lg-picker-card")&&AutomationProperties.GetName(b)=="スポットライト");
            card.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));Dispatcher.UIThread.RunJobs();
            var use=window.GetVisualDescendants().OfType<Button>().First(b=>b.Content is string s&&s=="使用此卡");
            use.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));Dispatcher.UIThread.RunJobs();
            check(rule.ComboTarget=="スポットライト","pair target selected "+theme);
            check(model.Save(),model.Status);
            using(var reload=new CardRulesViewModel(Path.GetDirectoryName(Path.GetDirectoryName(model.ConfigPath))!))
                check(reload.Preset.Rules.Any(r=>r.Card=="国民的アイドル"&&r.ComboTarget=="スポットライト"&&r.Tier=="SR"),"pair and demotion persisted "+theme);
            using(var image=new RenderTargetBitmap(new PixelSize(1120,1000))){image.Render(window);image.Save(Path.Combine(output,$"national-{theme}.png"));}
            Named<Button>("清除选择").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));Dispatcher.UIThread.RunJobs();
            check(rule.ComboTarget=="","clear target "+theme);
            window.Close();
        }
    }
}
