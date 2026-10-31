using MFAAvalonia.Configuration;
using MFAAvalonia.Helper;
using MFAAvalonia.Helper.ValueType;
using Newtonsoft.Json;
using System;
using System.Collections.Generic;
using System.Linq;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// Loads and saves the shared workbench layout. The 2.1.x grid layout keys are read once
/// for migration and left untouched, so returning to an older client still finds them.
/// </summary>
public static class BoardLayoutStore
{
    private const string LegacyKey = ConfigurationKeys.DashboardCardGridLayout + ".TaskQueueView";
    private static BoardLayout? _current;

    /// <summary>Raised after any board committed a new layout (all instance tabs follow).</summary>
    public static event Action<BoardLayout>? Changed;

    public static BoardLayout Current => (_current ??= Load()).Clone();

    public static BoardLayout Load()
    {
        try
        {
            var json = ConfigurationManager.Current.GetValue(ConfigurationKeys.LiquidBoardLayout, string.Empty);
            if (!string.IsNullOrWhiteSpace(json))
            {
                var saved = JsonConvert.DeserializeObject<BoardLayout>(json);
                if (saved?.IsValid() == true) return saved;
                LoggerHelper.Warning("工作台布局配置无效，已使用默认布局");
                return BoardLayout.Default();
            }
            foreach (var key in new[] { LegacyKey, ConfigurationKeys.DashboardCardGridLayout })
            {
                if (!ConfigurationManager.Current.ContainsKey(key)) continue;
                var legacy = ConfigurationManager.Current.GetValue(key, new List<DashboardCardLayout>());
                if (legacy is { Count: > 0 })
                {
                    var migrated = BoardSolver.FromLegacy(legacy.Select(l => (l.Id, l.Col, l.Row, l.ColSpan, l.RowSpan)));
                    LoggerHelper.Info("已从旧版网格布局迁移工作台布局");
                    return migrated;
                }
            }
        }
        catch (Exception ex)
        {
            LoggerHelper.Warning($"读取工作台布局失败，使用默认布局：{ex.Message}");
        }
        return BoardLayout.Default();
    }

    public static bool Save(BoardLayout layout)
    {
        if (!layout.IsValid()) return false;
        _current = layout.Clone();
        try
        {
            ConfigurationManager.Current.SetValue(ConfigurationKeys.LiquidBoardLayout, JsonConvert.SerializeObject(layout));
        }
        catch (Exception ex)
        {
            LoggerHelper.Warning($"保存工作台布局失败：{ex.Message}");
            Changed?.Invoke(layout.Clone());
            return false;
        }
        Changed?.Invoke(layout.Clone());
        return true;
    }

    public static void Reset() => Save(BoardLayout.Default());
}
