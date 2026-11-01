using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Controls.Templates;
using Avalonia.Data;
using Avalonia.Data.Converters;
using Avalonia.Input;
using Avalonia.Layout;
using Avalonia.Markup.Xaml.MarkupExtensions;
using Avalonia.Media;
using Avalonia.Platform.Storage;
using Avalonia.Xaml.Interactivity;
using MFAAvalonia.Configuration;
using MFAAvalonia.Controls.Liquid;
using MFAAvalonia.Extensions;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper.ValueType;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.ViewModels.UsersControls.Settings;
using MFAAvalonia.Views.UserControls;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using System;
using System.IO;
using Avalonia.Threading;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using Lang.Avalonia.MarkupExtensions;
using SukiUI.Extensions;
using Action = System.Action;

namespace MFAAvalonia.Helper;

public class TaskOptionGenerator(TaskQueueViewModel viewModel, Action saveConfigurationAction)
{
    // 嵌套层级：0 为顶层选项（分组卡片标题使用粗体），子选项使用常规字重。
    private int _depth;

    /// <summary>顶层选项放入玻璃分组卡片，子选项随父项显示在同一卡片内。</summary>
    private static Control WrapGroup(Control content) => new MFAAvalonia.Controls.Liquid.GlassPanel
    {
        Classes = { "box" },
        Padding = new Thickness(6, 6, 8, 6),
        Content = content,
    };

    private static Border CreateSubDivider() => new() { Classes = { "lg-divider" }, Margin = new Thickness(10, 2, 2, 4) };
    
    public void GeneratePanelContent(StackPanel panel, DragItemViewModel dragItem)
    {
        // 检测是否为特殊任务，如果是则生成特殊任务选项面板
        var entry = dragItem.InterfaceItem?.Entry;
        if (!string.IsNullOrEmpty(entry) && AddTaskDialogViewModel.SpecialActionNames.Contains(entry))
        {
            GenerateSpecialTaskPanelContent(panel, dragItem);
            return;
        }

        AddRepeatOption(panel, dragItem);

        if (dragItem.InterfaceItem?.Option != null)
        {
            if (entry == "Produce") AddProduceOptions(panel, dragItem);
            else foreach (var option in dragItem.InterfaceItem.Option.ToList()) AddOption(panel, option, dragItem);
        }

        if (dragItem.InterfaceItem?.Advanced != null)
        {
            foreach (var option in dragItem.InterfaceItem.Advanced.ToList())
            {
               AddAdvancedOption(panel, option);
            }
        }
    }

    public void GenerateCommonPanelContent(StackPanel panel, DragItemViewModel dragItem)
    {
        // 检测是否为特殊任务，如果是则生成特殊任务选项面板
        var entry = dragItem.InterfaceItem?.Entry;
        if (!string.IsNullOrEmpty(entry) && AddTaskDialogViewModel.SpecialActionNames.Contains(entry))
        {
            GenerateSpecialTaskPanelContent(panel, dragItem);
            return;
        }

        AddRepeatOption(panel, dragItem);

        if (dragItem.InterfaceItem?.Option != null)
        {
            if (entry == "Produce") AddProduceOptions(panel, dragItem);
            else foreach (var option in dragItem.InterfaceItem.Option.ToList())
            {
                AddOption(panel, option, dragItem);
            }
        }
    }

    public void GenerateAdvancedPanelContent(StackPanel panel, DragItemViewModel dragItem)
    {
        if (dragItem.InterfaceItem?.Advanced != null)
        {
            foreach (var option in dragItem.InterfaceItem.Advanced.ToList())
            {
                AddAdvancedOption(panel, option);
            }
        }
    }
    
    public void GenerateResourceOptionPanelContent(StackPanel panel, DragItemViewModel dragItem)
    {
        if (dragItem.ResourceItem?.SelectOptions == null)
            return;

        // 收集所有子选项名称（这些选项不应该在顶级显示）
        var subOptionNames = new HashSet<string>();
        foreach (var selectOption in dragItem.ResourceItem.SelectOptions)
        {
            if (MaaProcessor.Interface?.Option?.TryGetValue(selectOption.Name ?? string.Empty, out var interfaceOption) == true)
            {
                if (interfaceOption.Cases != null)
                {
                    foreach (var caseOption in interfaceOption.Cases)
                    {
                        if (caseOption.Option != null)
                        {
                            foreach (var subOptionName in caseOption.Option)
                            {
                                subOptionNames.Add(subOptionName);
                            }
                        }
                    }
                }
            }
        }

        foreach (var selectOption in dragItem.ResourceItem.SelectOptions)
        {
            if (subOptionNames.Contains(selectOption.Name ?? string.Empty))
                continue;

            AddOption(panel, selectOption, dragItem);
        }
    }

    private void AddRepeatOption(Panel panel, DragItemViewModel source)
    {
        if (source.InterfaceItem is not { Repeatable: true }) return;
        
        var grid = CreateBaseGrid();

        var textBlock = CreateLabelText("RepeatOption");
        Grid.SetColumn(textBlock, 0);
        grid.Children.Add(textBlock);

        var numericUpDown = new NumericUpDown
        {
            Value = source.InterfaceItem.RepeatCount ?? 1,
            VerticalAlignment = VerticalAlignment.Center,
            MinWidth = 150,
            HorizontalAlignment = HorizontalAlignment.Right,
            Margin = new Thickness(0, 2, 0, 2),
            Increment = 1,
            Minimum = -1,
        };
        
        BindIdleEnabled(numericUpDown);
        
        numericUpDown.ValueChanged += (_, _) =>
        {
            source.InterfaceItem.RepeatCount = Convert.ToInt32(numericUpDown.Value);
            saveConfigurationAction();
        };
        
        Grid.SetColumn(numericUpDown, 1);
        AddResponsiveBehavior(grid, textBlock, numericUpDown);
        
        grid.Children.Add(numericUpDown);
        panel.Children.Add(WrapGroup(grid));
    }

    // Presentation only: the existing option controls still own validation, cascades and saving.
    // Unknown/future options remain visible instead of being lost in a hard-coded replacement form.
    private void AddProduceOptions(StackPanel panel, DragItemViewModel source)
    {
        panel.Spacing = 12;
        var pending = source.InterfaceItem.Option.ToList();
        void Group(string id, string? title, Func<string, bool> match)
        {
            var options = pending.Where(o => match(o.Name ?? "")).ToList();
            if (options.Count == 0) return;
            var content = new StackPanel { Spacing = 8 };
            if (title != null) content.Children.Add(new TextBlock { Text = ExText.T(title), FontSize = 12.5,
                FontWeight = FontWeight.Bold, Margin = new Thickness(10, 4, 4, 0) });
            foreach (var option in options)
            {
                if (id == "produce-items" && content.Children.Count > 0)
                    content.Children.Add(CreateSubDivider());
                AddOption(content, option, source, wrap: false);
                pending.Remove(option);
            }
            if (content.Children.Count == 0) return;
            var group = WrapGroup(content);
            group.Tag = id;
            panel.Children.Add(group);
        }
        Group("produce-scenario", null, n => n == "培育难度");
        Group("produce-idol", null, n => n.StartsWith("培育偶像", StringComparison.Ordinal));
        Group("produce-mode", null, n => n == "培育模式");

        var links = new AdaptiveTilePanel { MinItemWidth = 100, MaxColumns = 3, Gap = 8, Tag = "produce-strategies" };
        foreach (var (label, page) in new[] { ("出牌规则曲线", LiquidPages.CardRules), ("饮料策略", LiquidPages.Drinks), ("卡牌黑白名单", LiquidPages.CardList) })
        {
            var button = new Button { Classes = { "lg-sm" }, HorizontalAlignment = HorizontalAlignment.Stretch,
                Content = ExText.T(label) + " ↗", Padding = new Thickness(6, 0) };
            button.Click += (_, _) => LiquidNavigation.Navigate(page);
            links.Children.Add(button);
        }
        panel.Children.Add(links);
        Group("produce-items", null, n => n is "出牌后额外等待" or "使用道具");
        var automation = new HashSet<string> { "快速命中模式", "使用体力药", "跳过选择偶像", "启用自动自定义",
            "启用自动支援卡", "启用自动回忆", "启用关注租借", "启用培育失败重试", "跳过准备阶段", "跟随老师的建议" };
        Group("produce-automation", null, automation.Contains);
        foreach (var option in pending) AddOption(panel, option, source);
    }

    private void AddOption(Panel panel, MaaInterface.MaaInterfaceSelectOption option, DragItemViewModel source, bool wrap = true)
    {
        if (MaaProcessor.Interface?.Option?.TryGetValue(option.Name ?? string.Empty, out var interfaceOption) != true) return;

        // 过滤：根据 option.controller / option.resource 隐藏不适用项（任务 11）
        if (!IsOptionApplicable(interfaceOption)) return;

        if (MFAAvalonia.Views.UserControls.TaskTargetControl.Handles(option.Name))
        {
            var memoryControl = MFAAvalonia.Views.UserControls.TaskTargetControl.Create(option, () => {
                option.PipelineOverride = interfaceOption.GenerateProcessedPipeline(option.Data.Where(kv => kv.Value != null).ToDictionary(kv => kv.Key, kv => kv.Value!));
                saveConfigurationAction();
            });
            BindIdleEnabled(memoryControl);
            panel.Children.Add(wrap ? WrapGroup(memoryControl) : memoryControl);
            return;
        }
        Control control;

        if (interfaceOption.IsInput)
        {
            control = CreateInputControl(option, interfaceOption);
        }
        else if (interfaceOption.IsCheckbox)
        {
            // checkbox 类型：多选 ToggleButton 列表（任务 8）
            control = CreateCheckboxControl(option, interfaceOption, source);
        }
        else if ((interfaceOption.IsSwitch && interfaceOption.Cases.ShouldSwitchButton(out var yes, out var no)) ||
                 interfaceOption.Cases.ShouldSwitchButton(out yes, out no)) // Try both conditions with/without type checking logic
        {
            control = CreateToggleControl(option, yes, no, interfaceOption, source);
        }
        else
        {
            control = CreateComboBoxControl(option, interfaceOption, source);
        }

        panel.Children.Add(wrap && _depth == 0 ? WrapGroup(control) : control);
    }

    /// <summary>
    /// 检查 option 是否适用于当前控制器和资源（任务 11）
    /// </summary>
    private bool IsOptionApplicable(MaaInterface.MaaInterfaceOption interfaceOption)
    {
        var currentControllerName = MaaInterfaceActivationHelper.ResolveControllerName(
            MaaProcessor.Interface,
            viewModel.CurrentController) ?? viewModel.CurrentController.ToJsonKey();

        return MaaInterfaceActivationHelper.IsOptionApplicable(
            MaaProcessor.Interface,
            interfaceOption,
            currentControllerName,
            viewModel.CurrentResource);
    }

    /// <summary>
    /// 创建 checkbox 类型的多选 ToggleButton 控件（任务 8）
    /// </summary>
    private Control CreateCheckboxControl(MaaInterface.MaaInterfaceSelectOption option, MaaInterface.MaaInterfaceOption interfaceOption, DragItemViewModel source)
    {
        var container = new StackPanel { Margin = new Thickness(10, 10, 10, 6), Spacing = 4 };

        interfaceOption.InitializeIcon();

        // Header（显示 option 名称和图标）
        var header = CreateOptionHeader(interfaceOption);
        header.Margin = new Thickness(0); // 外层 container 已提供垂直间距，去掉 header 内部多余的上下 margin
        container.Children.Add(header);

        // 初始化 SelectedCases（任务 9）
        option.SelectedCases ??= new List<string>(interfaceOption.DefaultCases ?? new List<string>());

        // WrapPanel of ToggleButtons（等宽按钮，自然换行）
        Panel wrapPanel = option.Name == "使用道具" ? new AdaptiveTilePanel { MinItemWidth = 140, MaxColumns = 2, Gap = 8 } : new WrapPanel
        {
            Margin = new Thickness(0, 2, 0, 2),
            HorizontalAlignment = HorizontalAlignment.Left
        };

        // Sub-options container（显示所有被勾选 case 的子选项）
        var subOptionsContainer = new StackPanel();

        // Sub-option update logic for checkbox（与 select/switch 不同，需要显示所有已勾选 case 的子选项）
        void UpdateSubOptions()
        {
            subOptionsContainer.Children.Clear();
            if (interfaceOption.Cases == null) return;

            option.SubOptions ??= new List<MaaInterface.MaaInterfaceSelectOption>();
            var selectedCases = option.SelectedCases ?? new List<string>();

            foreach (var caseItem in interfaceOption.Cases)
            {
                if (caseItem.Name == null || !selectedCases.Contains(caseItem.Name)) continue;
                if (caseItem.Option == null || caseItem.Option.Count == 0) continue;

                foreach (var subOptionName in caseItem.Option)
                {
                    var existing = option.SubOptions.FirstOrDefault(o => o.Name == subOptionName);
                    if (existing == null)
                    {
                        existing = CreateDefaultSelectOption(subOptionName);
                        option.SubOptions.Add(existing);
                    }
                    AddSubOption(subOptionsContainer, existing, source);
                }
            }
        }

        if (interfaceOption.Cases != null)
        {
            foreach (var caseOption in interfaceOption.Cases)
            {
                caseOption.InitializeDisplayName();
                var caseName = caseOption.Name ?? string.Empty;
                var isChecked = option.SelectedCases.Contains(caseName);

                var toggleBtn = new ToggleButton
                {
                    IsChecked = isChecked,
                    Margin = new Thickness(2,2,6,6),
                    Padding = new Thickness(6, 4, 6, 4),
                    HorizontalAlignment = HorizontalAlignment.Stretch,
                    HorizontalContentAlignment = HorizontalAlignment.Center,
                };

                // 按钮内容：图标 + 滚动文字（用 Grid 让 MarqueeTextBlock 自适应剩余宽度）
                var btnContent = new Grid
                {
                    ColumnDefinitions =
                    {
                        new ColumnDefinition { Width = GridLength.Auto },
                        new ColumnDefinition { Width = GridLength.Star },
                        new ColumnDefinition { Width = GridLength.Auto },
                    }
                };

                var iconDisplay = new DisplayIcon
                {
                    IconSize = 16,
                    Margin = new Thickness(0, 0, 4, 0),
                    VerticalAlignment = VerticalAlignment.Center
                };
                iconDisplay.Bind(DisplayIcon.IconSourceProperty, new Binding(nameof(MaaInterface.MaaInterfaceOptionCase.ResolvedIcon)) { Source = caseOption });
                iconDisplay.Bind(Visual.IsVisibleProperty, new Binding(nameof(MaaInterface.MaaInterfaceOptionCase.HasIcon)) { Source = caseOption });
                Grid.SetColumn(iconDisplay, 0);

                var marqueeText = new MarqueeTextBlock
                {
                    VerticalContentAlignment = VerticalAlignment.Center,
                    HorizontalAlignment = HorizontalAlignment.Stretch,
                };
                marqueeText.Bind(MarqueeTextBlock.TextProperty, new ResourceBindingWithFallback(caseOption.DisplayName, caseOption.Name));
                Grid.SetColumn(marqueeText, 1);

                btnContent.Children.Add(iconDisplay);
                btnContent.Children.Add(marqueeText);
                toggleBtn.Content = btnContent;

                // Item cases that carry a picture (e.g. 使用道具: "![](resource/base/image/items/...png)") are shown as
                // item cards: picture, name and an explicit check mark; selection logic is unchanged.
                if (ItemImage(caseOption.Description) is { } itemImage)
                {
                    toggleBtn.Classes.Add("lg-item-card");
                    toggleBtn.Margin = new Thickness(0);
                    toggleBtn.Padding = new Thickness(10, 8);
                    toggleBtn.HorizontalContentAlignment = HorizontalAlignment.Stretch;
                    btnContent.ColumnDefinitions.Add(new ColumnDefinition(GridLength.Auto));
                    var status = new TextBlock { FontSize = 11, VerticalAlignment = VerticalAlignment.Center };
                    status.Bind(TextBlock.TextProperty, new Binding(nameof(ToggleButton.IsChecked)) { Source = toggleBtn, Converter = new FuncValueConverter<bool?, string>(value => ExText.T(value == true ? "已选" : "未选")) });
                    btnContent.Children.Add(status);
                    Grid.SetColumn(status, 2);
                    var picture = new Border { Width = 24, Height = 24, CornerRadius = new CornerRadius(10), ClipToBounds = true, Margin = new Thickness(0, 0, 10, 0),
                        Child = new Avalonia.Controls.Image { Source = itemImage, Stretch = Stretch.Uniform } };
                    btnContent.Children.Remove(iconDisplay);
                    Grid.SetColumn(picture, 0);
                    btnContent.Children.Insert(0, picture);
                    // Selection is displayed by the independent status label above.
                    marqueeText.Margin = new Thickness(0, 0, 8, 0);
                }

                BindIdleEnabled(toggleBtn);

                var capturedCaseName = caseName;
                toggleBtn.IsCheckedChanged += (_, _) =>
                {
                    if (toggleBtn.IsChecked == true)
                    {
                        if (!option.SelectedCases.Contains(capturedCaseName))
                            option.SelectedCases.Add(capturedCaseName);
                    }
                    else
                    {
                        option.SelectedCases.Remove(capturedCaseName);
                    }
                    UpdateSubOptions();
                    saveConfigurationAction();
                };

                // TooltipBlock 放在 ToggleButton 内部（btnContent 末尾），
                // TooltipBlock 会自动检测父级 Button 并通过 PointerMoved 坐标追踪来显示 Flyout，
                // 绕过 SukiUI 按钮模板 ContentPresenter 的 IsHitTestVisible="False" 限制。
                if (caseOption.HasDescription && !string.IsNullOrWhiteSpace(MarkdownImage.Replace(caseOption.DisplayDescription ?? "", "")))
                {
                    var tooltipBlock = new TooltipBlock
                    {
                        Margin = new Thickness(2, 0, 0, 0),
                        VerticalAlignment = VerticalAlignment.Center
                    };
                    tooltipBlock.Bind(TooltipBlock.TooltipTextProperty,
                        new Binding(nameof(MaaInterface.MaaInterfaceOptionCase.DisplayDescription)) { Source = caseOption });
                    Grid.SetColumn(tooltipBlock, toggleBtn.Classes.Contains("lg-item-card") ? 3 : 2);
                    btnContent.Children.Add(tooltipBlock);
                }

                wrapPanel.Children.Add(toggleBtn);
            }
        }

        // 首次布局后测量所有按钮自然宽度，取最大值统一设置 MinWidth（等宽效果）；物品卡片使用固定宽度
        if (wrapPanel.Children.Count > 0 && wrapPanel.Children.OfType<ToggleButton>().All(b => !b.Classes.Contains("lg-item-card")))
        {
            wrapPanel.LayoutUpdated += EqualizeOnce;
            void EqualizeOnce(object? s, EventArgs ev)
            {
                wrapPanel.LayoutUpdated -= EqualizeOnce;
                double maxWidth = 0;
                foreach (var child in wrapPanel.Children)
                {
                    if (child is ToggleButton btn)
                    {
                        btn.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
                        if (btn.DesiredSize.Width > maxWidth)
                            maxWidth = btn.DesiredSize.Width;
                    }
                }
                if (maxWidth > 0)
                {
                    // 限制最大宽度不超过容器宽度减去按钮 Margin
                    var containerWidth = container.Bounds.Width;
                    if (containerWidth > 0)
                    {
                        var btnMarginH = 2 + 6; // Margin Left + Right
                        maxWidth = Math.Min(maxWidth, containerWidth - btnMarginH);
                    }
                    foreach (var child in wrapPanel.Children)
                    {
                        if (child is ToggleButton btn)
                            btn.MinWidth = maxWidth;
                    }
                }
            }
        }

        container.Children.Add(wrapPanel);

        // Initialize sub-options for default checked cases
        UpdateSubOptions();

        // Enhanced Sub-option visualization
        var subOptionsBorder = new Border
        {
            BorderThickness = new Thickness(2, 0, 0, 0),
            Background = Brushes.Transparent,
            Margin = new Thickness(24, 0, 0, 4),
            Padding = new Thickness(8, 0, 0, 0),
            Child = subOptionsContainer
        };
        subOptionsBorder.Bind(Border.BorderBrushProperty, new DynamicResourceExtension("SukiPrimaryColor"));
        subOptionsBorder.Bind(Visual.IsVisibleProperty, new Binding("Children.Count")
        {
            Source = subOptionsContainer,
            Converter = new FuncValueConverter<int, bool>(count => count > 0)
        });

        container.Children.Add(subOptionsBorder);
        return container;
    }

    private Control CreateInputControl(MaaInterface.MaaInterfaceSelectOption option, MaaInterface.MaaInterfaceOption interfaceOption)
    {
        var hasOptionDescription = !string.IsNullOrWhiteSpace(GetTooltipText(interfaceOption.Description, interfaceOption.Document));
        var isSingleInput = interfaceOption.Inputs?.Count == 1;
        
        // 单输入且有 option description 时，需要显示 header，所以使用与多输入相同的 margin
        var needsHeader = !isSingleInput || hasOptionDescription;
        
        var container = new StackPanel
        {
            Margin = needsHeader ? new Thickness(10, 3, 10, 3) : new Thickness(0),
            Spacing = 4
        };

        option.Data ??= new Dictionary<string, string?>();
        if (interfaceOption.Inputs == null || interfaceOption.Inputs.Count == 0) return container;

        interfaceOption.InitializeIcon();

        // Header for multi-input options OR single input with option description
        if (needsHeader)
        {
            container.Children.Add(CreateOptionHeader(interfaceOption));
        }

        foreach (var input in interfaceOption.Inputs)
        {
            if (string.IsNullOrEmpty(input.Name)) continue;

            if (!option.Data.TryGetValue(input.Name, out var currentValue) || currentValue == null)
            {
                currentValue = input.Default ?? string.Empty;
                option.Data[input.Name] = currentValue;
            }

            var pipelineType = input.PipelineType?.ToLower() ?? "string";

            if (pipelineType == "bool")
            {
                container.Children.Add(CreateBoolInputControl(input, currentValue, option, interfaceOption));
            }
            else
            {
                container.Children.Add(CreateStringInputControl(input, currentValue, option, interfaceOption));
            }
        }

        return container;
    }

    private Control CreateStringInputControl(
        MaaInterface.MaaInterfaceOptionInput input, 
        string currentValue, 
        MaaInterface.MaaInterfaceSelectOption option, 
        MaaInterface.MaaInterfaceOption interfaceOption)
    {
        var grid = CreateBaseGrid();
        
        // Adjust margin based on whether header is shown
        var hasOptionDescription = !string.IsNullOrWhiteSpace(GetTooltipText(interfaceOption.Description, interfaceOption.Document));
        var needsHeader = interfaceOption.Inputs.Count > 1 || hasOptionDescription;
        
        if (needsHeader)
        {
            // When header is shown, container already has margin, so grid needs no extra left/right margin
            grid.Margin = new Thickness(0, 3, 0, 3);
        }
        else
        {
            // Single input without header needs its own margin
            grid.Margin = new Thickness(10, 6, 10, 6);
        }

        // TextBox
        var displayValue = currentValue == MaaInterface.MaaInterfaceOption.ExplicitNullMarker ? "null" : currentValue;
        var textBox = new TextBox
        {
            MinWidth = 120,
            Margin = new Thickness(0, 2, 0, 2),
            BorderThickness = new Thickness(1),
            Text = displayValue,
            VerticalAlignment = VerticalAlignment.Center
        };
        textBox.Bind(TextBox.BorderBrushProperty, new DynamicResourceExtension("SukiControlBorderBrush"));
        
        if (!string.IsNullOrWhiteSpace(input.PatternMsg))
            textBox.Bind(TextBox.WatermarkProperty, new ResourceBinding(input.PatternMsg));
        
        BindIdleEnabled(textBox);

        // Events
        textBox.TextChanged += (_, _) => HandleStringInputChange(textBox, input, option, interfaceOption);
        
        // Initial setup
        HandleStringInputChange(textBox, input, option, interfaceOption, true); 

        // Label Panel
        var labelPanel = CreateLabelPanel(input.DisplayName, input.Name, input.Description);

        // Icon (Show only if single input WITHOUT header, because header already has icon)
        if (!needsHeader)
        {
            var icon = CreateIcon(interfaceOption);
            icon.Margin = new Thickness(10, 0, 6, 0); // specific margin for single input layout
            labelPanel.Children.Insert(0, icon);
        }

        // Numeric inputs get -/+ around the same text box (prototype stepper); manual typing still goes
        // through the same validation, and the buttons only emit values the field's own regex accepts.
        Control field = TryCreateStepper(input, textBox) ?? textBox;

        Grid.SetColumn(labelPanel, 0);
        Grid.SetColumn(field, 1);
        
        AddResponsiveBehavior(grid, labelPanel, field);
        
        grid.Children.Add(labelPanel);
        grid.Children.Add(field);

        return grid;
    }

    /// <summary>
    /// Wraps a numeric input in a stepper. Applies when the default is a number accepted by the input's
    /// verify pattern; the step is the default's precision (1.5 → 0.1, 3 → 1). A button press moves to
    /// the next value the pattern accepts, so the pattern alone defines the range (e.g. 0–5 seconds).
    /// </summary>
    private Control? TryCreateStepper(MaaInterface.MaaInterfaceOptionInput input, TextBox textBox)
    {
        var inv = System.Globalization.CultureInfo.InvariantCulture;
        if (string.IsNullOrWhiteSpace(input.Default) || !decimal.TryParse(input.Default, System.Globalization.NumberStyles.Number, inv, out _)) return null;
        System.Text.RegularExpressions.Regex? verify = null;
        if (!string.IsNullOrWhiteSpace(input.Verify))
        {
            try { verify = new System.Text.RegularExpressions.Regex(input.Verify); }
            catch (ArgumentException) { return null; }
            if (!verify.IsMatch(input.Default)) return null;
        }
        var decimals = input.Default.Contains('.') ? input.Default.Length - input.Default.IndexOf('.') - 1 : 0;
        var step = decimals == 0 ? 1m : 0.1m;
        var format = decimals == 0 ? "0" : "0.0#";
        bool Accepts(string text) => verify == null || verify.IsMatch(text);

        void Step(int direction)
        {
            if (!decimal.TryParse(textBox.Text, System.Globalization.NumberStyles.Number, inv, out var value))
                value = decimal.Parse(input.Default, inv);
            // Snap to the step grid (1.25 -> 1.3 / 1.2), then move one step; at the edge of the accepted range stay put.
            var snapped = direction > 0 ? Math.Floor(value / step) * step : Math.Ceiling(value / step) * step;
            var text = (snapped + direction * step).ToString(format, inv);
            if (Accepts(text)) textBox.Text = text;
        }

        Button Make(string kind, int direction, string name)
        {
            var button = new Button { Classes = { "lg-step" }, Content = new MFAAvalonia.Controls.Liquid.LiquidIcon { Kind = kind, Size = 12 } };
            Avalonia.Automation.AutomationProperties.SetName(button, ExText.T(name));
            button.Click += (_, _) => Step(direction);
            BindIdleEnabled(button);
            return button;
        }

        textBox.MinWidth = 48;
        textBox.Width = 54;
        textBox.MinHeight = 26;
        textBox.Height = 26;
        textBox.Padding = new Thickness(2, 0);
        textBox.TextAlignment = TextAlignment.Center;
        textBox.Classes.Add("lg-step-value");
        return new Border
        {
            Classes = { "lg-stepper" },
            VerticalAlignment = VerticalAlignment.Center,
            Child = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 2,
                Children = { Make("minus", -1, "减少"), textBox, Make("plus", 1, "增加") } },
        };
    }

    private Control CreateBoolInputControl(
        MaaInterface.MaaInterfaceOptionInput input,
        string currentValue,
        MaaInterface.MaaInterfaceSelectOption option,
        MaaInterface.MaaInterfaceOption interfaceOption)
    {
        // Adjust margin based on whether header is shown
        var hasOptionDescription = !string.IsNullOrWhiteSpace(GetTooltipText(interfaceOption.Description, interfaceOption.Document));
        var needsHeader = interfaceOption.Inputs.Count > 1 || hasOptionDescription;
        
        var grid = new Grid
        {
            ColumnDefinitions =
            {
                new ColumnDefinition { Width = GridLength.Auto },
                new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) },
                new ColumnDefinition { Width = GridLength.Auto }
            },
            Margin = needsHeader ? new Thickness(0, 3, 0, 3) : new Thickness(10, 6, 10, 6)
        };

        bool isChecked = currentValue.Equals("true", StringComparison.OrdinalIgnoreCase) || currentValue == "1";

        var toggleSwitch = new ToggleSwitch
        {
            IsChecked = isChecked,
            Classes = { "Switch" },
            MaxHeight = 60,
            MaxWidth = 100,
            Margin = new Thickness(0, 2, 0, 2),
            HorizontalAlignment = HorizontalAlignment.Right,
            VerticalAlignment = VerticalAlignment.Center
        };
        
        BindIdleEnabled(toggleSwitch);
        toggleSwitch.Bind(ToolTip.TipProperty, new ResourceBindingWithFallback(input.DisplayName, input.Name));

        var fieldName = input.Name;
        toggleSwitch.IsCheckedChanged += (_, _) =>
        {
            var boolValue = toggleSwitch.IsChecked == true;
            option.Data[fieldName] = boolValue.ToString().ToLower();
            UpdatePipeline(option, interfaceOption);
            saveConfigurationAction();
        };

        var labelPanel = CreateLabelPanel(input.DisplayName, input.Name, input.Description);
        labelPanel.Margin = new Thickness(10, 0, 5, 0);

        Grid.SetColumn(labelPanel, 0);
        Grid.SetColumn(toggleSwitch, 2);
        
        grid.Children.Add(labelPanel);
        grid.Children.Add(toggleSwitch);

        return grid;
    }

    private Control CreateToggleControl(
        MaaInterface.MaaInterfaceSelectOption option,
        int yesValue,
        int noValue,
        MaaInterface.MaaInterfaceOption interfaceOption,
        DragItemViewModel source)
    {
        var wrapper = new StackPanel();

        var grid = new Grid
        {
            ColumnDefinitions =
            {
                new ColumnDefinition { Width = GridLength.Auto },
                new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) },
                new ColumnDefinition { Width = GridLength.Auto }
            },
            Margin = new Thickness(10, 6, 10, 6)
        };
        
        interfaceOption.InitializeIcon();

        var toggleSwitch = new ToggleSwitch
        {
            IsChecked = option.Index == yesValue,
            Classes = { "Switch" },
            MaxHeight = 60,
            MaxWidth = 100,
            Margin = new Thickness(0, 4, 0, 4),
            HorizontalAlignment = HorizontalAlignment.Right,
            VerticalAlignment = VerticalAlignment.Center,
            Tag = option.Name
        };

        BindIdleEnabled(toggleSwitch);
        toggleSwitch.Bind(ToolTip.TipProperty, new ResourceBindingWithFallback(option.DisplayName, option.Name));
        
        // Sub-options container
        var subOptionsContainer = new StackPanel();

        // Sub-option update logic
        void UpdateSubOptions(int index)
        {
            subOptionsContainer.Children.Clear();
            if (interfaceOption.Cases == null || index < 0 || index >= interfaceOption.Cases.Count) return;

            var selectedCase = interfaceOption.Cases[index];
            if (selectedCase.Option == null || selectedCase.Option.Count == 0) return;

            option.SubOptions ??= new List<MaaInterface.MaaInterfaceSelectOption>();

            foreach (var subOptionName in selectedCase.Option)
            {
                var existing = option.SubOptions.FirstOrDefault(o => o.Name == subOptionName);
                if (existing == null)
                {
                    existing = CreateDefaultSelectOption(subOptionName);
                    option.SubOptions.Add(existing);
                }
                AddSubOption(subOptionsContainer, existing, source);
            }
        }

        toggleSwitch.IsCheckedChanged += (_, _) =>
        {
            option.Index = toggleSwitch.IsChecked == true ? yesValue : noValue;
            UpdateSubOptions(option.Index ?? 0);
            saveConfigurationAction();
        };

        // Initialize sub-options
        UpdateSubOptions(option.Index ?? 0);

        // Label with Icon
        var labelPanel = CreateLabelPanel(option.DisplayName, option.Name, interfaceOption.Description, interfaceOption.Document);
        var icon = CreateIcon(interfaceOption);
        icon.Margin = new Thickness(10, 0, 6, 0); 
        labelPanel.Children.Insert(0, icon);

        Grid.SetColumn(labelPanel, 0);
        Grid.SetColumn(toggleSwitch, 2);
        
        grid.Children.Add(labelPanel);
        grid.Children.Add(toggleSwitch);
        wrapper.Children.Add(grid);

        // Enhanced Sub-option visualization
        var subOptionsBorder = new Border
        {
            Margin = new Thickness(0, 2, 0, 0),
            Child = option.Name == "启用自动自定义" ? subOptionsContainer : new StackPanel { Children = { CreateSubDivider(), subOptionsContainer } }
        };
        subOptionsBorder.Bind(Visual.IsVisibleProperty,new Binding("Children.Count")
        {
            Source = subOptionsContainer,
            Converter = new FuncValueConverter<int, bool>(count => count > 0)
        });
        
        wrapper.Children.Add(subOptionsBorder);

        return wrapper;
    }

    private Control CreateComboBoxControl(
        MaaInterface.MaaInterfaceSelectOption option, 
        MaaInterface.MaaInterfaceOption interfaceOption,
        DragItemViewModel source)
    {
        var wrapper = new StackPanel();
        var grid = CreateBaseGrid();

        interfaceOption.Cases?.ForEach(c => c.InitializeDisplayName());
        interfaceOption.InitializeIcon();

        if (IdolAvatarFile(interfaceOption) != null)
            return CreateIdolPicker(option, interfaceOption, source);
        if (UseSegmented(interfaceOption))
            return CreateSegmentedControl(option, interfaceOption, source);

        var longest = interfaceOption.Cases?.Max(c => (c.DisplayName ?? c.Name ?? "").Length) ?? 0;
        var comboBox = new ComboBox
        {
            Width = longest > 10 ? 270 : 200,
            Classes = { "LimitWidth" },
            Margin = new Thickness(0, 2, 0, 2),
            ItemsSource = interfaceOption.Cases,
            SelectedIndex = option.Index ?? 0,
            HorizontalAlignment = HorizontalAlignment.Right
        };
        
        BindIdleEnabled(comboBox);
        SetupComboBoxTemplate(comboBox);

        // Sub-options container
        var subOptionsContainer = new StackPanel();

        void UpdateSubOptions(int index)
        {
            subOptionsContainer.Children.Clear();
            if (interfaceOption.Cases == null || index < 0 || index >= interfaceOption.Cases.Count) return;

            var selectedCase = interfaceOption.Cases[index];
            if (selectedCase.Option == null || selectedCase.Option.Count == 0) return;

            option.SubOptions ??= new List<MaaInterface.MaaInterfaceSelectOption>();

            foreach (var subOptionName in selectedCase.Option)
            {
                var existing = option.SubOptions.FirstOrDefault(o => o.Name == subOptionName);
                if (existing == null)
                {
                    existing = CreateDefaultSelectOption(subOptionName);
                    option.SubOptions.Add(existing);
                }
                AddSubOption(subOptionsContainer, existing, source);
            }
        }

        comboBox.SelectionChanged += (_, _) =>
        {
            // Filtering/rebuilding the list can temporarily clear selection. Only a
            // concrete case is a user choice; never persist the UI sentinel -1.
            if (comboBox.SelectedItem is not MaaInterface.MaaInterfaceOptionCase selectedCase || interfaceOption.Cases == null) return;
            var resolvedIndex = interfaceOption.Cases.FindIndex(c => c.Name == selectedCase.Name);
            if (resolvedIndex < 0 || resolvedIndex == option.Index) return;
            option.Index = resolvedIndex;
            UpdateSubOptions(resolvedIndex);
            saveConfigurationAction();
        };
        
        UpdateSubOptions(option.Index ?? 0);
        ComboBoxExtensions.SetDisableNavigationOnLostFocus(comboBox, true);
        ComboBoxExtensions.SetCanSearch(comboBox, true);
        ComboBoxExtensions.SetSearchMemberPath(comboBox, "DisplayName");
        comboBox.Bind(ComboBoxExtensions.SearchWatermarkProperty, new I18nBinding(LangKeys.Search));
        // Header
        var labelPanel = CreateLabelPanel(option.DisplayName, option.Name, interfaceOption.Description, interfaceOption.Document);
        var icon = CreateIcon(interfaceOption);
        icon.Margin = new Thickness(10, 0, 6, 0); // Margin adjusted
        labelPanel.Children.Insert(0, icon);

        Grid.SetColumn(labelPanel, 0);
        Grid.SetColumn(comboBox, 1);
        
        AddResponsiveBehavior(grid, labelPanel, comboBox);
        
        grid.Children.Add(labelPanel);
        grid.Children.Add(comboBox);

        wrapper.Children.Add(grid);
        
        // Sub-options border
        var subOptionsBorder = new Border
        {
            Margin = new Thickness(0, 2, 0, 0),
            Child = option.Name?.Contains("上课", StringComparison.Ordinal) == true ? subOptionsContainer : new StackPanel { Children = { CreateSubDivider(), subOptionsContainer } }
        };
        subOptionsBorder.Bind(Visual.IsVisibleProperty, new Binding("Children.Count")
        {
            Source = subOptionsContainer,
            Converter = new FuncValueConverter<int, bool>(count => count > 0)
        });

        wrapper.Children.Add(subOptionsBorder);

        return wrapper;
    }
    
    /// <summary>短小的二至四项选择使用分段控件（原型中的 初 / NIA / HIF 等）。</summary>
    private static bool UseSegmented(MaaInterface.MaaInterfaceOption interfaceOption) =>
        interfaceOption.Cases is { Count: >= 2 and <= 4 } cases &&
        cases.All(c => !c.HasIcon && string.IsNullOrEmpty(c.Description) && (c.DisplayName ?? c.Name ?? "").Length <= 8);

    private Control CreateSegmentedControl(
        MaaInterface.MaaInterfaceSelectOption option,
        MaaInterface.MaaInterfaceOption interfaceOption,
        DragItemViewModel source)
    {
        var segmented = new MFAAvalonia.Controls.Liquid.SegmentedControl
        {
            ItemsSource = interfaceOption.Cases,
            HorizontalAlignment = HorizontalAlignment.Right,
            Margin = new Thickness(0, 2, 0, 2),
            ItemTemplate = new FuncDataTemplate<MaaInterface.MaaInterfaceOptionCase>((caseOption, _) =>
            {
                var tb = new TextBlock { VerticalAlignment = VerticalAlignment.Center, HorizontalAlignment = HorizontalAlignment.Center, TextTrimming = TextTrimming.CharacterEllipsis };
                tb.Bind(TextBlock.TextProperty, new Binding(nameof(MaaInterface.MaaInterfaceOptionCase.DisplayName)) { Source = caseOption });
                return tb;
            }),
        };
        return CreateCaseSelector(option, interfaceOption, source, segmented, stacked: false);
    }

    // Idol face crops shipped with the resources (resource/base/image/idols), keyed by the case names used
    // in the Japanese and Chinese task definitions.
    private static readonly Dictionary<string, string> IdolAvatars = new()
    {
        ["花海咲季"] = "hanami_saki", ["月村手毬"] = "tukimura_temari", ["藤田琴音"] = "huzita_kotone", ["藤田ことね"] = "huzita_kotone",
        ["有村麻央"] = "arimura_mao", ["葛城リーリヤ"] = "katuragi_ririya", ["葛城莉莉娅"] = "katuragi_ririya",
        ["倉本千奈"] = "kuramoto_tina", ["仓本千奈"] = "kuramoto_tina", ["紫雲清夏"] = "sion_sumika", ["紫云清夏"] = "sion_sumika",
        ["篠澤広"] = "sasazawa_hiro", ["篠泽广"] = "sasazawa_hiro", ["筱泽广"] = "sasazawa_hiro",
        ["姫崎莉波"] = "himezaki_rinami", ["姬崎莉波"] = "himezaki_rinami", ["花海佑芽"] = "hanami_ume",
        ["秦谷美鈴"] = "hataya_misuzu", ["秦谷美铃"] = "hataya_misuzu", ["十王星南"] = "zyuuou_sena", ["雨夜燕"] = "tsubame_amaya",
    };

    private static readonly System.Text.RegularExpressions.Regex MarkdownImage = new(@"!\[[^\]]*\]\(([^)\s]+)\)");
    private static readonly Dictionary<string, Avalonia.Media.Imaging.Bitmap?> ItemImageCache = new();

    /// <summary>Picture referenced by a case description ("![](relative/path.png)"), resolved under the data root.</summary>
    private static Avalonia.Media.Imaging.Bitmap? ItemImage(string? description)
    {
        if (string.IsNullOrWhiteSpace(description)) return null;
        var match = MarkdownImage.Match(description);
        if (!match.Success) return null;
        var relative = match.Groups[1].Value.Replace('/', Path.DirectorySeparatorChar);
        if (ItemImageCache.TryGetValue(relative, out var cached)) return cached;
        Avalonia.Media.Imaging.Bitmap? bitmap = null;
        try
        {
            var path = Path.Combine(AppPaths.DataRoot, relative);
            if (File.Exists(path)) bitmap = new Avalonia.Media.Imaging.Bitmap(path);
        }
        catch (Exception ex) { LoggerHelper.Warning($"物品图片加载失败：{relative}（{ex.Message}）"); }
        return ItemImageCache[relative] = bitmap;
    }

    /// <summary>The idol choice is recognised by its cases (almost all map to an idol face), not by option key.</summary>
    private static string? IdolAvatarFile(MaaInterface.MaaInterfaceOption interfaceOption) =>
        interfaceOption.Cases is { Count: >= 8 } cases && cases.Count(c => IdolAvatars.ContainsKey(c.Name ?? "")) >= cases.Count - 1 ? "idols" : null;

    private static readonly Dictionary<string, Avalonia.Media.Imaging.Bitmap?> AvatarCache = new();

    private static Avalonia.Media.Imaging.Bitmap? LoadAvatar(string? caseName)
    {
        if (caseName == null || !IdolAvatars.TryGetValue(caseName, out var file)) return null;
        if (AvatarCache.TryGetValue(file, out var cached)) return cached;
        Avalonia.Media.Imaging.Bitmap? bitmap = null;
        try
        {
            var path = Path.Combine(AppPaths.ResourceDirectory, "base", "image", "idols", file + ".png");
            if (File.Exists(path)) bitmap = new Avalonia.Media.Imaging.Bitmap(path);
        }
        catch (Exception ex) { LoggerHelper.Warning($"偶像头像加载失败：{file}（{ex.Message}）"); }
        return AvatarCache[file] = bitmap;
    }

    /// <summary>
    /// Idol choice as an avatar grid (prototype). Same option semantics as the select: one case,
    /// the idol's card option appears below, the generic keyboard/automation of ListBox is kept.
    /// </summary>
    private Control CreateIdolPicker(
        MaaInterface.MaaInterfaceSelectOption option,
        MaaInterface.MaaInterfaceOption interfaceOption,
        DragItemViewModel source)
    {
        var grid = new ListBox
        {
            Classes = { "lg-idol-grid" },
            ItemsSource = interfaceOption.Cases,
            SelectionMode = SelectionMode.Single,
            ItemsPanel = new FuncTemplate<Panel?>(() => new AdaptiveTilePanel { MinItemWidth = 92, Gap = 10 }),
            ItemTemplate = new FuncDataTemplate<MaaInterface.MaaInterfaceOptionCase>((caseOption, _) =>
            {
                var image = new Avalonia.Controls.Image { Source = LoadAvatar(caseOption?.Name), Stretch = Stretch.UniformToFill };
                var face = new Border { Classes = { "lg-idol-avatar" }, Width = 42, Height = 42, CornerRadius = new CornerRadius(21), Padding = new Thickness(2), Child = new Border { CornerRadius = new CornerRadius(19), ClipToBounds = true, Child = image }, HorizontalAlignment = HorizontalAlignment.Center };
                var name = new TextBlock { FontSize = 11.5, HorizontalAlignment = HorizontalAlignment.Center, TextWrapping = TextWrapping.Wrap, TextAlignment = TextAlignment.Center };
                name.Bind(TextBlock.TextProperty, new Binding(nameof(MaaInterface.MaaInterfaceOptionCase.DisplayName)) { Source = caseOption });
                var tile = new StackPanel { Spacing = 5, Children = { face, name } };
                tile.Bind(ToolTip.TipProperty, new Binding(nameof(MaaInterface.MaaInterfaceOptionCase.DisplayName)) { Source = caseOption });
                return tile;
            }),
        };
        return CreateCaseSelector(option, interfaceOption, source, grid, stacked: true);
    }

    /// <summary>
    /// Shared single-choice selector for a select option: writes option.Index, renders the selected case's
    /// sub-options (existing values kept), and saves — the same semantics as the combo box path.
    /// </summary>
    private Control CreateCaseSelector(
        MaaInterface.MaaInterfaceSelectOption option,
        MaaInterface.MaaInterfaceOption interfaceOption,
        DragItemViewModel source,
        Avalonia.Controls.Primitives.SelectingItemsControl selector,
        bool stacked)
    {
        var wrapper = new StackPanel();
        var grid = CreateBaseGrid();
        // Restoring the saved selection makes the ListBox bring the selected item into view, which scrolled the
        // whole settings panel when it opened. Such requests are ignored until the user interacts with the selector.
        var userDriven = false;
        selector.AddHandler(InputElement.PointerPressedEvent, (_, _) => userDriven = true, Avalonia.Interactivity.RoutingStrategies.Tunnel, true);
        selector.AddHandler(InputElement.KeyDownEvent, (_, _) => userDriven = true, Avalonia.Interactivity.RoutingStrategies.Tunnel, true);
        selector.AddHandler(Control.RequestBringIntoViewEvent, (_, e) => { if (!userDriven) e.Handled = true; });
        var index = option.Index ?? 0;
        selector.SelectedIndex = interfaceOption.Cases != null && index >= 0 && index < interfaceOption.Cases.Count ? index : 0;
        BindIdleEnabled(selector);
        selector.Bind(Avalonia.Automation.AutomationProperties.NameProperty, new ResourceBindingWithFallback(option.DisplayName, option.Name));

        var subOptionsContainer = new StackPanel();
        void UpdateSubOptions(int selected)
        {
            subOptionsContainer.Children.Clear();
            if (interfaceOption.Cases == null || selected < 0 || selected >= interfaceOption.Cases.Count) return;
            var selectedCase = interfaceOption.Cases[selected];
            if (selectedCase.Option == null || selectedCase.Option.Count == 0) return;
            option.SubOptions ??= new List<MaaInterface.MaaInterfaceSelectOption>();
            foreach (var subOptionName in selectedCase.Option)
            {
                var existing = option.SubOptions.FirstOrDefault(o => o.Name == subOptionName);
                if (existing == null)
                {
                    existing = CreateDefaultSelectOption(subOptionName);
                    option.SubOptions.Add(existing);
                }
                AddSubOption(subOptionsContainer, existing, source);
            }
        }

        selector.SelectionChanged += (_, _) =>
        {
            if (selector.SelectedItem is not MaaInterface.MaaInterfaceOptionCase selectedCase || interfaceOption.Cases == null) return;
            var resolvedIndex = interfaceOption.Cases.FindIndex(c => c.Name == selectedCase.Name);
            if (resolvedIndex < 0 || resolvedIndex == option.Index) return;
            option.Index = resolvedIndex;
            UpdateSubOptions(resolvedIndex);
            saveConfigurationAction();
        };
        UpdateSubOptions(selector.SelectedIndex);

        var labelPanel = CreateLabelPanel(option.DisplayName, option.Name, interfaceOption.Description, interfaceOption.Document);
        var icon = CreateIcon(interfaceOption);
        icon.Margin = new Thickness(0, 0, 6, 0);
        labelPanel.Children.Insert(0, icon);
        if (stacked)
        {
            // The prototype uses a full-width avatar grid without a redundant heading.
            grid.ColumnDefinitions.Clear();
            grid.Children.Add(selector);
        }
        else
        {
            Grid.SetColumn(labelPanel, 0);
            Grid.SetColumn(selector, 1);
            AddResponsiveBehavior(grid, labelPanel, selector);
            grid.Children.Add(labelPanel);
            grid.Children.Add(selector);
        }
        wrapper.Children.Add(grid);

        var subOptionsBorder = new Border
        {
            Margin = new Thickness(0, 2, 0, 0),
            Child = option.Name?.Contains("上课", StringComparison.Ordinal) == true ? subOptionsContainer : new StackPanel { Children = { CreateSubDivider(), subOptionsContainer } }
        };
        subOptionsBorder.Bind(Visual.IsVisibleProperty, new Binding("Children.Count")
        {
            Source = subOptionsContainer,
            Converter = new FuncValueConverter<int, bool>(count => count > 0)
        });
        wrapper.Children.Add(subOptionsBorder);
        return wrapper;
    }

    // ... Methods for Advanced Options, Helper methods below ...

    private void AddAdvancedOption(Panel panel, MaaInterface.MaaInterfaceSelectAdvanced option)
    {
        if (MaaProcessor.Interface?.Advanced?.TryGetValue(option.Name, out var interfaceOption) != true) return;

        // Iterate fields
        for (int i = 0; interfaceOption.Field != null && i < interfaceOption.Field.Count; i++)
        {
            var field = interfaceOption.Field[i];
            var type = i < (interfaceOption.Type?.Count ?? 0) ? (interfaceOption.Type?[i] ?? "string") : (interfaceOption.Type?.Count > 0 ? interfaceOption.Type[0] : "string");

            // Resolve default value
            string defaultValue = string.Empty;
            if (option.Data.TryGetValue(field, out var value))
            {
                defaultValue = value;
            }
            else if (interfaceOption.Default != null && interfaceOption.Default.Count > i)
            {
                var defaultToken = interfaceOption.Default[i];
                defaultValue = defaultToken is JArray arr ? (arr.Count > 0 ? arr[0].ToString() : "") : defaultToken.ToString();
            }

            var grid = CreateBaseGrid();

            // AutoCompleteBox
            var autoCompleteBox = new AutoCompleteBox
            {
                MinWidth = 120,
                Margin = new Thickness(0, 2, 0, 2),
                Text = defaultValue,
                IsTextCompletionEnabled = true,
                FilterMode = AutoCompleteFilterMode.Custom,
                ItemFilter = (search, item) => 
                {
                    if (string.IsNullOrEmpty(search)) return true;
                    return item?.ToString()?.Contains(search, StringComparison.InvariantCultureIgnoreCase) ?? false;
                }
            };
            
            BindIdleEnabled(autoCompleteBox);
            
            // Completion Items
            if (interfaceOption.Default != null && interfaceOption.Default.Count > i)
            {
                var defaultToken = interfaceOption.Default[i];
                if (defaultToken is JArray arr)
                    autoCompleteBox.ItemsSource = arr.Select(item => item.ToString()).ToList();
                else
                    autoCompleteBox.ItemsSource = new List<string> { defaultToken.ToString(), string.Empty };
                
                if (autoCompleteBox.ItemsSource is System.Collections.IEnumerable items && items.Cast<object>().Any())
                      Interaction.GetBehaviors(autoCompleteBox).Add(new AutoCompleteBehavior());
            }

            // Events
            autoCompleteBox.TextChanged += (_, _) => HandleAdvancedInputChange(autoCompleteBox, option, field, type, interfaceOption);
            autoCompleteBox.SelectionChanged += (_, _) => 
            {
                if (autoCompleteBox.SelectedItem is string selectedText)
                {
                    autoCompleteBox.Text = selectedText;
                    // Change event will be fired by setting Text
                }
            };

            // Initial manual trigger if needed? No, Text is set.

            // Label
            var labelPanel = CreateLabelPanel(field, null, interfaceOption.Description, interfaceOption.Document, isResourceBinding: true);
            labelPanel.Margin = new Thickness(10, 0, 0, 0);

            Grid.SetColumn(labelPanel, 0);
            Grid.SetColumn(autoCompleteBox, 1);
            
            AddResponsiveBehavior(grid, labelPanel, autoCompleteBox);
            
            grid.Children.Add(labelPanel);
            grid.Children.Add(autoCompleteBox);
            panel.Children.Add(grid);
        }
    }

    private void AddSubOption(StackPanel container, MaaInterface.MaaInterfaceSelectOption subOption, DragItemViewModel source)
    {
        _depth++;
        try { AddSubOptionCore(container, subOption, source); }
        finally { _depth--; }
    }

    private void AddSubOptionCore(StackPanel container, MaaInterface.MaaInterfaceSelectOption subOption, DragItemViewModel source)
    {
         if (MaaProcessor.Interface?.Option?.TryGetValue(subOption.Name ?? string.Empty, out var subInterfaceOption) != true) return;
         
         if (MFAAvalonia.Views.UserControls.TaskTargetControl.Handles(subOption.Name))
        {
            var memoryControl = MFAAvalonia.Views.UserControls.TaskTargetControl.Create(subOption, () => {
                subOption.PipelineOverride = subInterfaceOption.GenerateProcessedPipeline(subOption.Data.Where(kv => kv.Value != null).ToDictionary(kv => kv.Key, kv => kv.Value!));
                saveConfigurationAction();
            });
            BindIdleEnabled(memoryControl);
            container.Children.Add(memoryControl);
            return;
        }
        Control control;
         if (subInterfaceOption.IsInput)
            control = CreateInputControl(subOption, subInterfaceOption);
         else if (subInterfaceOption.IsCheckbox)
            control = CreateCheckboxControl(subOption, subInterfaceOption, source);
         else if ((subInterfaceOption.IsSwitch && subInterfaceOption.Cases.ShouldSwitchButton(out var yes, out var no)) ||
                  subInterfaceOption.Cases.ShouldSwitchButton(out yes, out no))
            control = CreateToggleControl(subOption, yes, no, subInterfaceOption, source);
         else
            control = CreateComboBoxControl(subOption, subInterfaceOption, source);

         container.Children.Add(control);
    }

    // Helper Creation Methods

    private MFAAvalonia.Controls.Liquid.ResponsiveOptionGrid CreateBaseGrid()
    {
        var grid = new MFAAvalonia.Controls.Liquid.ResponsiveOptionGrid
        {
            ColumnDefinitions =
            {
                new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) },
                new ColumnDefinition { Width = GridLength.Auto }
            },
            ColumnSpacing = 12,
            Margin = new Thickness(10, 4, 4, 4)
        };
        return grid;
    }

    private MFAAvalonia.Controls.Liquid.ResponsiveOptionGrid CreateSpecialTaskGrid(bool isFirstRow = false)
    {
        var grid = CreateBaseGrid();
        grid.Margin = isFirstRow ? new Thickness(10, 6, 10, 6) : new Thickness(10, 3, 10, 3);
        return grid;
    }

    private StackPanel CreateLabelPanel(string displayName, string? name, string? description, List<string>? document = null, bool isResourceBinding = false, bool useI18n = false)
    {
        var stackPanel = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            MinWidth = 120,
            VerticalAlignment = VerticalAlignment.Center,
            HorizontalAlignment = HorizontalAlignment.Left
        };
        
        var textBlock = new TextBlock
        {
            FontSize = 13,
            FontWeight = _depth == 0 ? FontWeight.SemiBold : FontWeight.Normal,
            VerticalAlignment = VerticalAlignment.Center,
            HorizontalAlignment = HorizontalAlignment.Left,
            TextWrapping = TextWrapping.NoWrap,
            TextTrimming = TextTrimming.CharacterEllipsis
        };
        
        if (useI18n)
            textBlock.Bind(TextBlock.TextProperty, new I18nBinding(displayName));
        else if (isResourceBinding)
            textBlock.Bind(TextBlock.TextProperty, new ResourceBinding(displayName));
        else
            textBlock.Bind(TextBlock.TextProperty, new ResourceBindingWithFallback(displayName, name));

        textBlock.Bind(TextBlock.ForegroundProperty, new DynamicResourceExtension("LgTextMainBrush"));
        stackPanel.Children.Add(textBlock);
        
        var tooltipText = GetTooltipText(description, document);
        if (!string.IsNullOrWhiteSpace(tooltipText))
        {
            var docBlock = new TooltipBlock();
            docBlock.Bind(TooltipBlock.TooltipTextProperty, new ResourceBinding(tooltipText));
            docBlock.Margin = new Thickness(4, 0, 0, 0); // Spacing
            stackPanel.Children.Add(docBlock);
        }
        
        return stackPanel;
    }

    private static TextBlock CreateLabelText(string key)
    {
        var textBlock = new TextBlock
        {
            FontSize = 13,
            FontWeight = FontWeight.SemiBold,
            MinWidth = 120,
            VerticalAlignment = VerticalAlignment.Center,
            HorizontalAlignment = HorizontalAlignment.Left
        };
        textBlock.Bind(TextBlock.TextProperty, new I18nBinding(key));
        textBlock.Bind(TextBlock.ForegroundProperty, new DynamicResourceExtension("LgTextMainBrush"));
        return textBlock;
    }

    private DisplayIcon CreateIcon(MaaInterface.MaaInterfaceOption interfaceOption)
    {
        var iconDisplay = new DisplayIcon
        {
            IconSize = 20,
            Margin = new Thickness(0, 0, 6, 0),
            VerticalAlignment = VerticalAlignment.Center
        };
        iconDisplay.Bind(DisplayIcon.IconSourceProperty, new Binding(nameof(MaaInterface.MaaInterfaceOption.ResolvedIcon)) { Source = interfaceOption });
        iconDisplay.Bind(Visual.IsVisibleProperty, new Binding(nameof(MaaInterface.MaaInterfaceOption.HasIcon)) { Source = interfaceOption });
        return iconDisplay;
    }

    private Control CreateOptionHeader(MaaInterface.MaaInterfaceOption interfaceOption)
    {
        var headerPanel = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            VerticalAlignment = VerticalAlignment.Center,
            Margin = new Thickness(0, 4, 0, 4)
        };

        var icon = CreateIcon(interfaceOption);
        icon.Margin = new Thickness(0, 0, 6, 0);
        headerPanel.Children.Add(icon);

        var headerText = new TextBlock
        {
            FontSize = 14,
        };
        headerText.Bind(TextBlock.TextProperty, new ResourceBindingWithFallback(interfaceOption.DisplayName, interfaceOption.Name));
        headerText.Bind(TextBlock.ForegroundProperty, new DynamicResourceExtension("SukiLowText"));
        headerPanel.Children.Add(headerText);

        // 添加 TooltipBlock 显示 Option 级别的 Description
        var tooltipText = GetTooltipText(interfaceOption.Description, interfaceOption.Document);
        if (!string.IsNullOrWhiteSpace(tooltipText))
        {
            var docBlock = new TooltipBlock();
            docBlock.Bind(TooltipBlock.TooltipTextProperty, new ResourceBinding(tooltipText));
            docBlock.Margin = new Thickness(4, 0, 0, 0);
            headerPanel.Children.Add(docBlock);
        }

        return headerPanel;
    }

    private void BindIdleEnabled(Control control)
    {
        control.Bind(Control.IsEnabledProperty, new Binding("Idle") { Source = Instances.RootViewModel });
    }

    private static void AddResponsiveBehavior(MFAAvalonia.Controls.Liquid.ResponsiveOptionGrid grid, Control label, Control input)
        => grid.Configure(label, input);

    // Logic Helpers

    private void HandleStringInputChange(TextBox textBox, MaaInterface.MaaInterfaceOptionInput input, MaaInterface.MaaInterfaceSelectOption option, MaaInterface.MaaInterfaceOption interfaceOption, bool silent = false)
    {
        var text = textBox.Text ?? string.Empty;
        var pipelineType = input.PipelineType?.ToLower() ?? "string";

        if (pipelineType == "int" && !IsValidIntegerInput(text))
        {
            var oldIdx = textBox.CaretIndex;
            textBox.Text = FilterToInteger(text);
            if (oldIdx <= textBox.Text.Length) textBox.CaretIndex = oldIdx;
            return;
        }

        if (!string.IsNullOrEmpty(input.Verify))
        {
             // Regex validation... logic as before
        }

        // 验证输入并显示红框
        var validation = interfaceOption.ValidateInput(input.Name ?? string.Empty, text);
        if (validation.IsValid)
            textBox.Bind(TextBox.BorderBrushProperty, new DynamicResourceExtension("SukiControlBorderBrush"));
        else
            textBox.BorderBrush = Brushes.Red;

        if (!silent)
        {
            option.Data[input.Name] = text == "null" ? MaaInterface.MaaInterfaceOption.ExplicitNullMarker : text;
            UpdatePipeline(option, interfaceOption);
            saveConfigurationAction();
        }
    }
    
    private void HandleAdvancedInputChange(AutoCompleteBox box, MaaInterface.MaaInterfaceSelectAdvanced option, string field, string type, MaaInterfaceAdvancedOption interfaceOption)
    {
        if (type.ToLower() == "int" && !IsValidIntegerInput(box.Text))
                box.Text = FilterToInteger(box.Text);

        option.Data[field] = box.Text;
        option.PipelineOverride = interfaceOption.GenerateProcessedPipeline(option.Data);
        saveConfigurationAction();
    }
    
    private void UpdatePipeline(MaaInterface.MaaInterfaceSelectOption option, MaaInterface.MaaInterfaceOption interfaceOption)
    {
        if (interfaceOption.PipelineOverride != null && option.Data != null)
        {
             option.PipelineOverride = interfaceOption.GenerateProcessedPipeline(
                 option.Data.Where(kv => kv.Value != null).ToDictionary(kv => kv.Key, kv => kv.Value!));
        }
    }

    private static MaaInterface.MaaInterfaceSelectOption CreateDefaultSelectOption(string optionName)
    {
        var selectOption = new MaaInterface.MaaInterfaceSelectOption { Name = optionName, Index = 0 };
        if (MaaProcessor.Interface?.Option?.TryGetValue(optionName, out var interfaceOption) == true)
        {
            if (interfaceOption.IsInput && interfaceOption.Inputs != null)
            {
                selectOption.Data = new Dictionary<string, string?>();
                foreach(var input in interfaceOption.Inputs)
                    if (!string.IsNullOrEmpty(input.Name))
                        selectOption.Data[input.Name] = input.Default ?? string.Empty;
            }
            else if (interfaceOption.IsCheckbox)
            {
                // 任务 9：checkbox 类型初始化 SelectedCases from DefaultCases
                selectOption.SelectedCases = new List<string>(interfaceOption.DefaultCases ?? new List<string>());
            }
            else if (!string.IsNullOrEmpty(interfaceOption.DefaultCase) && interfaceOption.Cases != null)
            {
                var idx = interfaceOption.Cases.FindIndex(c => c.Name == interfaceOption.DefaultCase);
                if (idx >= 0) selectOption.Index = idx;
            }
        }
        return selectOption;
    }

    private static string? GetTooltipText(string? description, List<string>? document)
    {
         if (!string.IsNullOrWhiteSpace(description))
         {
             var result = description.ResolveContentAsync(transform: false).GetAwaiter().GetResult();
             // 如果结果与输入相同（未被解析），尝试通过 resx i18n 系统解析（支持特殊任务描述等）
             if (result == description)
             {
                 var localized = description.ToLocalization();
                 if (localized != description)
                     return localized;
             }
             return result;
         }
             
         if (document is { Count: > 0 })
         {
             try { return LanguageHelper.GetLocalizedString(Regex.Unescape(string.Join("\\n", document))); }
             catch { return string.Join("\n", document); }
         }
         return null;
    }
    
    // Utils
    private bool IsValidIntegerInput(string text)
    {
        if (string.IsNullOrEmpty(text) || text == "-") return true;
        if (text.StartsWith("-") && (text.Length == 1 || (!char.IsDigit(text[1])))) return false;
        return text.All(c => c == '-' || char.IsDigit(c)) && text.Count(c => c == '-') <= 1 && text.LastIndexOf('-') <= 0;
    }
    private string FilterToInteger(string text)
    {
         var filtered = new string(text.Where(c => c == '-' || char.IsDigit(c)).ToArray());
         if (filtered.Contains('-') && (filtered[0] != '-' || filtered.Count(c => c == '-') > 1))
             filtered = filtered.Replace("-", "");
         if (string.IsNullOrEmpty(filtered) || filtered == "-") return filtered;
         return filtered.Length > 1 && filtered[0] == '0' ? filtered.TrimStart('0') : filtered; 
    }
    
    private void SetupComboBoxTemplate(ComboBox comboBox)
    {
        // ... Copy ItemTemplate and SelectionBoxItemTemplate from original ...
        // Simplification for brevity in this step, but fully implemented in real code
        
        // IMPORTANT: Copying the complex DataTemplates for ComboBox items (Icons + MarqueeText/TextBlock + Tooltip)
        // I will implement a helper to create the DataTemplate to avoid duplicating code between ItemTemplate and SelectionBoxItemTemplate
        comboBox.ItemTemplate = new FuncDataTemplate<MaaInterface.MaaInterfaceOptionCase>((caseOption, _) => CreateComboBoxItemContent(caseOption, true));
        comboBox.SelectionBoxItemTemplate = new FuncDataTemplate<MaaInterface.MaaInterfaceOptionCase>((caseOption, _) => CreateComboBoxItemContent(caseOption, false));
    }
    
    private Control CreateComboBoxItemContent(MaaInterface.MaaInterfaceOptionCase? caseOption, bool isMarquee)
    {
         var grid = new Grid
         {
             ColumnDefinitions =
             {
                 new ColumnDefinition { Width = GridLength.Auto },
                 new ColumnDefinition { Width = GridLength.Star },
                 new ColumnDefinition { Width = GridLength.Auto }
             },
             HorizontalAlignment = HorizontalAlignment.Stretch
         };
         
         var iconDisplay = new DisplayIcon { IconSize = 20, Margin = new Thickness(0,0,6,0), VerticalAlignment = VerticalAlignment.Center };
         iconDisplay.Bind(DisplayIcon.IconSourceProperty, new Binding(nameof(MaaInterface.MaaInterfaceOptionCase.ResolvedIcon)) { Source = caseOption });
         iconDisplay.Bind(Visual.IsVisibleProperty, new Binding(nameof(MaaInterface.MaaInterfaceOptionCase.HasIcon)) { Source = caseOption });
         Grid.SetColumn(iconDisplay, 0);

         Control textControl;
         if (isMarquee)
         {
             var marquee = new MarqueeTextBlock { VerticalContentAlignment = VerticalAlignment.Center };
             marquee.Bind(MarqueeTextBlock.ForegroundProperty, new DynamicResourceExtension("SukiText"));
             marquee.Bind(MarqueeTextBlock.TextProperty, new Binding(nameof(MaaInterface.MaaInterfaceOptionCase.DisplayName)) { Source = caseOption });
             marquee.Bind(ToolTip.TipProperty, new Binding(nameof(MaaInterface.MaaInterfaceOptionCase.DisplayName)) { Source = caseOption });
             ToolTip.SetShowDelay(marquee, 100);
             textControl = marquee;
         }
         else
         {
             var tb = new TextBlock { TextTrimming = TextTrimming.WordEllipsis, TextWrapping = TextWrapping.NoWrap, VerticalAlignment = VerticalAlignment.Center };
             tb.Bind(TextBlock.ForegroundProperty, new DynamicResourceExtension("SukiText"));
             tb.Bind(TextBlock.TextProperty, new Binding(nameof(MaaInterface.MaaInterfaceOptionCase.DisplayName)) { Source = caseOption });
             tb.Bind(ToolTip.TipProperty, new Binding(nameof(MaaInterface.MaaInterfaceOptionCase.DisplayName)) { Source = caseOption });
             textControl = tb;
         }
         Grid.SetColumn(textControl, 1);
         
         // 始终创建 TooltipBlock，由绑定控制可见性，避免模板回收阶段 caseOption 为空时空引用
         var tooltipBlock = new TooltipBlock { Margin = new Thickness(4, 0, 0, 0) };
         tooltipBlock.Bind(TooltipBlock.TooltipTextProperty, new Binding(nameof(MaaInterface.MaaInterfaceOptionCase.DisplayDescription)) { Source = caseOption });
         tooltipBlock.Bind(Visual.IsVisibleProperty, new Binding(nameof(MaaInterface.MaaInterfaceOptionCase.HasDescription)) { Source = caseOption });
         Grid.SetColumn(tooltipBlock, 2);
         grid.Children.Add(tooltipBlock);
         
         grid.Children.Add(iconDisplay);
         grid.Children.Add(textControl);
         
         return grid;
    }

    #region 特殊任务选项面板

    /// <summary>
    /// 获取特殊任务当前的 custom_action_param JObject
    /// </summary>
    private static JObject GetActionParam(DragItemViewModel dragItem)
    {
        var entry = dragItem.InterfaceItem?.Entry;
        if (string.IsNullOrEmpty(entry) || dragItem.InterfaceItem?.PipelineOverride == null)
            return new JObject();

        if (dragItem.InterfaceItem.PipelineOverride.TryGetValue(entry, out var node) && node is JObject obj)
        {
            var paramToken = obj["custom_action_param"];
            if (paramToken is JObject paramObj)
                return paramObj;
            // 兼容旧版序列化为字符串的情况
            if (paramToken is JValue { Type: JTokenType.String } strVal)
            {
                try { return JObject.Parse((string)strVal!); }
                catch { return new JObject(); }
            }
        }
        return AddTaskDialogViewModel.GetDefaultActionParam(entry);
    }

    /// <summary>
    /// 更新特殊任务的 custom_action_param
    /// </summary>
    private void UpdateActionParam(DragItemViewModel dragItem, JObject param)
    {
        var entry = dragItem.InterfaceItem?.Entry;
        if (string.IsNullOrEmpty(entry) || dragItem.InterfaceItem?.PipelineOverride == null) return;

        if (dragItem.InterfaceItem.PipelineOverride.TryGetValue(entry, out var node) && node is JObject obj)
        {
            obj["custom_action_param"] = param;
        }
        saveConfigurationAction();
    }

    /// <summary>
    /// 根据 Entry 分发到对应的特殊任务选项面板生成方法
    /// </summary>
    private void GenerateSpecialTaskPanelContent(StackPanel panel, DragItemViewModel dragItem)
    {
        var entry = dragItem.InterfaceItem?.Entry;
        if (string.IsNullOrEmpty(entry)) return;

        switch (entry)
        {
            case "CountdownAction":
                AddCountdownOptions(panel, dragItem);
                break;
            case "TimedWaitAction":
                AddTimedWaitOptions(panel, dragItem);
                break;
            case "SystemNotificationAction":
                AddSystemNotificationOptions(panel, dragItem);
                break;
            case "CustomProgramAction":
                AddCustomProgramOptions(panel, dragItem);
                break;
            case "KillProcessAction":
                AddKillProcessOptions(panel, dragItem);
                break;
            case "ComputerOperationAction":
                AddComputerOperationOptions(panel, dragItem);
                break;
            case "WebhookAction":
                AddWebhookOptions(panel, dragItem);
                break;
        }
    }

    /// <summary>
    /// 倒计时 - seconds (NumericUpDown)
    /// </summary>
    private void AddCountdownOptions(StackPanel panel, DragItemViewModel dragItem)
    {
        var param = GetActionParam(dragItem);
        var grid = CreateSpecialTaskGrid(isFirstRow: true);

        var label = CreateLabelPanel(LangKeys.SpecialTask_CountdownSeconds, null, null, useI18n: true);
        label.Margin = new Thickness(10, 0, 0, 0);
        Grid.SetColumn(label, 0);
        grid.Children.Add(label);

        var numericUpDown = new NumericUpDown
        {
            Value = (int?)param["seconds"] ?? 60,
            Minimum = 1,
            Maximum = 86400,
            Increment = 1,
            MinWidth = 120,
            Margin = new Thickness(0, 2, 0, 2),
            VerticalAlignment = VerticalAlignment.Center,
            HorizontalAlignment = HorizontalAlignment.Stretch,
            Classes = { "TaskOptionLikeCombo" },
        };
        BindIdleEnabled(numericUpDown);
        numericUpDown.ValueChanged += (_, _) =>
        {
            param["seconds"] = Convert.ToInt32(numericUpDown.Value);
            UpdateActionParam(dragItem, param);
        };

        Grid.SetColumn(numericUpDown, 1);
        AddResponsiveBehavior(grid, label, numericUpDown);
        grid.Children.Add(numericUpDown);
        panel.Children.Add(grid);
    }

    /// <summary>
    /// 定时等待 - 等待到指定时间 (hour:minute)，使用 TimePicker 控件
    /// </summary>
    private void AddTimedWaitOptions(StackPanel panel, DragItemViewModel dragItem)
    {
        var param = GetActionParam(dragItem);
        var grid = CreateSpecialTaskGrid(isFirstRow: true);

        var label = CreateLabelPanel(LangKeys.SpecialTask_WaitUntilTime, null, null, useI18n: true);
        label.Margin = new Thickness(10, 0, 0, 0);
        Grid.SetColumn(label, 0);
        grid.Children.Add(label);

        var hour = (int?)param["hour"] ?? 0;
        var minute = (int?)param["minute"] ?? 0;

        var timePicker = new TimePicker
        {
            ClockIdentifier = "24HourClock",
            SelectedTime = new TimeSpan(hour, minute, 0),
            MinWidth = 120,
            Margin = new Thickness(0, 2, 0, 2),
            VerticalAlignment = VerticalAlignment.Center,
            HorizontalAlignment = HorizontalAlignment.Stretch,
            Classes = { "TaskOptionLikeCombo" },
        };
        BindIdleEnabled(timePicker);

        timePicker.SelectedTimeChanged += (_, _) =>
        {
            var time = timePicker.SelectedTime ?? TimeSpan.Zero;
            param["hour"] = time.Hours;
            param["minute"] = time.Minutes;
            UpdateActionParam(dragItem, param);
        };

        Grid.SetColumn(timePicker, 1);
        AddResponsiveBehavior(grid, label, timePicker);
        grid.Children.Add(timePicker);
        panel.Children.Add(grid);
    }

    /// <summary>
    /// 系统通知 - title + message (TextBox)
    /// </summary>
    private void AddSystemNotificationOptions(StackPanel panel, DragItemViewModel dragItem)
    {
        var param = GetActionParam(dragItem);

        // 标题
        var grid1 = CreateSpecialTaskGrid(isFirstRow: true);
        var label1 = CreateLabelPanel(LangKeys.SpecialTask_NotificationTitle, null, null, useI18n: true);
        label1.Margin = new Thickness(10, 0, 0, 0);
        Grid.SetColumn(label1, 0);
        grid1.Children.Add(label1);

        var titleBox = new TextBox
        {
            Text = (string?)param["title"] ?? "MFAAvalonia",
            MinWidth = 120,
            Margin = new Thickness(0, 2, 0, 2),
            VerticalAlignment = VerticalAlignment.Center,
        };
        BindIdleEnabled(titleBox);
        titleBox.TextChanged += (_, _) =>
        {
            param["title"] = titleBox.Text ?? "";
            UpdateActionParam(dragItem, param);
        };
        Grid.SetColumn(titleBox, 1);
        AddResponsiveBehavior(grid1, label1, titleBox);
        grid1.Children.Add(titleBox);
        panel.Children.Add(grid1);

        // 内容
        var grid2 = CreateSpecialTaskGrid();
        var label2 = CreateLabelPanel(LangKeys.SpecialTask_NotificationContent, null, null, useI18n: true);
        label2.Margin = new Thickness(10, 0, 0, 0);
        Grid.SetColumn(label2, 0);
        grid2.Children.Add(label2);

        var messageBox = new TextBox
        {
            Text = (string?)param["message"] ?? "",
            MinWidth = 120,
            Margin = new Thickness(0, 2, 0, 2),
            VerticalAlignment = VerticalAlignment.Center,
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            MaxHeight = 100,
        };
        BindIdleEnabled(messageBox);
        messageBox.TextChanged += (_, _) =>
        {
            param["message"] = messageBox.Text ?? "";
            UpdateActionParam(dragItem, param);
        };
        Grid.SetColumn(messageBox, 1);
        AddResponsiveBehavior(grid2, label2, messageBox);
        grid2.Children.Add(messageBox);
        panel.Children.Add(grid2);
    }

    /// <summary>
    /// 自定义程序 - program (TextBox+拖拽+文件选择), arguments (TextBox), wait_for_exit (ToggleSwitch)
    /// </summary>
    private void AddCustomProgramOptions(StackPanel panel, DragItemViewModel dragItem)
    {
        var param = GetActionParam(dragItem);

        // 程序路径
        var grid1 = CreateSpecialTaskGrid(isFirstRow: true);
        var label1 = CreateLabelPanel(LangKeys.SpecialTask_ProgramPath, null, null, useI18n: true);
        label1.Margin = new Thickness(10, 0, 0, 0);
        Grid.SetColumn(label1, 0);
        grid1.Children.Add(label1);

        var programBox = new TextBox
        {
            Text = (string?)param["program"] ?? "",
            Margin = new Thickness(0, 2, 0, 2),
            MinWidth = 120,
            VerticalAlignment = VerticalAlignment.Center,
        };
        BindIdleEnabled(programBox);

        // InnerRightContent: 文件选择按钮（参考 StartSettings 游戏路径样式）
        var browseBtn = new Button
        {
            BorderBrush = Brushes.Transparent,
            BorderThickness = new Thickness(0),
            Height = 20,
            Width = 30,
            Padding = new Thickness(-4, 0, 0, 0),
            Content = new FluentIcons.Avalonia.Fluent.FluentIcon()
            {
                Icon = FluentIcons.Common.Icon.FolderArrowLeft,
                IconSize = FluentIcons.Common.IconSize.Size16,
            },
        };
        browseBtn.Click += async (_, _) =>
        {
            var topLevel = TopLevel.GetTopLevel(panel);
            if (topLevel?.StorageProvider == null) return;
            var result = await topLevel.StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions
            {
                Title = LangKeys.SpecialTask_SelectProgram.ToLocalization(),
                AllowMultiple = false,
            });
            if (result.Count > 0)
            {
                programBox.Text = result[0].Path.LocalPath;
            }
        };
        programBox.InnerRightContent = browseBtn;

        // 支持拖拽
        DragDrop.SetAllowDrop(programBox, true);
        programBox.AddHandler(DragDrop.DropEvent, (_, e) =>
        {
#pragma warning disable CS0618
            if (e.Data.GetFiles() is { } files)
#pragma warning restore CS0618
            {
                var file = files.FirstOrDefault();
                if (file != null)
                {
                    programBox.Text = file.Path.LocalPath;
                }
            }
        });
        programBox.AddHandler(DragDrop.DragOverEvent, (_, e) =>
        {
            e.DragEffects = DragDropEffects.Copy;
        });

        programBox.TextChanged += (_, _) =>
        {
            param["program"] = programBox.Text ?? "";
            UpdateActionParam(dragItem, param);
        };

        Grid.SetColumn(programBox, 1);
        AddResponsiveBehavior(grid1, label1, programBox);
        grid1.Children.Add(programBox);
        panel.Children.Add(grid1);

        // 附加参数
        var grid2 = CreateSpecialTaskGrid();
        var label2 = CreateLabelPanel(LangKeys.SpecialTask_Arguments, null, null, useI18n: true);
        label2.Margin = new Thickness(10, 0, 0, 0);
        Grid.SetColumn(label2, 0);
        grid2.Children.Add(label2);

        var argsBox = new TextBox
        {
            Text = (string?)param["arguments"] ?? "",
            MinWidth = 120,
            Margin = new Thickness(0, 2, 0, 2),
            VerticalAlignment = VerticalAlignment.Center,
        };
        BindIdleEnabled(argsBox);
        argsBox.TextChanged += (_, _) =>
        {
            param["arguments"] = argsBox.Text ?? "";
            UpdateActionParam(dragItem, param);
        };
        Grid.SetColumn(argsBox, 1);
        AddResponsiveBehavior(grid2, label2, argsBox);
        grid2.Children.Add(argsBox);
        panel.Children.Add(grid2);

        // 等待退出
        var grid3 = new Grid
        {
            ColumnDefinitions =
            {
                new ColumnDefinition { Width = GridLength.Auto },
                new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) },
                new ColumnDefinition { Width = GridLength.Auto },
            },
            Margin = new Thickness(10, 3, 10, 3),
        };
        var label3 = CreateLabelPanel(LangKeys.SpecialTask_WaitForExit, null, null, useI18n: true);
        label3.Margin = new Thickness(10, 0, 5, 0);
        Grid.SetColumn(label3, 0);
        grid3.Children.Add(label3);

        var waitToggle = new ToggleSwitch
        {
            IsChecked = (bool?)param["wait_for_exit"] ?? false,
            Classes = { "Switch" },
            MaxHeight = 60,
            MaxWidth = 100,
            HorizontalAlignment = HorizontalAlignment.Right,
            VerticalAlignment = VerticalAlignment.Center,
        };
        BindIdleEnabled(waitToggle);
        waitToggle.IsCheckedChanged += (_, _) =>
        {
            param["wait_for_exit"] = waitToggle.IsChecked == true;
            UpdateActionParam(dragItem, param);
        };
        Grid.SetColumn(waitToggle, 2);
        grid3.Children.Add(waitToggle);
        panel.Children.Add(grid3);
    }

    /// <summary>
    /// 结束进程 - kill_self_process (ToggleSwitch), process_name (TextBox)
    /// </summary>
    private void AddKillProcessOptions(StackPanel panel, DragItemViewModel dragItem)
    {
        var param = GetActionParam(dragItem);
        var processName = (string?)param["process_name"] ?? string.Empty;
        var killSelfProcess = (bool?)param["kill_self_process"] ?? string.IsNullOrWhiteSpace(processName);

        var wrapper = new StackPanel();
        var grid = CreateSpecialTaskGrid(isFirstRow: true);

        var labelPanel = CreateLabelPanel(LangKeys.SpecialTask_KillSelfProcess, null, LangKeys.SpecialTask_KillSelfProcessDesc, useI18n: true);
        Grid.SetColumn(labelPanel, 0);
        grid.Children.Add(labelPanel);

        var toggleSwitch = new ToggleSwitch
        {
            IsChecked = killSelfProcess,
            Classes = { "Switch" },
            MaxHeight = 60,
            MaxWidth = 100,
            HorizontalAlignment = HorizontalAlignment.Right,
            VerticalAlignment = VerticalAlignment.Center,
        };
        BindIdleEnabled(toggleSwitch);
        Grid.SetColumn(toggleSwitch, 2);
        grid.Children.Add(toggleSwitch);
        wrapper.Children.Add(grid);

        var subOptionsContainer = new StackPanel();

        void UpdateSubOptions(bool isKillSelf)
        {
            subOptionsContainer.Children.Clear();
            if (isKillSelf)
                return;

            var processGrid = CreateSpecialTaskGrid();
            processGrid.Margin = new Thickness(0, 0, 0, 0);

            var processLabel = CreateLabelPanel(LangKeys.SpecialTask_ProcessName, null, null, useI18n: true);
            Grid.SetColumn(processLabel, 0);
            processGrid.Children.Add(processLabel);

            var textBox = new TextBox
            {
                Text = (string?)param["process_name"] ?? string.Empty,
                MinWidth = 120,
                Margin = new Thickness(0, 2, 0, 2),
                VerticalAlignment = VerticalAlignment.Center,
                Watermark = LangKeys.SpecialTask_ProcessNamePlaceholder.ToLocalization(),
            };
            BindIdleEnabled(textBox);
            textBox.TextChanged += (_, _) =>
            {
                param["process_name"] = textBox.Text ?? string.Empty;
                UpdateActionParam(dragItem, param);
            };

            Grid.SetColumn(textBox, 1);
            AddResponsiveBehavior(processGrid, processLabel, textBox);
            processGrid.Children.Add(textBox);
            subOptionsContainer.Children.Add(processGrid);
        }

        toggleSwitch.IsCheckedChanged += (_, _) =>
        {
            var isKillSelf = toggleSwitch.IsChecked == true;
            param["kill_self_process"] = isKillSelf;
            UpdateActionParam(dragItem, param);
            UpdateSubOptions(isKillSelf);
        };

        UpdateSubOptions(killSelfProcess);

        var subOptionsBorder = new Border
        {
            BorderThickness = new Thickness(2, 0, 0, 0),
            Background = Brushes.Transparent,
            Margin = new Thickness(24, 0, 0, 4),
            Padding = new Thickness(8, 0, 0, 0),
            Child = subOptionsContainer
        };
        subOptionsBorder.Bind(Border.BorderBrushProperty, new DynamicResourceExtension("SukiPrimaryColor"));
        subOptionsBorder.Bind(Visual.IsVisibleProperty, new Binding("Children.Count")
        {
            Source = subOptionsContainer,
            Converter = new FuncValueConverter<int, bool>(count => count > 0)
        });

        wrapper.Children.Add(subOptionsBorder);
        panel.Children.Add(wrapper);
    }

    /// <summary>
    /// 电脑操作 - operation (ComboBox: shutdown/restart/sleep/hibernate)
    /// </summary>
    private void AddComputerOperationOptions(StackPanel panel, DragItemViewModel dragItem)
    {
        var param = GetActionParam(dragItem);
        var grid = CreateSpecialTaskGrid(isFirstRow: true);

        var label = CreateLabelPanel(LangKeys.SpecialTask_OperationType, null, null, useI18n: true);
        label.Margin = new Thickness(10, 0, 0, 0);
        Grid.SetColumn(label, 0);
        grid.Children.Add(label);

        var operations = new List<string> { "shutdown", "restart", "sleep", "hibernate" };
        var displayNames = new List<string>
        {
            LangKeys.SpecialTask_Shutdown.ToLocalization(),
            LangKeys.SpecialTask_Restart.ToLocalization(),
            LangKeys.SpecialTask_Sleep.ToLocalization(),
            LangKeys.SpecialTask_Hibernate.ToLocalization(),
        };

        var comboBox = new ComboBox
        {
            MinWidth = 120,
            Margin = new Thickness(0, 2, 0, 2),
            ItemsSource = displayNames,
            SelectedIndex = Math.Max(0, operations.IndexOf((string?)param["operation"] ?? "shutdown")),
            HorizontalAlignment = HorizontalAlignment.Stretch,
        };
        BindIdleEnabled(comboBox);
        comboBox.SelectionChanged += (_, _) =>
        {
            if (comboBox.SelectedIndex >= 0 && comboBox.SelectedIndex < operations.Count)
            {
                param["operation"] = operations[comboBox.SelectedIndex];
                UpdateActionParam(dragItem, param);
            }
        };

        Grid.SetColumn(comboBox, 1);
        AddResponsiveBehavior(grid, label, comboBox);
        grid.Children.Add(comboBox);
        panel.Children.Add(grid);
    }

    /// <summary>
    /// Webhook - url, method (GET/POST), body, content_type
    /// </summary>
    private void AddWebhookOptions(StackPanel panel, DragItemViewModel dragItem)
    {
        var param = GetActionParam(dragItem);

        // URL
        var grid1 = CreateSpecialTaskGrid(isFirstRow: true);
        var label1 = CreateLabelPanel("URL", null, null);
        label1.Margin = new Thickness(10, 0, 0, 0);
        Grid.SetColumn(label1, 0);
        grid1.Children.Add(label1);

        var urlBox = new TextBox
        {
            Text = (string?)param["url"] ?? "",
            MinWidth = 120,
            Margin = new Thickness(0, 2, 0, 2),
            VerticalAlignment = VerticalAlignment.Center,
            Watermark = "https://example.com/webhook",
        };
        BindIdleEnabled(urlBox);
        urlBox.TextChanged += (_, _) =>
        {
            param["url"] = urlBox.Text ?? "";
            UpdateActionParam(dragItem, param);
        };
        Grid.SetColumn(urlBox, 1);
        AddResponsiveBehavior(grid1, label1, urlBox);
        grid1.Children.Add(urlBox);
        panel.Children.Add(grid1);

        // Method
        var grid2 = CreateSpecialTaskGrid();
        var label2 = CreateLabelPanel(LangKeys.SpecialTask_RequestMethod, null, null, useI18n: true);
        label2.Margin = new Thickness(10, 0, 0, 0);
        Grid.SetColumn(label2, 0);
        grid2.Children.Add(label2);

        var methods = new List<string> { "GET", "POST" };
        var methodCombo = new ComboBox
        {
            MinWidth = 120,
            Margin = new Thickness(0, 2, 0, 2),
            ItemsSource = methods,
            SelectedIndex = Math.Max(0, methods.IndexOf((string?)param["method"] ?? "GET")),
            HorizontalAlignment = HorizontalAlignment.Stretch,
        };
        BindIdleEnabled(methodCombo);
        methodCombo.SelectionChanged += (_, _) =>
        {
            if (methodCombo.SelectedIndex >= 0)
            {
                param["method"] = methods[methodCombo.SelectedIndex];
                UpdateActionParam(dragItem, param);
            }
        };
        Grid.SetColumn(methodCombo, 1);
        AddResponsiveBehavior(grid2, label2, methodCombo);
        grid2.Children.Add(methodCombo);
        panel.Children.Add(grid2);

        // Body
        var grid3 = CreateSpecialTaskGrid();
        var label3 = CreateLabelPanel(LangKeys.SpecialTask_RequestBody, null, null, useI18n: true);
        label3.Margin = new Thickness(10, 0, 0, 0);
        Grid.SetColumn(label3, 0);
        grid3.Children.Add(label3);

        var bodyBox = new TextBox
        {
            Text = (string?)param["body"] ?? "",
            MinWidth = 120,
            Margin = new Thickness(0, 2, 0, 2),
            VerticalAlignment = VerticalAlignment.Center,
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            MaxHeight = 100,
        };
        BindIdleEnabled(bodyBox);
        bodyBox.TextChanged += (_, _) =>
        {
            param["body"] = bodyBox.Text ?? "";
            UpdateActionParam(dragItem, param);
        };
        Grid.SetColumn(bodyBox, 1);
        AddResponsiveBehavior(grid3, label3, bodyBox);
        grid3.Children.Add(bodyBox);
        panel.Children.Add(grid3);

        // Content-Type
        var grid4 = CreateSpecialTaskGrid();
        var label4 = CreateLabelPanel("Content-Type", null, null);
        label4.Margin = new Thickness(10, 0, 0, 0);
        Grid.SetColumn(label4, 0);
        grid4.Children.Add(label4);

        var ctBox = new TextBox
        {
            Text = (string?)param["content_type"] ?? "application/json",
            MinWidth = 120,
            Margin = new Thickness(0, 2, 0, 2),
            VerticalAlignment = VerticalAlignment.Center,
        };
        BindIdleEnabled(ctBox);
        ctBox.TextChanged += (_, _) =>
        {
            param["content_type"] = ctBox.Text ?? "";
            UpdateActionParam(dragItem, param);
        };
        Grid.SetColumn(ctBox, 1);
        AddResponsiveBehavior(grid4, label4, ctBox);
        grid4.Children.Add(ctBox);
        panel.Children.Add(grid4);
    }

    #endregion
}






