using Avalonia;
using Avalonia.Controls;
using Avalonia.Headless;
using Avalonia.Media.Imaging;
using Avalonia.Media;
using Avalonia.Styling;
using Avalonia.Themes.Fluent;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.Views.Pages;
using Newtonsoft.Json.Linq;
using SukiUI;

var repo=Path.GetFullPath(args[0]);
var root=Path.Combine(AppContext.BaseDirectory,"fixture");
Directory.CreateDirectory(Path.Combine(root,"config"));
Directory.CreateDirectory(Path.Combine(root,"agent/custom"));
File.Copy(Path.Combine(repo,"agent/custom/drink_catalog.json"),Path.Combine(root,"agent/custom/drink_catalog.json"),true);
var portable=Path.GetFullPath(args[1]);
File.Copy(Path.Combine(portable,"config/card_library.json"),Path.Combine(root,"config/card_library.json"),true);
var library=JObject.Parse(File.ReadAllText(Path.Combine(root,"config/card_library.json")));
foreach(var card in library["cards"]!) Copy((string)card["image"]!);
foreach(var drink in JArray.Parse(File.ReadAllText(Path.Combine(root,"agent/custom/drink_catalog.json")))) Copy($"assets/resource/base/image/produce/drinks/{drink["id"]}.png");
void Copy(string relative) { var dest=Path.Combine(root,relative); Directory.CreateDirectory(Path.GetDirectoryName(dest)!); var src=Path.Combine(repo,relative); if(!File.Exists(src)) src=Path.Combine(portable,relative); File.Copy(src,dest,true); }
File.WriteAllText(Path.Combine(root,"config/drink_policy.json"),"{\"version\":1,\"drinks\":{}}");
AppBuilder.Configure<TestApp>().UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing=false }).UseSkia().SetupWithoutStarting();
void Assert(bool condition,string name) { if(!condition) throw new Exception(name); Console.WriteLine("PASS "+name); }
var vm=new DrinkSettingsViewModel(root);
Assert(vm.Drinks.Count==28 && vm.Drinks.Count(d=>d.Target)==6,"28 names and six default targets");
Assert(vm.Drinks.All(d=>d.Thumbnail != null),"all drink images loaded");
var d=vm.Drinks[0]; d.Target=false; d.Timing=1; vm.OpenPicker(d);
Assert(vm.VisibleCards.Count>400 && vm.VisibleCards.All(c=>c.Thumbnail != null),"shared card library images");
vm.Rarity=2; Assert(vm.VisibleCards.All(c=>c.Rarity=="SR"),"rarity filter");
vm.Plan=2; Assert(vm.VisibleCards.All(c=>c.PlanType==2),"plan filter");
vm.Plan=0; var selected=vm.VisibleCards[0]; vm.ChooseCard(selected);
Assert(!vm.PickerOpen && d.Card==selected.Name,"picker selection closes and saves");
var restored=new DrinkSettingsViewModel(root);
Assert(!restored.Drinks[0].Target && restored.Drinks[0].Timing==1 && restored.Drinks[0].Card==selected.Name,"settings survive reopen");
restored.Drinks[1].Timing=2; restored.Drinks[1].Turns=3;
Assert(new DrinkSettingsViewModel(root).Drinks[1].Turns==3,"turn count saves");
restored.TargetFilter=1;
Assert(restored.VisibleDrinks.Count==5 && restored.VisibleDrinks.All(x=>x.Target),"target filter");
var changed=restored.VisibleDrinks[0]; changed.Target=false;
Assert(restored.VisibleDrinks.Count==4 && restored.TargetsLabel=="目标 4","target filter and count update immediately");
changed.Target=true;
restored.TargetFilter=2;
Assert(restored.VisibleDrinks.Count==23 && restored.VisibleDrinks.All(x=>!x.Target),"non-target filter");
restored.TargetFilter=0; restored.Search=restored.Drinks[0].Id;
Assert(restored.VisibleDrinks.Count==1,"search by ID");
restored.Search="no-such-drink"; Assert(restored.IsEmpty,"empty search state");
restored.Search="";
foreach(var theme in new[]{ThemeVariant.Light,ThemeVariant.Dark})
foreach(var width in new[]{800,1120,1600})
{
 Application.Current!.RequestedThemeVariant=theme;
 var view=new DrinkSettingsView { DataContext=restored, Foreground=new SolidColorBrush(theme==ThemeVariant.Light ? Color.Parse("#222222") : Color.Parse("#EDFFFFFF")), Background=new SolidColorBrush(theme==ThemeVariant.Light ? Color.Parse("#F1F2F6") : Color.Parse("#17171C")) };
 var window=new Window { Width=width,Height=820,Content=view }; window.Show(); Dispatcher.UIThread.RunJobs();
 Assert(view.GetVisualDescendants().OfType<ToggleSwitch>().Count(t=>t.IsChecked==true)==5,"target switch bindings reflect saved values");
 view.Measure(new Size(width,820)); view.Arrange(new Rect(0,0,width,820));
 using(var bitmap=new RenderTargetBitmap(new PixelSize(width,820))) { bitmap.Render(view); bitmap.Save(Path.Combine(AppContext.BaseDirectory,$"drinks-{width}-{theme}.png")); }
 restored.OpenPicker(restored.Drinks[0]); Dispatcher.UIThread.RunJobs(); view.Measure(new Size(width,820)); view.Arrange(new Rect(0,0,width,820));
 using(var bitmap=new RenderTargetBitmap(new PixelSize(width,820))) { bitmap.Render(view); bitmap.Save(Path.Combine(AppContext.BaseDirectory,$"picker-{width}-{theme}.png")); }
 restored.ClosePicker(); window.Close();
}
File.WriteAllText(Path.Combine(root,"config/drink_policy.json"),"broken");
var invalid=new DrinkSettingsViewModel(root);
Assert(invalid.Status.Contains("读取失败") && File.ReadAllText(Path.Combine(root,"config/drink_policy.json"))=="broken","corrupt config preserved");
Console.WriteLine("DRINK_UI_TESTS_PASSED");
class TestApp:Application { public override void Initialize() { Styles.Add(new FluentTheme()); Styles.Add(new SukiTheme { ThemeColor=SukiUI.Enums.SukiColor.Blue }); } }
