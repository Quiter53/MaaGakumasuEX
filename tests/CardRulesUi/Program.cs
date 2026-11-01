using MFAAvalonia.Controls.Liquid;
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
    private static bool RulesOnly;
    [STAThread] public static void Main(string[] args)
    {
        // Usage: CardRulesUi [<fixtureRoot> <outputDir>] [--rules-only|--controller-only|--outcome-only|--combo-only|--interactive]
        // Flags never stand in for paths: an earlier positional lookup turned "--rules-only" into an output folder.
        var known=new[]{"--rules-only","--controller-only","--outcome-only","--combo-only","--interactive"};
        var unknown=args.Where(a=>a.StartsWith("--")&&!known.Contains(a)).ToArray();
        if(unknown.Length>0){Console.Error.WriteLine("unknown option(s): "+string.Join(", ",unknown));Environment.Exit(2);}
        var positional=args.Where(a=>!a.StartsWith("--")).ToArray();
        RulesOnly = args.Contains("--rules-only");
        Root = Path.GetFullPath(positional.Length>0?positional[0]:File.ReadAllText(Path.Combine(AppContext.BaseDirectory,"fixture-root.txt")).Trim());
        string Output(){if(positional.Length<2){Console.Error.WriteLine("an output directory is required: CardRulesUi <fixtureRoot> <outputDir> [options]");Environment.Exit(2);}return Path.GetFullPath(positional[1]);}
        if(args.Length==0||args.Contains("--interactive"))AppBuilder.Configure<RulesTestApp>().UsePlatformDetect().StartWithClassicDesktopLifetime(args);
        else if (args.Contains("--controller-only"))
        {
            var output=Output();Directory.CreateDirectory(output);
            AppBuilder.Configure<RulesTestApp>().UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing=false }).UseSkia().SetupWithoutStarting();
            int checks=0;
            ControllerSettingsChecks.Run(Root,output,(ok,name)=>{if(!ok)throw new Exception(name);checks++;Console.WriteLine("PASS "+name);});
            Console.WriteLine($"CONTROLLER_SETTINGS_PASSED {checks} checks");
        }
        else if (args.Contains("--outcome-only"))
        {
            int checks=0;
            TaskOutcomeChecks.Run((ok,name)=>{if(!ok)throw new Exception(name);checks++;Console.WriteLine("PASS "+name);});
            Console.WriteLine($"TASK_OUTCOME_PASSED {checks} checks");
        }
        else if(args.Contains("--combo-only"))
        {
            var output=Output();Directory.CreateDirectory(output);
            AppBuilder.Configure<RulesTestApp>().UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing=false }).UseSkia().SetupWithoutStarting();
            using var vm=new CardRulesViewModel(Root);int checks=0;
            ComboChecks.Run(vm,output,(ok,name)=>{if(!ok)throw new Exception(name);checks++;Console.WriteLine("PASS "+name);});
            Console.WriteLine($"NATIONAL_UI_PASSED {checks} checks");
        }
        else RunTests(Output());
    }
    private static void RunTests(string output)
    {
        Directory.CreateDirectory(output);
        AppBuilder.Configure<RulesTestApp>().UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing=false }).UseSkia().SetupWithoutStarting();
        int count=0;void Check(bool ok,string name){if(!ok)throw new Exception(name);count++;Console.WriteLine("PASS "+name);}
        var selectAsset=typeof(VersionChecker).GetMethod("SelectGitHubReleaseAsset",System.Reflection.BindingFlags.Static|System.Reflection.BindingFlags.NonPublic)!;
        var release=JObject.Parse("""{"assets":[{"name":"MaaGakumasuEX-win-x64.zip.sha256","state":"uploaded","browser_download_url":"checksum"},{"name":"MaaGakumasuEX-win-x64.zip","state":"starter","browser_download_url":"unfinished"},{"name":"MaaGakumasuEX-win-x64.zip","state":"uploaded","browser_download_url":"package","digest":"sha256:abc"}]}""");
        var selectedAsset=((string,string))selectAsset.Invoke(null,new object[]{release})!;
        Check(selectedAsset==("package","abc"),"release selection uses package digest and ignores checksum/incomplete assets");
        // GitHub rate limiting: only 403/429 with exhausted quota or Retry-After counts; the reset time is honoured.
        {
            var limited=typeof(VersionChecker).GetMethod("IsGitHubRateLimited",System.Reflection.BindingFlags.Static|System.Reflection.BindingFlags.NonPublic)!;
            var reset=typeof(VersionChecker).GetMethod("GitHubRateLimitReset",System.Reflection.BindingFlags.Static|System.Reflection.BindingFlags.NonPublic)!;
            System.Net.Http.HttpResponseMessage R(int code,string? remaining=null,string? resetEpoch=null,int? retryAfter=null)
            {
                var r=new System.Net.Http.HttpResponseMessage((System.Net.HttpStatusCode)code){ReasonPhrase="Forbidden"};
                if(remaining!=null)r.Headers.Add("X-RateLimit-Remaining",remaining);
                if(resetEpoch!=null)r.Headers.Add("X-RateLimit-Reset",resetEpoch);
                if(retryAfter!=null)r.Headers.RetryAfter=new System.Net.Http.Headers.RetryConditionHeaderValue(TimeSpan.FromSeconds(retryAfter.Value));
                return r;
            }
            bool L(System.Net.Http.HttpResponseMessage r)=>(bool)limited.Invoke(null,new object[]{r})!;
            Check(L(R(403,"0")),"403 with exhausted quota is a rate limit (ReasonPhrase is only 'Forbidden')");
            Check(L(R(429,retryAfter:60)),"429 with Retry-After is a rate limit");
            Check(!L(R(403,"42")),"403 with remaining quota is an ordinary failure");
            Check(!L(R(404)),"404 is not a rate limit");
            var epoch=DateTimeOffset.UtcNow.AddMinutes(17).ToUnixTimeSeconds();
            Check(((DateTimeOffset)reset.Invoke(null,new object[]{R(403,"0",epoch.ToString())})!).ToUnixTimeSeconds()==epoch,"rate limit reset read from X-RateLimit-Reset");
            var ra=(DateTimeOffset)reset.Invoke(null,new object[]{R(429,retryAfter:120)})!;
            Check(Math.Abs((ra-DateTimeOffset.UtcNow).TotalSeconds-120)<5,"rate limit reset read from Retry-After");
        }
        using var vm=new CardRulesViewModel(Root);Check(vm.Loaded,vm.Status);Check(vm.Cards.Count==456,"456 cards loaded including conversion additions");
        Check(vm.Document.Presets.Count==6&&vm.Preset.Name=="干劲","six builtin presets");
        var baseline=PlayCardPolicy.Clone(vm.Document);
        var rule=vm.Selected!;var initialMax=rule.Max;var initialRuleCount=vm.Preset.Rules.Count;Check(rule.Card=="届いて！"&&rule.Max<=2,"Japanese default rule");
        Check(!rule.SingleCopy,"existing rules default single copy off");
        Check(!rule.CustomExtraAction,"existing rules default custom extra off");
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
        var plainSsr=vm.Cards.First(c=>c.Rarity=="SSR"&&!c.Extra&&!vm.Preset.Rules.Any(r=>r.Card==c.Name));
        vm.Preset.Hand=[plainSsr.Name,sr.Name];vm.Selected.Tier=null;vm.Selected.CustomExtraAction=true;
        Check(vm.Preview()[0].Card.Name==sr.Name&&vm.Preview()[0].Extra&&!sr.Extra,"custom extra changes preview without editing catalog");
        vm.Selected.CustomExtraAction=false;Check(vm.Preview()[0].Card.Name==plainSsr.Name,"custom extra off restores preview");
        foreach(var c in vm.Cards)
        {
            var rare=Array.IndexOf(new[]{"","LR","SSR","SR","R","N"},c.Rarity);var origin=Array.IndexOf(new[]{"","none","idol","support"},c.Origin);
            Check(vm.Filter(c.Name,rare,c.PlanType,c.Category,origin,c.IsConversion?2:1,c.Extra?1:2,0,0).Any(x=>x.Name==c.Name),"combined filters "+c.Name);
        }
        // Values exported from the actual native policy are compared to Python.
        var vectors=new JArray();
        foreach(string rarity in PlayCardPolicy.Tiers)foreach(string? tier in PlayCardPolicy.Tiers.Cast<string?>().Append(null))foreach(bool enabled in new[]{true,false})foreach(string mode in new[]{"linear","step"})for(int turn=1;turn<=30;turn++)
        {
            var r=new PlayCardRule{Card="届いて！",Enabled=enabled,CustomExtraAction=turn%3==0,Limited=true,Min=1,Max=2,Tier=tier,Mode=mode,Points=[[29,17],[13,96],[1,3]]};
            vectors.Add(new JObject{["rarity"]=rarity,["extra"]=turn%2==0,["effectiveExtra"]=PlayCardPolicy.EffectiveExtraAction(turn%2==0,r),["rule"]=JObject.FromObject(r,JsonSerializer.Create(PlayCardPolicy.Json)),["turn"]=turn,["result"]=new JArray(PlayCardPolicy.Allowed(r,turn),PlayCardPolicy.EffectiveTier(rarity,r),PlayCardPolicy.Priority(r,turn))});
        }
        File.WriteAllText(Path.Combine(output,"native-policy-vectors.json"),vectors.ToString());
        ComboChecks.Run(vm,output,Check);
        // Restore isolated test config before rendering the approved defaults.
        File.WriteAllText(vm.ConfigPath,JsonConvert.SerializeObject(baseline,PlayCardPolicy.Json));vm.Load();
        foreach(var theme in new[]{ThemeVariant.Light,ThemeVariant.Dark})foreach(var width in new[]{1120,1500})
        {
            Application.Current!.RequestedThemeVariant=theme;
            var view=new CardRulesView(vm);var window=new Window{Width=width,Height=1000,Content=view};window.Show();Dispatcher.UIThread.RunJobs();
            void Shot(string name){Dispatcher.UIThread.RunJobs();using var bitmap=new RenderTargetBitmap(new PixelSize(width,1000));bitmap.Render(window);bitmap.Save(Path.Combine(output,$"{name}-{width}-{theme}.png"));}
            T Named<T>(string name) where T:Control=>window.GetVisualDescendants().OfType<T>().First(c=>c.IsEffectivelyVisible&&Avalonia.Automation.AutomationProperties.GetName(c)==ExText.T(name));
            void Click(Button b){b.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));Dispatcher.UIThread.RunJobs();}
            void ClickText(string label)=>Click(window.GetVisualDescendants().OfType<Button>().First(b=>b.IsEffectivelyEnabled&&b.IsEffectivelyVisible&&(b.Content as string==ExText.T(label)||Avalonia.Automation.AutomationProperties.GetName(b)==ExText.T(label))));
            Shot("native");
            if(width==1120&&theme==ThemeVariant.Light)
            {
                // Pointer drag on the first node uses the same transform as drawing.
                var curve=window.GetVisualDescendants().OfType<CardRuleCurve>().First(c=>!c.Mini);
                var node=vm.Selected!.Points.OrderByDescending(p=>p[0]).First();var total=PlayCardPolicy.Total(vm.Selected);
                double left=40,right=curve.Bounds.Width-22,top=26,bottom=curve.Bounds.Height-26;
                var local=new Point(left+(total-node[0])/(double)(total-1)*(right-left),bottom-node[1]/100d*(bottom-top));
                var start=curve.TranslatePoint(local,window)!.Value;var before=node[1];
                window.MouseDown(start,Avalonia.Input.MouseButton.Left);window.MouseMove(start-new Vector(0,30));window.MouseUp(start-new Vector(0,30),Avalonia.Input.MouseButton.Left);Dispatcher.UIThread.RunJobs();
                Check(node[1]>before,"native pointer drag updates the same node");
                Check(vm.Selected.Points.Contains(node),"dragged node keeps identity");
                var presets=Named<SegmentedControl>("职业预设");
                presets.SelectedItem=presets.Items.OfType<ListBoxItem>().First(i=>i.Tag==vm.Document.Presets[2]);Dispatcher.UIThread.RunJobs();
                Check(vm.Preset.Name=="干劲","preset selector wired");Click(Named<Button>("保存"));Check(!vm.Dirty,"save button reaches disk");
                var single=Named<ToggleSwitch>("最多保留一张");
                single.IsChecked=true;Dispatcher.UIThread.RunJobs();Check(vm.Selected!.SingleCopy&&vm.Dirty,"single-copy toggle updates rule");
                Click(Named<Button>("保存"));
                using(var check=new CardRulesViewModel(Root))Check(check.Selected!.SingleCopy,"single-copy choice survives native reload");
                single.IsChecked=false;Dispatcher.UIThread.RunJobs();Click(Named<Button>("保存"));
                var extra=Named<ToggleSwitch>("自定义再动");
                extra.IsChecked=true;Dispatcher.UIThread.RunJobs();Check(vm.Selected!.CustomExtraAction&&vm.Dirty,"custom extra toggle updates rule");
                Click(Named<Button>("保存"));using(var check=new CardRulesViewModel(Root))Check(check.Selected!.CustomExtraAction,"custom extra survives native reload");
                extra.IsChecked=false;Dispatcher.UIThread.RunJobs();Click(Named<Button>("保存"));
                // Hand simulation slider stays the same instance while dragging values.
                var slider=Named<Slider>("模拟剩余回合");
                foreach(var t in new[]{1,3,6,9,12}){slider.Value=t;Dispatcher.UIThread.RunJobs();}
                Check(ReferenceEquals(slider,Named<Slider>("模拟剩余回合"))&&vm.PreviewTurn==12,"hand slider reused while moving");
                Check(slider.Maximum==PlayCardPolicy.Total(vm.Selected),"hand range follows total turns");
                // Total turns shrink clamps nodes and the preview range.
                vm.SetTotalTurns(vm.Selected,8);Check(vm.Selected.Points.All(p=>p[0]<=8)&&vm.Selected.TotalTurns==8,"total turns clamps nodes");
                vm.SetTotalTurns(vm.Selected,12);
            }
            window.GetVisualDescendants().OfType<Expander>().First().IsExpanded=true;Dispatcher.UIThread.RunJobs();
            var nodes=vm.Selected!.Points.Count;ClickText("＋ 添加节点");Check(vm.Selected!.Points.Count==Math.Min(30,nodes+1),"wired add-node button");
            Shot("hand");
            view.OpenPicker(hand:true);Dispatcher.UIThread.RunJobs();Shot("picker");
            Check(window.GetVisualDescendants().OfType<TextBlock>().Any(t=>t.Text?.StartsWith(ExText.T("当前预设"))==true),"current preset cards listed first");
            Button[] Tiles()=>window.GetVisualDescendants().OfType<Button>().Where(b=>b.Classes.Contains("lg-picker-card")&&b.IsEffectivelyVisible).ToArray();
            var pickerTiles=Tiles();
            var pick=pickerTiles[0];pick.Focus();Click(pick);
            Check(pickerTiles.SequenceEqual(Tiles()),"selection preserves all picker control instances");
            Check(pick.IsFocused&&pick.Classes.Contains("selected"),"selection preserves keyboard focus and marks tile");
            var pickerSearch=window.GetVisualDescendants().OfType<TextBox>().First(t=>t.Watermark?.ToString()==ExText.T("搜索日文卡牌名称…"));
            pickerSearch.Text=vm.Cards[0].Name;Dispatcher.UIThread.RunJobs();Dispatcher.UIThread.RunJobs(DispatcherPriority.Background);
            Check(Tiles().Length<pickerTiles.Length,"search filters picker membership");
            pickerSearch.Text="";Dispatcher.UIThread.RunJobs();Dispatcher.UIThread.RunJobs(DispatcherPriority.Background);
            Check(pickerTiles.SequenceEqual(Tiles()),"clearing search reuses original picker controls");
            window.KeyPress(Avalonia.Input.Key.Escape,Avalonia.Input.RawInputModifiers.None,Avalonia.Input.PhysicalKey.Escape,null);Dispatcher.UIThread.RunJobs();
            Check(!window.GetVisualDescendants().OfType<Button>().Any(b=>b.Classes.Contains("lg-picker-card")&&b.IsHitTestVisible&&b.IsEffectivelyVisible&&b.GetVisualAncestors().OfType<Control>().All(a=>a.IsHitTestVisible)),"escape closes picker");
            // Detach/reattach must retain unsaved model and work without external hosts.
            window.Content=null;window.Content=view;Dispatcher.UIThread.RunJobs();Check(vm.Dirty,"unsaved edits survive navigation");
            window.Close();
        }
        foreach(var language in new[]{"zh-CN","en-US","ja-JP","zh-Hant"})
        {
            LanguageHelper.ChangeLanguage(language);
            vm.AddRule(vm.Cards.First(c=>c.Rarity=="R").Name);
            var text=ExText.T("剩余 7 回合");
            Check(language=="zh-CN" || text!="剩余 7 回合","dynamic translation "+language);
            Check(ExText.T("届いて！")=="届いて！","card name stays Japanese "+language);
            var view=new CardRulesView(vm);var window=new Window{Width=1280,Height=1000,Content=view};window.Show();Dispatcher.UIThread.RunJobs();
            using(var promoted=new RenderTargetBitmap(new PixelSize(1280,1000))){promoted.Render(window);promoted.Save(Path.Combine(output,$"promotion-{language}.png"));}
            Check(window.GetVisualDescendants().OfType<ToggleSwitch>().Any(t=>Avalonia.Automation.AutomationProperties.GetName(t)==ExText.T("自定义再动")),"custom extra label translated "+language);
            var promotionText=ExText.T("提升档位应用于整个培育的选牌判断；停用规则后恢复。回合限制与曲线仅用于出牌。");
            Check(window.GetVisualDescendants().OfType<TextBlock>().Any(t=>t.Text==promotionText),"global promotion description "+language);
            Check(language=="zh-CN"||!promotionText.StartsWith("提升档位"),"promotion description translated "+language);
            view.OpenPicker();Dispatcher.UIThread.RunJobs();
            var libraryCount=window.GetVisualDescendants().OfType<TextBlock>().First(t=>t.Text?.Contains($"{vm.Cards.Count} / {vm.Cards.Count}")==true||t.Text?.Contains($"{vm.Cards.Count}")==true&&t.Text.Contains("/"));
            Check(libraryCount!=null,"picker reports full library "+language);
            var cardButtons=window.GetVisualDescendants().OfType<Button>().Where(b=>b.Classes.Contains("lg-picker-card")&&b.IsEffectivelyVisible).ToArray();
            Check(cardButtons.Length==Math.Min(60,vm.Cards.Count),"picker pages the library "+language);
            Check(cardButtons.All(b=>b.Bounds.Height>=84),"card tiles do not clip "+language);
            using var bitmap=new RenderTargetBitmap(new PixelSize(1280,1000));bitmap.Render(window);bitmap.Save(Path.Combine(output,$"picker-{language}.png"));window.Close();
        }
        LanguageHelper.ChangeLanguage("zh-CN");
        { var custom = new MFAAvalonia.Views.UserControls.CustomizationTargetDialog(Root, "[]");
            Check(custom.Filter().Count() == 247, "customization catalog only includes supported cards");
            Check(custom.AddCard("幸せのおまじない") && custom.AddCard("仕切り直し"), "customization targets can be added");
            Check(!custom.AddCard("幸せのおまじない"), "duplicate customization target rejected");
            custom.Targets[0].Counts = [2, 0, 1];
            custom.Targets[1].Counts = [0, 1];
            Check(custom.Move(1, -1) && custom.Targets[0].Name == "仕切り直し", "customization order changed");
            Check(custom.AddCard("輝くキミへ") && custom.MoveTo(0,2), "customization insert move over multiple cards");
            Check(string.Join(",",custom.Targets.Select(t=>t.Name))=="幸せのおまじない,輝くキミへ,仕切り直し", "drag destination uses insertion preserving intervening order");
            var saved = custom.Serialize();
            var reopened = new MFAAvalonia.Views.UserControls.CustomizationTargetDialog(Root, saved);
            Check(reopened.Serialize() == saved, "customization ordered counts roundtrip");
            custom.Targets[2].Counts = [1, 1];
            bool rejected = false; try { custom.Serialize(); } catch (InvalidDataException) { rejected = true; }
            Check(rejected, "customization total limit checked independent of option limits");
        }
        foreach(var theme in new[]{ThemeVariant.Light, ThemeVariant.Dark}) foreach(var width in new[]{680,920})
        {
            Application.Current!.RequestedThemeVariant = theme;
            var custom = new MFAAvalonia.Views.UserControls.CustomizationTargetDialog(Root,"[]");
            custom.AddCard("幸せのおまじない"); custom.Targets[0].Counts=[2,0,1]; custom.AddCard("仕切り直し");
            var host=new Window{Width=width,Height=760,Content=custom};host.Show();Dispatcher.UIThread.RunJobs();custom.ShowTargets();Dispatcher.UIThread.RunJobs();
            void CustomShot(string name)
            {
                Dispatcher.UIThread.RunJobs();
                using var bitmap=new RenderTargetBitmap(new PixelSize(width,760));bitmap.Render(host);
                bitmap.Save(Path.Combine(output,$"customization-{name}-{width}-{theme}.png"));
            }
            CustomShot("targets");
            var counts=custom.GetVisualDescendants().OfType<NumericUpDown>().ToArray();
            Check(counts.Length==5,"customization two and three option menus "+width+theme);
            counts[0].Value=1;Check(custom.Targets[0].Counts[0]==1,"native customization count changes model "+width+theme);
            var handles=custom.GetVisualDescendants().OfType<Border>().Where(b=>b.Name=="CustomizationDragHandle").ToArray();
            Check(handles.Length==2,"native drag handles available "+width+theme);
            var first=handles[0].TranslatePoint(new Point(20,10),host)!.Value;
            var last=handles[1].TranslatePoint(new Point(20,60),host)!.Value;
            host.MouseDown(first,Avalonia.Input.MouseButton.Left);
            host.MouseMove(last);host.MouseUp(last,Avalonia.Input.MouseButton.Left);Dispatcher.UIThread.RunJobs();
            Check(custom.Targets[0].Name=="仕切り直し"&&custom.Targets[1].Counts.SequenceEqual(new[]{1,0,1}),"native drag reorders without changing option counts "+width+theme);
            var reordered=custom.Serialize();
            var reloaded=new MFAAvalonia.Views.UserControls.CustomizationTargetDialog(Root,reordered);
            Check(reloaded.Serialize()==reordered,"native dragged order survives reload "+width+theme);
            CustomShot("reordered");
            Check(custom.PickerCandidates().Count==247,"customization picker has all supported cards "+width+theme);
            string? result="unset";custom.Finished+=r=>result=r;
            custom.GetVisualDescendants().OfType<Button>().First(b=>b.Content as string==ExText.T("取消")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Check(result==null,"cancel returns no result (draft discarded) "+width+theme);
            host.Close();
        }
        foreach(var language in new[]{"en-US","ja-JP","zh-Hant"})
        {
            LanguageHelper.ChangeLanguage(language);
            Check(ExText.T("自定义卡牌设置")!="自定义卡牌设置","customization title translated "+language);
            Check(ExText.T("选项 2")!="选项 2","customization option label translated "+language);
        }
        LanguageHelper.ChangeLanguage("zh-CN");
        if (!RulesOnly)
        {
            ControllerSettingsChecks.Run(Root, output, Check);
            TaskOutcomeChecks.Run(Check);
        }
        File.WriteAllText(vm.ConfigPath,"broken");using(var invalid=new CardRulesViewModel(Root)){Check(!invalid.Loaded&&!invalid.Save()&&File.ReadAllText(vm.ConfigPath)=="broken","corrupt file protected");}
        File.WriteAllText(vm.ConfigPath,JsonConvert.SerializeObject(baseline,PlayCardPolicy.Json));
        Console.WriteLine($"NATIVE_RULES_PASSED {count} checks; {vectors.Count} policy vectors; rulesOnly={RulesOnly}");
    }
}
public sealed class RulesTestApp : Application
{
    public override void Initialize() { Styles.Add(new FluentTheme());Styles.Add(new SukiTheme { ThemeColor=SukiUI.Enums.SukiColor.Blue }); MFAAvalonia.Controls.Liquid.LiquidTheme.Install(this); }
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
