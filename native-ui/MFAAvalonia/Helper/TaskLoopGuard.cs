using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;

namespace MFAAvalonia.Helper;

/// <summary>Per-task log watchdog. All time values use a monotonic clock.</summary>
public sealed class TaskLoopGuard
{
    private readonly object _sync = new();
    private readonly Queue<string> _recent = new();
    private TimeSpan _lastLog;
    private TimeSpan _repeatSince;
    private string? _pattern;
    private int _repeats;
    public TaskLoopGuard(TimeSpan now) => Reset(now);
    public void Reset(TimeSpan now)
    {
        lock (_sync)
        {
            _recent.Clear(); _pattern = null; _repeats = 0;
            _lastLog = _repeatSince = now;
        }
    }

    public static string Normalize(string text)
    {
        text = Regex.Replace(text, @"\x1B\[[0-9;]*[a-zA-Z]", "");
        text = Regex.Replace(text, @"^\s*(?:\[?\d{4}-\d{2}-\d{2}[ T])?\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\]?\s*", "");
        text = Regex.Replace(text, @"^(?:info|success|warn|err|critical):\s*", "", RegexOptions.IgnoreCase);
        // Strip volatile diagnostic values, but keep turns, card names, quantities and scores.
        text = Regex.Replace(text, @"(?:box|roi|confidence|margin|elapsed|耗时|置信度)\s*[:=：]\s*(?:\[[^\]]*\]|\([^)]*\)|[-+.\d]+(?:ms|s|秒)?)", "", RegexOptions.IgnoreCase);
        text = Regex.Replace(text, @"\bscore\s*[:=]\s*(?:0\.\d+|1\.0+)\b", "", RegexOptions.IgnoreCase);
        text = Regex.Replace(text, @"(?:重试|retry)\s*(?:第\s*)?\d+(?:\s*/\s*\d+)?\s*次?", "重试", RegexOptions.IgnoreCase);
        return Regex.Replace(text, @"\s+", " ").Trim();
    }

    public void Record(TimeSpan now, string message)
    {
        if (string.IsNullOrWhiteSpace(message) || message.StartsWith("debug:", StringComparison.OrdinalIgnoreCase)
            || message.StartsWith("trace:", StringComparison.OrdinalIgnoreCase)) return;
        var text = Normalize(message);
        if (text.Length == 0) return;
        lock (_sync)
        {
            // A delayed next line does not inherit an old repetition interval.
            if (now - _lastLog > TimeSpan.FromMinutes(1)) { _pattern = null; _repeats = 0; }
            _lastLog = now;
            _recent.Enqueue(text);
            if (_recent.Count > 64) _recent.Dequeue();
            var lines = _recent.ToArray();
            string? pattern = null;
            for (int period = 1; period <= 16 && period * 3 <= lines.Length; period++)
            {
                bool same = true;
                for (int i = lines.Length - period * 3; i < lines.Length - period; i++)
                    if (lines[i] != lines[i + period]) { same = false; break; }
                if (!same) continue;
                // Rotation-independent identity for A/B/A/B and B/A/B/A.
                var cycle = lines.Skip(lines.Length - period).ToArray();
                pattern = Enumerable.Range(0, period)
                    .Select(offset => string.Join("\u001f", cycle.Skip(offset).Concat(cycle.Take(offset))))
                    .OrderBy(s => s, StringComparer.Ordinal).First();
                break;
            }
            if (pattern == null || pattern != _pattern)
            {
                _pattern = pattern; _repeatSince = now; _repeats = 0;
            }
            else _repeats++;
        }
    }

    public string? Check(TimeSpan now, bool paused)
    {
        lock (_sync)
        {
            if (paused) { Reset(now); return null; }
            if (now - _lastLog >= TimeSpan.FromMinutes(5)) return "silent";
            if (_pattern != null && _repeats >= 30 && now - _repeatSince >= TimeSpan.FromMinutes(3)) return "loop";
            return null;
        }
    }
}
