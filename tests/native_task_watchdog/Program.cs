using System;
using System.IO;
using System.Text.Json;
using System.Globalization;
using System.Collections.Generic;
using MFAAvalonia.Helper;

int passed = 0;
void Check(bool value, string name) { if (!value) throw new Exception(name); passed++; }
TimeSpan At(double seconds) => TimeSpan.FromSeconds(seconds);
var guard = new TaskLoopGuard(At(0));
Check(guard.Check(At(299), false) == null, "Brief silence is not a stop");
Check(guard.Check(At(300), false) == "silent", "Five-minute silence");
guard.Reset(At(0));
for (int i = 0; i <= 200; i++) guard.Record(At(i), "warn:等待重新识别");
Check(guard.Check(At(200), false) == "loop", "Repeated single log");
guard.Reset(At(0));
for (int i = 0; i <= 240; i++) guard.Record(At(i), i % 2 == 0 ? "跳过剧情" : "点击取消");
Check(guard.Check(At(240), false) == "loop", "Two-screen cycle");
guard.Reset(At(0));
for (int i = 0; i <= 240; i++) guard.Record(At(i), new[] { "识别卡牌", "尝试确认", "返回选择" }[i % 3]);
Check(guard.Check(At(240), false) == "loop", "Three-log cycle");
guard.Record(At(241), "进入下一周");
Check(guard.Check(At(242), false) == null, "New progress breaks cycle");
guard.Reset(At(0));
for (int i = 0; i < 1000; i++)
{
    guard.Record(At(i), $"当前回合 {i}，卡牌 届いて！");
    Check(guard.Check(At(i), false) == null, "Real turn progress is not normalized away");
}
guard.Reset(At(0));
for (int i = 0; i <= 240; i++) guard.Record(At(i), $"[2026-10-16 12:{i / 60:00}:{i % 60:00}.100] warn:重试第 {i} 次");
Check(guard.Check(At(240), false) == "loop", "Timestamps and retry counters do not hide recurrence");
guard.Reset(At(0));
for (int i = 0; i <= 240; i++) guard.Record(At(i), $"等待确认 confidence=0.{i:000} score=0.{i:000} box=[{i},2,3,4] elapsed={i}ms");
Check(guard.Check(At(240), false) == "loop", "Detailed confidence and timing jitter does not hide loops");
Check(TaskLoopGuard.Normalize("当前分数 score=1500") != TaskLoopGuard.Normalize("当前分数 score=1600"), "Actual game score is preserved");
guard.Reset(At(0));
for (int i = 0; i < 5; i++) guard.Record(At(i * 50), "等待");
Check(guard.Check(At(250), false) == null, "Low-frequency repeated log requires enough observations");
Check(guard.Check(At(4000), true) == null, "Manual confirmation pause");
guard.Reset(At(4001));
Check(guard.Check(At(4299), false) == null, "Resume resets timeout");
Check(guard.Check(At(4301), false) == "silent", "Timeout works again after resume");
guard.Reset(At(0));
guard.Record(At(299), "debug:内部计时");
Check(guard.Check(At(300), false) == "silent", "Diagnostic noise is not task progress");
guard.Reset(At(600));
Check(guard.Check(At(601), false) == null, "New task clears old evidence");
var other = new TaskLoopGuard(At(0));
guard.Record(At(601), "进展");
Check(other.Check(At(601), false) == "silent", "Instances are isolated");
foreach (var lang in new[] { "zh-CN", "zh-Hant", "en-US", "ja-JP" })
foreach (var outcome in new[] { "success", "failed", "stopped" })
{
    var reason = TaskOutcomeText.Reason(outcome == "failed" ? "loop" : outcome, lang);
    var report = TaskOutcomeText.Build(outcome, "培育", reason, At(3670), lang, "自定义文本");
    Check(report.Contains("培育") && report.Contains(reason) && report.Contains("01:01:10") && report.EndsWith("自定义文本"), "Structured localized report preserves custom text");
}
Check(WxPushResult.Accepted("{\"code\":1000,\"data\":[{\"code\":1000}]}"), "Normal push accepted");
Check(WxPushResult.Accepted("{\"success\":true,\"data\":\"ok\"}"), "Simple push accepted");
Check(!WxPushResult.Accepted("{\"code\":1001,\"msg\":\"invalid token\"}"), "HTTP 200 business failure is not success");
Check(!WxPushResult.Accepted("{\"code\":1000,\"data\":[{\"code\":1001}]}"), "Recipient failure is not success");
Check(!WxPushResult.Accepted("{}") && !WxPushResult.Accepted("invalid"), "Missing result is not success");
Check(!WxPushResult.Accepted("{\"code\":1000,\"success\":false}"), "Explicit failure wins");
Check(!WxPushResult.Accepted("{\"code\":1001,\"success\":true}"), "Error code wins over success flag");
Check(TaskOutcomeText.Reason("stopPending", "zh-CN").Contains("尚未确认"), "Unconfirmed stop is reported honestly");
Console.WriteLine($"WATCHDOG_AND_OUTCOME_TESTS_OK: {passed}");

if (args.Length > 0)
{
    // Replay the saved real incident event timeline; no sleeping or game input.
    DateTime start = DateTime.Parse("2026-10-15 03:06:59.308", CultureInfo.InvariantCulture);
    var replay = new TaskLoopGuard(TimeSpan.Zero);
    var seen = new HashSet<long>();
    TimeSpan? detected = null;
    int events = 0;
    foreach (var line in File.ReadLines(args[0]))
    {
        using var doc = JsonDocument.Parse(line);
        var e = doc.RootElement;
        var time = DateTime.Parse(e.GetProperty("time").GetString()!, CultureInfo.InvariantCulture);
        if (time < start || time > start.AddMinutes(6)) continue;
        var action = e.GetProperty("action");
        if (!seen.Add(action.GetProperty("action_id").GetInt64())) continue;
        var now = time - start;
        replay.Record(now, "操作:" + e.GetProperty("name").GetString());
        events++;
        if (replay.Check(now, false) == "loop") { detected = now; break; }
    }
    Check(detected.HasValue && detected.Value.TotalMinutes < 5, "Real story loop detected before five minutes");
    Console.WriteLine($"REAL_STORY_LOG_REPLAY_OK: stoppedAfter={detected!.Value.TotalSeconds:F1}s events={events}");
}
