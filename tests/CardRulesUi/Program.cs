using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Themes.Fluent;
using Avalonia.Headless;
using Avalonia.Media.Imaging;
using Avalonia.Styling;
using Avalonia.Threading;
using Avalonia.VisualTree;
using Avalonia.Interactivity;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.Views.Pages;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using SukiUI;
using MFAAvalonia.Helper;

internal class Program
{
    internal static string Root = "";
    [STAThread] public static void Main(string[] args)
    {
        Root = Path.GetFullPath(args.Length>0?args[0]:File.ReadAllText(Path.Combine(AppContext.BaseDirectory,"fixture-root.txt")).Trim());
        if(args.Length==0||args.Contains("--interactive"))AppBuilder.Configure<RulesTestApp>().UsePlatformDetect().StartWithClassicDesktopLifetime(args);
        else RunTests(args[1]);
    }
    private static void RunTests(string output)
    {
        Directory.CreateDirectory(output);
        AppBuilder.Configure<RulesTestApp>().UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing=false }).UseSkia().SetupWithoutStarting();
        int count=0;void Check(bool ok,string name){if(!ok)throw new Exception(name);count++;Console.WriteLine("PASS "+name);}
        using var vm=new CardRulesViewModel(Root);Check(vm.Loaded,vm.Status);Check(vm.Cards.Count==446,"446 cards loaded including October additions");
        Check(vm.Document.Presets.Count==6&&vm.Preset.Name=="干劲","six builtin presets");
        var baseline=PlayCardPolicy.Clone(vm.Document);
        var rule=vm.Selected!;var initialMax=rule.Max;var initialRuleCount=vm.Preset.Rules.Count;Check(rule.Card=="届いて！"&&rule.Max<=2,"Japanese default rule");
        vm.PreviewTurn=5;Check(vm.Preview().Last().Card.Name=="届いて！"&&!vm.Preview().Last().Allowed,"timing in preview");
        vm.EditPreset("验证预设","集中",true,true);Check(vm.Preset.Rules.Count==initialRuleCount&&!vm.Preset.Builtin,"copy preset");
        vm.Preset.Rules[0].Max=4;Check(vm.Document.Presets[2].Rules[0].Max==initialMax,"deep copy isolation");
        vm.EditPreset("重命名预设","全力",false,false);Check(vm.Preset.Name=="重命名预设"&&vm.Preset.Career=="全力","rename/career");
        Check(vm.Save(),vm.Status);
        using(var reload=new CardRulesViewModel(Root))Check(reload.Preset.Name=="重命名预设"&&reload.Preset.Rules[0].Max==4,"native save/reload");
        using(var stale=new CardRulesViewModel(Root)){vm.Preset.Rules[0].Max=5;Check(vm.Save(),vm.Status);Check(!stale.Save()&&stale.Status.Contains("其他窗口"),"concurrent window conflict");}
        vm.DeletePreset();Check(vm.Document.Presets.Count==6,"delete custom");vm.SwitchPreset("career-2");
        var sr=vm.Cards.First(c=>c.Rarity=="SR"&&!c.Extra);vm.AddRule(sr.Name);vm.Selected!.Tier="SSR";vm.Selected.Points=[[12,99],[1,99]];
        vm.SetHand(0,sr.Name);vm.SetHand(1,sr.Name);Check(vm.Preset.Hand[0]==vm.Preset.Hand[1],"duplicate hand slots");Check(PlayCardPolicy.EffectiveTier("SR",vm.Selected)==3,"SR promotion");
        Check(!vm.SetPoint(0,1,50),"duplicate curve turn rejected");Check(vm.SetPoint(0,12,92),"numeric node update");vm.AddPoint();Check(vm.Selected.Points.Count==3,"add node");
        foreach(var c in vm.Cards)
        {
            var rare=Array.IndexOf(new[]{"","LR","SSR","SR","R","N"},c.Rarity);var origin=Array.IndexOf(new[]{"","none","idol","support"},c.Origin);
            Check(vm.Filter(c.Name,rare,c.PlanType,c.Category,origin,c.IsConversion?2:1,c.Extra?1:2,0,0).Any(x=>x.Name==c.Name),"combined filters "+c.Name);
        }
        // Values exported from the actual native policy are compared to Python.
        var vectors=new JArray();
        foreach(string rarity in PlayCardPolicy.Tiers)foreach(string? tier in PlayCardPolicy.Tiers.Cast<string?>().Append(null))foreach(bool enabled in new[]{true,false})foreach(string mode in new[]{"linear","step"})for(int turn=1;turn<=30;turn++)
        {
            var r=new PlayCardRule{Card="届いて！",Enabled=enabled,Limited=true,Min=1,Max=2,Tier=tier,Mode=mode,Points=[[29,17],[13,96],[1,3]]};
            vectors.Add(new JObject{["rarity"]=rarity,["rule"]=JObject.FromObject(r,JsonSerializer.Create(PlayCardPolicy.Json)),["turn"]=turn,["result"]=new JArray(PlayCardPolicy.Allowed(r,turn),PlayCardPolicy.EffectiveTier(rarity,r),PlayCardPolicy.Priority(r,turn))});
        }
        File.WriteAllText(Path.Combine(output,"native-policy-vectors.json"),vectors.ToString());
        // Restore isolated test config before rendering the approved defaults.
        File.WriteAllText(vm.ConfigPath,JsonConvert.SerializeObject(baseline,PlayCardPolicy.Json));vm.Load();
        foreach(var theme in new[]{ThemeVariant.Light,ThemeVariant.Dark})foreach(var width in new[]{800,1120,1500})
        {
            Application.Current!.RequestedThemeVariant=theme;
            var view=new CardRulesView(vm);var window=new Window{Width=width,Height=1100,Content=view};window.Show();Dispatcher.UIThread.RunJobs();
            void Shot(string name){view.Measure(new Size(width,1100));view.Arrange(new Rect(0,0,width,1100));Dispatcher.UIThread.RunJobs();using var bitmap=new RenderTargetBitmap(new PixelSize(width,1100));bitmap.Render(view);bitmap.Save(Path.Combine(output,$"{name}-{width}-{theme}.png"));}
            Shot("native");
            void Click(string label){var button=view.GetVisualDescendants().OfType<Button>().First(b=>b.IsEffectivelyEnabled&&b.IsVisible&&b.Content is string s&&s==label);button.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));Dispatcher.UIThread.RunJobs();}
            if(width==1120&&theme==ThemeVariant.Light)
            {
                var curve=view.GetVisualDescendants().OfType<CardRuleCurve>().First(c=>!c.Mini);
                var origin=curve.TranslatePoint(new Point(0,0),window)!.Value;
                var start=origin+new Vector(43,(222-36+22)/2d);
                window.MouseDown(start,Avalonia.Input.MouseButton.Left);window.MouseMove(start-new Vector(0,30));window.MouseUp(start-new Vector(0,30),Avalonia.Input.MouseButton.Left);Dispatcher.UIThread.RunJobs();
                Check(vm.Selected!.Points[0][1]!=50,"native pointer drag updates rule");
                Check(!view.GetVisualDescendants().OfType<Button>().Any(b=>b.Content is string s&&(s=="＋ 新建预设"||s=="管理")),"removed preset actions absent");
                var combo=view.GetVisualDescendants().OfType<ComboBox>().First(c=>c.ItemsSource is System.Collections.Generic.List<PlayCardPreset>);combo.SelectedItem=vm.Document.Presets[2];Dispatcher.UIThread.RunJobs();
                Check(vm.Preset.Name=="干劲","preset selector wired");Click("保存规则");Check(!vm.Dirty,"save button reaches disk");
            }
            Click("＋ 添加节点");Check(vm.Selected!.Points.Count>=3,"wired add-node button");
            Click("配置手牌");Shot("hand");
            var pageScroll=view.GetVisualDescendants().OfType<ScrollViewer>().First();
            pageScroll.Offset=new Vector(0,pageScroll.Extent.Height);Dispatcher.UIThread.RunJobs();Shot("hand-bottom");
            pageScroll.Offset=default;Dispatcher.UIThread.RunJobs();
            view.OpenPicker(0);Dispatcher.UIThread.RunJobs();Shot("picker");
            Check(view.GetVisualDescendants().OfType<TextBlock>().Any(t=>t.Text?.StartsWith("当前预设已有规则")==true),"quick rules native overlay");
            Click("×");
            // Detach/reattach must retain unsaved model and work without external hosts.
            window.Content=null;window.Content=view;Dispatcher.UIThread.RunJobs();Check(vm.Dirty,"unsaved edits survive navigation");
            window.Close();
        }
        foreach(var language in new[]{"zh-CN","en-US","ja-JP","zh-Hant"})
        {
            LanguageHelper.ChangeLanguage(language);
            var text=ExText.T("剩余 7 回合");
            Check(language=="zh-CN" || text!="剩余 7 回合","dynamic translation "+language);
            Check(ExText.T("届いて！")=="届いて！","card name stays Japanese "+language);
            var view=new CardRulesView(vm);var window=new Window{Width=1280,Height=1100,Content=view};window.Show();Dispatcher.UIThread.RunJobs();
            view.OpenPicker(0);Dispatcher.UIThread.RunJobs();view.Measure(new Size(1280,1100));view.Arrange(new Rect(0,0,1280,1100));Dispatcher.UIThread.RunJobs();
            var cardButtons=view.GetVisualDescendants().OfType<Button>().Where(b=>b.MinHeight==84).ToArray();
            Check(cardButtons.Length==446,"all picker cards visible "+language);
            Check(cardButtons.All(b=>b.Bounds.Height>=84),"card tiles do not clip "+language);
            using var bitmap=new RenderTargetBitmap(new PixelSize(1280,1100));bitmap.Render(view);bitmap.Save(Path.Combine(output,$"picker-{language}.png"));window.Close();
        }
        LanguageHelper.ChangeLanguage("zh-CN");
        File.WriteAllText(vm.ConfigPath,"broken");using(var invalid=new CardRulesViewModel(Root)){Check(!invalid.Loaded&&!invalid.Save()&&File.ReadAllText(vm.ConfigPath)=="broken","corrupt file protected");}
        File.WriteAllText(vm.ConfigPath,JsonConvert.SerializeObject(baseline,PlayCardPolicy.Json));
        Console.WriteLine($"NATIVE_RULES_PASSED {count} checks; {vectors.Count} policy vectors");
    }
}
public sealed class RulesTestApp : Application
{
    public override void Initialize() { Styles.Add(new FluentTheme());Styles.Add(new SukiTheme { ThemeColor=SukiUI.Enums.SukiColor.Blue }); }
    public override void OnFrameworkInitializationCompleted()
    {
        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            var tabs = new TabControl();
            tabs.Items.Add(new TabItem { Header = "出牌规则", Content = new CardRulesView(Program.Root) });
            tabs.Items.Add(new TabItem { Header = "切页验证", Content = new TextBlock { Text = "返回规则页验证未保存修改保留" } });
            desktop.MainWindow = new Window { Title = "Maa 出牌规则 · 隔离验证", Width = 1360, Height = 1000, Content = tabs };
        }
        base.OnFrameworkInitializationCompleted();
    }
}
