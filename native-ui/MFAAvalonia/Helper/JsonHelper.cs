using Avalonia.Threading;
using MFAAvalonia.Extensions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using System;
using System.Collections.Concurrent;
using System.IO;
using System.Threading;

namespace MFAAvalonia.Helper;

public static class JsonHelper
{
    // 同一文件的读写在进程内串行化；写入先落到同目录临时文件再替换，避免截断的 JSON。
    // 每次保存按序号排序，较旧的快照不会覆盖已写入的较新内容（多个写入者并发保存同一文件时）。
    private sealed class FileGate
    {
        public long Issued;
        public long Written;
    }

    private static readonly ConcurrentDictionary<string, FileGate> Gates =
        new(OperatingSystem.IsWindows() ? StringComparer.OrdinalIgnoreCase : StringComparer.Ordinal);

    private static FileGate GateFor(string filePath) => Gates.GetOrAdd(Path.GetFullPath(filePath), _ => new FileGate());

    // 加载JSON配置（自动处理线程问题，Newtonsoft.Json 反序列化到实体类时会自动忽略注释）
    public static T LoadJson<T>(string filePath, T defaultValue = default, params JsonConverter[] converters)
    {
        return LoadJson(filePath, defaultValue, null, converters);
    }

    public static T LoadJson<T>(string filePath, T defaultValue = default, Action? errorHandle = null, params JsonConverter[] converters)
    {
        try
        {
            EnsureDirectory(filePath);
            if (!File.Exists(filePath)) return defaultValue;

            string json;
            lock (GateFor(filePath)) json = File.ReadAllText(filePath);
            // Newtonsoft.Json 反序列化到实体类时会自动忽略注释，支持 JSONC 格式
            return TryDeserialize<T>(json, converters) ?? defaultValue;
        }
        catch (Exception ex) when (IsThreadAccessException(ex))
        {
            // 线程错误：切换到UI线程重试
            return DispatcherHelper.RunOnMainThread(() => LoadJson(filePath, defaultValue, errorHandle, converters));
        }
        catch (Exception ex)
        {
            errorHandle?.Invoke();
            LoggerHelper.Error($"配置加载失败：{Path.GetFileName(filePath)}", ex);
            return defaultValue;
        }
    }

    // 保存JSON配置（自动处理线程问题）
    public static bool SaveJson<T>(string filePath, T config, params JsonConverter[] converters)
    {
        try
        {
            EnsureDirectory(filePath);
            var gate = GateFor(filePath);
            // 序号在序列化时分配：它代表这份快照的新旧。
            var sequence = Interlocked.Increment(ref gate.Issued);
            var json = TrySerialize(config, converters);
            return WriteAtomically(filePath, json, gate, sequence);
        }
        catch (Exception ex) when (IsThreadAccessException(ex))
        {
            // 线程错误：切换到UI线程重试
            DispatcherHelper.PostOnMainThread(() => SaveJson(filePath, config, converters));
            return true;
        }
        catch (Exception ex)
        {
            LoggerHelper.Error($"配置保存失败：{Path.GetFileName(filePath)}", ex);
            return false;
        }
    }

    private static bool WriteAtomically(string filePath, string json, FileGate gate, long sequence)
    {
        lock (gate)
        {
            if (sequence < gate.Written) return true; // 已有更新的内容落盘
            var temp = filePath + ".tmp";
            // 其他程序（杀毒、同步盘）可能短暂占用目标文件：有限次退避后仍失败则如实报告。
            for (var attempt = 0;; attempt++)
            {
                try
                {
                    File.WriteAllText(temp, json);
                    File.Move(temp, filePath, true);
                    gate.Written = sequence;
                    return true;
                }
                catch (IOException ex) when (attempt < 3)
                {
                    LoggerHelper.Warning($"配置文件被占用，第 {attempt + 1} 次重试保存：{Path.GetFileName(filePath)}（{ex.Message}）");
                    Thread.Sleep(100 << attempt);
                }
                catch (Exception ex)
                {
                    try { if (File.Exists(temp)) File.Delete(temp); } catch (IOException) { }
                    LoggerHelper.Error($"配置保存失败（重试后仍未写入）：{Path.GetFileName(filePath)}", ex);
                    return false;
                }
            }
        }
    }

    // 加载配置（基于LoadJson）
    public static T LoadConfig<T>(string configName, T defaultValue = default, params JsonConverter[] converters)
    {
        var filePath = GetConfigPath(configName);
        return LoadJson(filePath, defaultValue, converters);
    }

    // 保存配置（基于SaveJson）
    public static void SaveConfig<T>(string configName, T config, params JsonConverter[] converters)
    {
        var filePath = GetConfigPath(configName);
        SaveJson(filePath, config, converters);
    }

    // 辅助方法：确保目录存在
    private static void EnsureDirectory(string filePath)
    {
        var directory = Path.GetDirectoryName(filePath);
        if (!string.IsNullOrEmpty(directory) && !Directory.Exists(directory))
        {
            Directory.CreateDirectory(directory);
            LoggerHelper.Info($"自动创建配置目录：{directory}");
        }
    }

    // 辅助方法：获取配置文件路径
    private static string GetConfigPath(string configName)
    {
        AppPaths.Initialize();
        if (!Directory.Exists(AppPaths.ConfigDirectory))
            Directory.CreateDirectory(AppPaths.ConfigDirectory);
        return Path.Combine(AppPaths.ConfigDirectory, $"{configName}.json");
    }

    // 辅助方法：尝试序列化（带线程错误检测）
    private static string TrySerialize<T>(T value, JsonConverter[] converters)
    {
        var settings = GetSerializerSettings(converters);
        return JsonConvert.SerializeObject(value, settings);
    }

    // 辅助方法：尝试反序列化（带线程错误检测）
    private static T? TryDeserialize<T>(string json, JsonConverter[] converters)
    {
        var settings = GetDeserializerSettings(converters);
        var token = JToken.Parse(json, new JsonLoadSettings
        {
            CommentHandling = CommentHandling.Ignore
        });
        return token.ToObject<T>(JsonSerializer.Create(settings));
    }

    // 辅助方法：创建JSON序列化设置
    private static JsonSerializerSettings GetSerializerSettings(JsonConverter[] converters)
    {
        var settings = new JsonSerializerSettings
        {
            Formatting = Formatting.Indented,
            NullValueHandling = NullValueHandling.Ignore,
            DefaultValueHandling = DefaultValueHandling.Ignore
        };
        if (converters is { Length: > 0 })
        {
            settings.Converters.AddRange(converters);
        }
        return settings;
    }
    
    private static JsonSerializerSettings GetDeserializerSettings(JsonConverter[] converters)
    {
        var settings = new JsonSerializerSettings();
        if (converters is { Length: > 0 })
        {
            settings.Converters.AddRange(converters);
        }
        return settings;
    }
    
    // 辅助方法：判断是否为线程访问错误
    private static bool IsThreadAccessException(Exception ex)
    {
        // 检查是否为Avalonia线程访问异常
        return ex is InvalidOperationException 
            && ex.Message.Contains("Call from invalid thread") 
            || (ex.InnerException != null && IsThreadAccessException(ex.InnerException));
    }
}
