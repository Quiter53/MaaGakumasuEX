using Avalonia;
using Avalonia.Controls;
using Avalonia.Media.Imaging;
using Avalonia.Styling;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;
using MFAAvalonia.Helper.ValueType;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.ViewModels.Other;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

internal static class FastModeChecks
{
    public static void Run(string root, string output, TaskQueueViewModel vm, MaaInterface document, Action<bool,string> check)
    {
        var task = JObject.Parse(File.ReadAllText(Path.Combine(root,"tasks/produce.json")));
        foreach(var name in new[]{"快速命中模式","使用体力药"})
            document.Option![name]=task["option"]![name]!.ToObject<MaaInterface.MaaInterfaceOption>()!;
        var fast = document.Option!["快速命中模式"];
        var yes = fast.Cases!.FindIndex(c=>c.Name=="Yes");
        check(fast.DefaultCase=="Yes", "fast exchange default is enabled");
        foreach(var language in new[]{"zh-CN","en-US","ja-JP","zh-Hant"})
        {
            LanguageHelper.ChangeLanguage(language);
            var selection=new MaaInterface.MaaInterfaceSelectOption{Name="快速命中模式",Index=yes};
            var options=new List<MaaInterface.MaaInterfaceSelectOption>{selection,new(){Name="使用体力药",Index=0}};
            var item=new DragItemViewModel(new MaaInterface.MaaInterfaceResource{Name="test-fast",SelectOptions=options});
            string? saved=null;
            var panel=new StackPanel{Margin=new Thickness(20)};
            new TaskOptionGenerator(vm,()=>saved=JsonConvert.SerializeObject(options)).GenerateResourceOptionPanelContent(panel,item);
            var window=new Window{Width=720,Height=200,Content=panel,RequestedThemeVariant=ThemeVariant.Dark,
                Background=Avalonia.Media.Brushes.Black};
            window.Show();Dispatcher.UIThread.RunJobs();
            var switches=panel.GetVisualDescendants().OfType<ToggleSwitch>().ToArray();
            check(switches.Length==2&&switches[0].Tag?.ToString()=="快速命中模式"&&switches[0].IsChecked==true,"fast switch before stamina "+language);
            var labels=panel.GetVisualDescendants().OfType<TextBlock>().Select(t=>t.Text).ToArray();
            var translated=JObject.Parse(File.ReadAllText(Path.Combine(root,"lang",language+".json")))["快速命中模式"]!.ToString();
            check(labels.Contains(translated),"fast switch localized "+language);
            switches[0].IsChecked=false;Dispatcher.UIThread.RunJobs();
            var reloaded=JsonConvert.DeserializeObject<List<MaaInterface.MaaInterfaceSelectOption>>(saved!)!;
            check(reloaded[0].Index==fast.Cases.FindIndex(c=>c.Name=="No"),"fast switch persists off "+language);
            check((bool)fast.Cases[reloaded[0].Index!.Value].PipelineOverride!["ProduceHIFChangeSourceFlag"]["attach"]!["fast_hit"]! == false,"off choice passes precise policy "+language);
            switches[0].IsChecked=true;Dispatcher.UIThread.RunJobs();
            check(selection.Index==yes,"fast switch can enable again "+language);
            window.Measure(new Size(720,200));window.Arrange(new Rect(0,0,720,200));
            using var bitmap=new RenderTargetBitmap(new PixelSize(720,200));bitmap.Render(window);
            bitmap.Save(Path.Combine(output,$"fast-mode-{language}.png"));window.Close();
        }
        LanguageHelper.ChangeLanguage("zh-CN");
    }
}
