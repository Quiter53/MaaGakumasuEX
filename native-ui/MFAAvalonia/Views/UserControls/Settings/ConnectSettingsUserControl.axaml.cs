using Avalonia;
using Avalonia.Controls;
using Avalonia.Markup.Xaml;
using MFAAvalonia.Helper;
using MFAAvalonia.ViewModels.Pages;
using System;
using System.ComponentModel;
using Avalonia.Threading;

namespace MFAAvalonia.Views.UserControls.Settings;

public partial class ConnectSettingsUserControl : UserControl
{
    private TaskQueueViewModel? _taskViewModel;

    public ConnectSettingsUserControl()
    {
        DataContext = Instances.ConnectSettingsUserControlModel;
        InitializeComponent();
        Loaded += (_, _) =>
        {
            Instances.InstanceTabBarViewModel.PropertyChanged += OnInstanceChanged;
            LanguageHelper.LanguageChanged += OnLanguageChanged;
            BindCurrentInstance();
        };
        Unloaded += (_, _) =>
        {
            Instances.InstanceTabBarViewModel.PropertyChanged -= OnInstanceChanged;
            LanguageHelper.LanguageChanged -= OnLanguageChanged;
            if (_taskViewModel != null) _taskViewModel.PropertyChanged -= OnTaskOptionsChanged;
            _taskViewModel = null;
        };
    }

    private void OnInstanceChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName == "ActiveTab") BindCurrentInstance();
    }

    private void BindCurrentInstance()
    {
        if (_taskViewModel != null) _taskViewModel.PropertyChanged -= OnTaskOptionsChanged;
        _taskViewModel = Instances.InstanceTabBarViewModel.ActiveTab?.TaskQueueViewModel;
        if (_taskViewModel != null) _taskViewModel.PropertyChanged += OnTaskOptionsChanged;
        RefreshControllerOptions();
    }

    private void OnTaskOptionsChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName == nameof(TaskQueueViewModel.ControllerOptionItem)) RefreshControllerOptions();
    }

    private void OnLanguageChanged(object? sender, EventArgs e) => Dispatcher.UIThread.Post(RefreshControllerOptions);

    private void RefreshControllerOptions()
    {
        ControllerOptionsPanel.Children.Clear();
        var vm = _taskViewModel;
        var item = vm?.ControllerOptionItem;
        ControllerOptionsCard.IsVisible = item != null;
        if (vm != null && item != null)
            new TaskOptionGenerator(vm, vm.PersistControllerOptionConfiguration)
                .GenerateResourceOptionPanelContent(ControllerOptionsPanel, item);
    }
}

