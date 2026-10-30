using Avalonia;
using Avalonia.Controls;
using Avalonia.Headless;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Styling;
using Avalonia.Themes.Fluent;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.Views.Pages;
using Newtonsoft.Json.Linq;
using SukiUI;

var portable = Path.GetFullPath(args[0]);
var fixture = Path.Combine(AppContext.BaseDirectory, "config");
Directory.CreateDirectory(fixture);
var config = Path.Combine(fixture, "card_lists.json");
var existing=JObject.Parse(File.ReadAllText(Path.Combine(portable, "config/card_lists.json")));
// A legacy fixture exercises migration without ever editing the user's presets.
var legacy=new JObject {
    ["blacklist"]=existing["blacklist"]?.DeepClone(),
    ["whitelist"]=existing["whitelist"]?.DeepClone(),
    ["debugDiagnosticsEnabled"]=existing["debugDiagnosticsEnabled"]?.DeepClone()
};
File.WriteAllText(config, legacy.ToString());
File.Copy(Path.Combine(portable, "config/card_library.json"), Path.Combine(fixture, "card_library.json"), true);
var library=JObject.Parse(File.ReadAllText(Path.Combine(fixture,"card_library.json")));
foreach(var token in library["cards"]!)
{
    var relative=(string)token["image"]!;
    var source=Path.Combine(portable,relative);
    var target=Path.Combine(AppContext.BaseDirectory,relative);
    Directory.CreateDirectory(Path.GetDirectoryName(target)!);
    if(File.Exists(source)) File.Copy(source,target,true);
}

AppBuilder.Configure<TestApp>().UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing=false })
    .UseSkia().SetupWithoutStarting();
var vm=new CardListSettingsViewModel();
void Assert(bool pass,string message) { if(!pass) throw new Exception(message); Console.WriteLine("PASS "+message); }
JObject Saved()=>JObject.Parse(File.ReadAllText(config));
Assert(vm.PresetOptions.SequenceEqual(new[]{"好调","集中","干劲","好印象","强气","全力"}),"six preset names in requested order");
var allVisible=vm.VisibleCards.ToArray();
int resets=0;vm.VisibleCards.CollectionChanged+=(_,e)=>{if(e.Action==System.Collections.Specialized.NotifyCollectionChangedAction.Reset)resets++;};
vm.SearchText=allVisible[0].Name;
Assert(vm.VisibleCards.Count<allVisible.Length && vm.VisibleCards.All(c=>allVisible.Contains(c)),"filter keeps original card objects");
vm.SearchText="";
Assert(allVisible.SequenceEqual(vm.VisibleCards) && resets==0,"filter clear restores order without collection reset");
var originalBlack=vm.BlacklistCards.Select(c=>c.Name).ToArray();
var originalWhite=vm.WhitelistCards.Select(c=>c.Name).ToArray();
Assert(originalBlack.SequenceEqual(legacy["blacklist"]!["cards"]!.Values<string>()) &&
    originalWhite.SequenceEqual(legacy["whitelist"]!["cards"]!.Values<string>()),
    "legacy list migration preserves current contents");
var a=vm.VisibleCards[0]; var b=vm.VisibleCards[1];
vm.SelectedPreset="干劲";
Assert(vm.BlacklistCards.Count==0 && vm.WhitelistCards.Count==0 && vm.ReplacementCards.Count==0,"new preset starts independently");
vm.AddToWhitelist(a); vm.AddToReplacement(a); vm.ReplacementEnabled=true;
Assert(vm.WhitelistCards.Contains(a) && vm.ReplacementCards.Contains(a),"replacement may overlap whitelist");
vm.AddToBlacklist(b); vm.BlacklistEnabled=true;
Assert((string?)Saved()["activePreset"]=="干劲" && (bool?)Saved()["replacementList"]?["enabled"]==true,"edits and active snapshot save immediately");
vm.SelectedPreset="全力"; vm.AddToWhitelist(b); vm.WhitelistEnabled=true;
vm.SelectedPreset="干劲";
Assert(vm.ReplacementEnabled && vm.BlacklistEnabled && vm.ReplacementCards.Contains(a) && vm.BlacklistCards.Contains(b),"switch back restores lists and enabled state");
vm.AddToBlacklist(a);
Assert(!vm.WhitelistCards.Contains(a) && vm.ReplacementCards.Contains(a),"black-white exclusion does not erase independent replacement");
vm.RemoveFromReplacement(a);
Assert(vm.ReplacementCards.Count==0 && ((JArray)Saved()["presets"]!["干劲"]!["replacementList"]!["cards"]!).Count==0,"remove saves immediately");
vm.SelectedPreset="好调";
Assert(originalBlack.SequenceEqual(vm.BlacklistCards.Select(c=>c.Name)) && originalWhite.SequenceEqual(vm.WhitelistCards.Select(c=>c.Name)),"original preset remains unchanged");
var resumed=new CardListSettingsViewModel();
Assert(resumed.SelectedPreset=="好调","last selected preset survives recreation");
resumed.SelectedPreset="全力";
Assert(resumed.WhitelistCards.Single().Name==b.Name && resumed.WhitelistEnabled,"other preset survives recreation");
resumed.SelectedPreset="好调";
var view=new CardListSettingsView { DataContext=resumed };
var surface=new Border { Child=view };
var window=new Window { Width=1120, Height=820, Content=surface };
window.Show();
Dispatcher.UIThread.RunJobs();
view.Measure(new Size(1120,820)); view.Arrange(new Rect(0,0,1120,820));
Dispatcher.UIThread.RunJobs();
Assert(!view.GetVisualDescendants().OfType<ToggleSwitch>().Any(t=>t.Content?.ToString()=="只看再动"),"extra-only switch absent");
Assert(view.GetVisualDescendants().OfType<ToggleSwitch>().Count()==3,"three independent list switches");
var selector=view.GetVisualDescendants().OfType<ComboBox>()
    .Single(c=>ReferenceEquals(c.ItemsSource,resumed.PresetOptions));
selector.SelectedItem="全力"; Dispatcher.UIThread.RunJobs();
Assert(resumed.SelectedPreset=="全力" && (string?)Saved()["activePreset"]=="全力","preset selector binding saves immediately");
selector.SelectedItem="好调"; Dispatcher.UIThread.RunJobs();
foreach(var size in new[]{new Size(1120,820),new Size(800,600)})
{
    window.Width=size.Width; window.Height=size.Height;
    surface.Measure(size); surface.Arrange(new Rect(size)); Dispatcher.UIThread.RunJobs();
    foreach(var theme in new[]{ThemeVariant.Light,ThemeVariant.Dark})
    {
        Application.Current!.RequestedThemeVariant=theme; Dispatcher.UIThread.RunJobs();
        // Same inherited text/background resources as the app's SukiWindow.
        surface.Background=new SolidColorBrush(theme==ThemeVariant.Light ? Color.Parse("#F1F2F6") : Color.Parse("#17171C"));
        view.Foreground=new SolidColorBrush(theme==ThemeVariant.Light ? Color.Parse("#222222") : Color.Parse("#EDFFFFFF"));
        Dispatcher.UIThread.RunJobs();
        using var bitmap=new RenderTargetBitmap(new PixelSize((int)size.Width,(int)size.Height));
        bitmap.Render(surface); bitmap.Save(Path.Combine(AppContext.BaseDirectory,$"card-lists-{size.Width}-{theme}.png"));
    }
}
window.Close();
var savedBefore=File.ReadAllText(config);
File.WriteAllText(config,"{ invalid JSON");
var invalid=new CardListSettingsViewModel();
Assert(File.ReadAllText(config)=="{ invalid JSON" && invalid.StatusText.Contains("载入失败"),
    "invalid settings remain recoverable without being overwritten");
File.WriteAllText(config,savedBefore);
// Existing five-preset users retain all edited lists when 感性 becomes 好调.
var fivePresets=JObject.Parse(savedBefore);
var beforeRename=fivePresets["presets"]!["好调"]!.DeepClone();
fivePresets["presets"]!["感性"]=beforeRename.DeepClone();
((JObject)fivePresets["presets"]!).Remove("好调");
((JObject)fivePresets["presets"]!).Remove("集中");
fivePresets["activePreset"]="感性";
File.WriteAllText(config,fivePresets.ToString());
var renamed=new CardListSettingsViewModel();
Assert(renamed.SelectedPreset=="好调" &&
    JToken.DeepEquals(beforeRename,Saved()["presets"]!["好调"]) &&
    Saved()["presets"]!["感性"]==null,"rename preserves lists and enabled states");
Assert(JToken.DeepEquals(fivePresets["presets"]!["干劲"],Saved()["presets"]!["干劲"]) &&
    JToken.DeepEquals(fivePresets["presets"]!["全力"],Saved()["presets"]!["全力"]),
    "rename preserves other edited presets");
renamed.SelectedPreset="集中";
Assert(renamed.BlacklistCards.Count==0 && renamed.WhitelistCards.Count==0 &&
    renamed.ReplacementCards.Count==0 && !renamed.BlacklistEnabled &&
    !renamed.WhitelistEnabled && !renamed.ReplacementEnabled,"focus preset starts empty and disabled");
renamed.AddToReplacement(a); renamed.ReplacementEnabled=true;
renamed.SelectedPreset="好调";
renamed.SelectedPreset="集中";
Assert(renamed.ReplacementEnabled && renamed.ReplacementCards.Single().Name==a.Name,
    "focus preset edits save independently");
renamed.SelectedPreset="好调";
Assert(JToken.DeepEquals(beforeRename,Saved()["presets"]!["好调"]),"renamed preset remains unchanged");
Console.WriteLine("CARD_LIST_UI_TESTS_PASSED");

class TestApp:Application
{
    public override void Initialize()
    {
        Styles.Add(new FluentTheme());
        Styles.Add(new SukiTheme());
        RequestedThemeVariant=ThemeVariant.Light;
    }
}
