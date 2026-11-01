using MFAAvalonia.Helper.Converters;
using SukiUI.Enums;
using System;

namespace MFAAvalonia.Configuration;

/// <summary>
/// One-time carry-over of the v2.1.5 Suki background settings to their 2.2.0 replacements. v2.1.5 only
/// wrote these keys when the user changed them, so a present key is an explicit choice; absent keys keep the
/// new defaults. Runs before any reader and stores the results under the new keys; old keys are left intact.
/// <list type="bullet">
/// <item>BackgroundStyle = Flat → 背景光晕 off (any other style → on)</item>
/// <item>BackgroundAnimations → 光晕流动</item>
/// <item>BackgroundTransitions = off → 减少动态效果 on (and vice versa)</item>
/// </list>
/// </summary>
public static class LiquidAppearanceMigration
{
    public static void Apply(MFAConfiguration config)
    {
        if (!config.ContainsKey(ConfigurationKeys.LiquidAmbient) && config.ContainsKey(ConfigurationKeys.BackgroundStyle))
        {
            var style = config.GetValue(ConfigurationKeys.BackgroundStyle, SukiBackgroundStyle.GradientSoft, SukiBackgroundStyle.GradientSoft, new UniversalEnumConverter<SukiBackgroundStyle>());
            config.SetValue(ConfigurationKeys.LiquidAmbient, style != SukiBackgroundStyle.Flat);
        }
        if (!config.ContainsKey(ConfigurationKeys.LiquidAmbientAnimate) && config.ContainsKey(ConfigurationKeys.BackgroundAnimations))
            config.SetValue(ConfigurationKeys.LiquidAmbientAnimate, config.GetValue(ConfigurationKeys.BackgroundAnimations, false));
        if (!config.ContainsKey(ConfigurationKeys.LiquidReduceMotion) && config.ContainsKey(ConfigurationKeys.BackgroundTransitions))
            config.SetValue(ConfigurationKeys.LiquidReduceMotion, !config.GetValue(ConfigurationKeys.BackgroundTransitions, false));
    }
}
