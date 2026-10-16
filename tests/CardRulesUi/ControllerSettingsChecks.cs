using System.Collections.Concurrent;
using System.Collections.ObjectModel;
using System.Reflection;
using System.Runtime.CompilerServices;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Media.Imaging;
using Avalonia.Media;
using Avalonia.Styling;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MaaFramework.Binding;
using MFAAvalonia.Configuration;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;
using MFAAvalonia.Helper.Converters;
using MFAAvalonia.Helper.ValueType;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.ViewModels.Windows;
using MFAAvalonia.ViewModels.Other;
using MFAAvalonia.ViewModels.UsersControls.Settings;
using MFAAvalonia.Views.UserControls.Settings;
using SukiUI.Controls;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

internal static class ControllerSettingsChecks
{
    // Exercise the real option renderer/persistence without creating devices,
    // starting task threads or loading the user's application configuration.
    public static void Run(string root, string output, Action<bool, string> check)
    {
        static void Field(object value, string name, object fieldValue) => value.GetType()
            .GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(value, fieldValue);
        var services = (ConcurrentDictionary<Type, Lazy<object>>)typeof(Instances)
            .GetField("ServiceCache", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        var idle = (RootViewModel)RuntimeHelpers.GetUninitializedObject(typeof(RootViewModel));
        Field(idle, "_idle", true);
        services[typeof(RootViewModel)] = new Lazy<object>(() => idle);
        var connection = (ConnectSettingsUserControlModel)RuntimeHelpers.GetUninitializedObject(typeof(ConnectSettingsUserControlModel));
        Field(connection, "_currentControllerType", MaaControllerTypes.Win32);
        services[typeof(ConnectSettingsUserControlModel)] = new Lazy<object>(() => connection);
        var tabs = (InstanceTabBarViewModel)RuntimeHelpers.GetUninitializedObject(typeof(InstanceTabBarViewModel));
        Field(tabs, "<Tabs>k__BackingField", new ObservableCollection<InstanceTabViewModel>());
        services[typeof(InstanceTabBarViewModel)] = new Lazy<object>(() => tabs);
        var document = JsonConvert.DeserializeObject<MaaInterface>(File.ReadAllText(Path.Combine(root, "interface.json")),
            new MaaInterfaceSelectAdvancedConverter(false), new MaaInterfaceSelectOptionConverter(false))!;
        typeof(MaaProcessor).GetProperty("Interface")!.SetValue(null, document);
        LanguageHelper.LoadLanguagesFromInterface(document.Languages, root);
        var config = new InstanceConfiguration("controller-settings-test");
        var saved = new Dictionary<string, List<MaaInterface.MaaInterfaceSelectOption>>
        {
            ["PC"] = [new() { Name = "DMM点击延迟", Data = new() { ["dmm_click_delay"] = "120" } }],
            ["Other"] = [new() { Name = "retained", Data = new() { ["value"] = "77" } }],
        };
        config.SetValue(ConfigurationKeys.ControllerOptionItems, saved);
        var processor = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
        Field(processor, "<InstanceConfiguration>k__BackingField", config);
        var vm = (TaskQueueViewModel)RuntimeHelpers.GetUninitializedObject(typeof(TaskQueueViewModel));
        Field(vm, "_processorField", processor);
        Field(vm, "_currentController", MaaControllerTypes.Win32);
        Field(vm, "_savedControllerName", "PC");
        Field(vm, "_currentResource", "DMM");
        Field(vm, "_taskItemViewModels", new ObservableCollection<DragItemViewModel>());
        vm.TaskItemViewModels.Add(new DragItemViewModel(new MaaInterface.MaaInterfaceResource { Name = "__ControllerOption__PC" }));
        vm.UpdateControllerOptions();
        check(vm.TaskItemViewModels.Count == 0, "controller settings no longer create or retain a task row");
        var pc = document.Controller!.First(c => c.Name == "PC");
        check(ReferenceEquals(vm.ControllerOptionItem!.ResourceItem!.SelectOptions, pc.SelectOptions), "connection settings and runtime share options");
        check(pc.SelectOptions![0].Data!["dmm_click_delay"] == "120", "old controller delay restored");
        foreach (var theme in new[] { ThemeVariant.Light, ThemeVariant.Dark })
        {
            Application.Current!.RequestedThemeVariant = theme;
            var settings = new ConnectSettingsUserControl();
            Field(settings, "_taskViewModel", vm);
            typeof(ConnectSettingsUserControl).GetMethod("RefreshControllerOptions", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(settings, null);
            var card = settings.FindControl<GlassCard>("ControllerOptionsCard")!;
            ((StackPanel)settings.Content!).Children.Remove(card);
            card.Margin = new Thickness(20);
            var window = new Window { Width = 700, Height = 240, Content = card,
                Background = new SolidColorBrush(Color.Parse(theme == ThemeVariant.Dark ? "#202225" : "#F6F7FA")) };
            window.Show(); Dispatcher.UIThread.RunJobs();
            var input = card.GetVisualDescendants().OfType<TextBox>().Single();
            input.Text = "240"; Dispatcher.UIThread.RunJobs();
            check(pc.SelectOptions[0].Data!["dmm_click_delay"] == "240", "connection input updates selected value " + theme);
            check((int?)JObject.Parse(pc.SelectOptions[0].PipelineOverride!)["Click_1"]?["post_delay"] == 240, "delay pipeline regenerated " + theme);
            var reloaded = new InstanceConfiguration("controller-settings-test").GetValue(ConfigurationKeys.ControllerOptionItems, saved);
            check(reloaded["PC"][0].Data!["dmm_click_delay"] == "240" && reloaded["Other"][0].Data!["value"] == "77", "persist delay without deleting other controller settings " + theme);
            idle.Idle = false; Dispatcher.UIThread.RunJobs();
            check(!input.IsEnabled, "running task locks connection option " + theme);
            idle.Idle = true; Dispatcher.UIThread.RunJobs();
            window.Measure(new Size(700, 240)); window.Arrange(new Rect(0, 0, 700, 240));
            using var bitmap = new RenderTargetBitmap(new PixelSize(700, 240)); bitmap.Render(window);
            bitmap.Save(Path.Combine(output, $"controller-settings-{theme}.png")); window.Close();
        }
        Field(vm, "_currentController", MaaControllerTypes.Adb); Field(vm, "_savedControllerName", "模拟器");
        vm.UpdateControllerOptions(); check(vm.ControllerOptionItem == null, "no controller options shown for emulator without presets");
        Field(vm, "_currentController", MaaControllerTypes.Win32); Field(vm, "_savedControllerName", "PC");
        pc.SelectOptions = null; vm.UpdateControllerOptions();
        check(vm.ControllerOptionItem!.ResourceItem!.SelectOptions![0].Data!["dmm_click_delay"] == "240", "switch back restores persisted delay");
        FastModeChecks.Run(root, output, vm, document, check);
        services.TryRemove(typeof(RootViewModel), out _);
        services.TryRemove(typeof(ConnectSettingsUserControlModel), out _);
        services.TryRemove(typeof(InstanceTabBarViewModel), out _);
        Console.WriteLine("CONTROLLER_SETTINGS_CHECKS_PASSED");
    }
}
