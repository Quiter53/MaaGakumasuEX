using System;

namespace MFAAvalonia.Helper;

public static class TaskOutcomeText
{
    public static string Reason(string code, string language) => (language, code) switch
    {
        ("en-US", "stopPending") => "The queue is cancelled, but the executor has not confirmed stopping. Please check the game manually.",
        ("ja-JP", "stopPending") => "キューは取り消されましたが、実行器の停止を確認できません。ゲームを手動で確認してください。",
        ("zh-Hant", "stopPending") => "佇列已取消，但尚未確認執行器停止，請手動檢查遊戲。",
        (_, "stopPending") => "队列已取消，但尚未确认执行器停止，请手动检查游戏。",
        ("en-US", "loop") => "The same log messages repeated for 3 minutes. The task was stopped to prevent an endless loop.",
        ("en-US", "silent") => "No task log was received for 5 minutes. The task was stopped.",
        ("en-US", "success") => "All selected tasks completed successfully.",
        ("en-US", "stopped") => "The run was stopped before completion.",
        ("en-US", "connection") => "The game connection could not be established or was lost.",
        ("en-US", _) => "Recognition or an operation did not complete. Check the local log for details.",
        ("ja-JP", "loop") => "3分間、同じログの繰り返しが続いたため、タスクを停止しました。",
        ("ja-JP", "silent") => "5分間、タスクのログが出力されなかったため、タスクを停止しました。",
        ("ja-JP", "success") => "選択したタスクはすべて正常に完了しました。",
        ("ja-JP", "stopped") => "完了前に実行が停止されました。",
        ("ja-JP", "connection") => "ゲームへの接続に失敗したか、接続が切れました。",
        ("ja-JP", _) => "認識または操作が完了しませんでした。詳細はローカルログを確認してください。",
        ("zh-Hant", "loop") => "連續 3 分鐘重複輸出相同日誌，已停止任務，避免死循環。",
        ("zh-Hant", "silent") => "連續 5 分鐘沒有任務日誌，已停止任務。",
        ("zh-Hant", "success") => "所有已選任務均已正常完成。",
        ("zh-Hant", "stopped") => "任務尚未完成，執行已被停止。",
        ("zh-Hant", "connection") => "未能連接遊戲，或遊戲連接已中斷。",
        ("zh-Hant", _) => "識別或操作未能完成，詳細記錄請查看本機日誌。",
        (_, "loop") => "连续 3 分钟重复输出相同日志，已停止任务，避免死循环。",
        (_, "silent") => "连续 5 分钟没有任务日志，已停止任务。",
        (_, "success") => "所有已选任务均已正常完成。",
        (_, "stopped") => "任务尚未完成，执行已被停止。",
        (_, "connection") => "未能连接游戏，或游戏连接已中断。",
        _ => "识别或操作未能完成，详细记录请查看本机日志。"
    };

    public static string Build(string outcome, string task, string reason, TimeSpan elapsed, string language, string custom = "")
    {
        var labels = language switch
        {
            "en-US" => new[] { "Succeeded", "Failed", "Stopped", "Task", "Reason", "Elapsed" },
            "ja-JP" => new[] { "成功", "失敗", "停止", "タスク", "理由", "所要時間" },
            "zh-Hant" => new[] { "成功", "失敗", "已停止", "任務", "原因", "耗時" },
            _ => new[] { "成功", "失败", "已停止", "任务", "原因", "耗时" }
        };
        string result = labels[outcome == "success" ? 0 : outcome == "stopped" ? 2 : 1];
        string report = $"MaaGakumasu EX — {result}\n{labels[3]}：{task}\n{labels[4]}：{reason}\n{labels[5]}：{(int)elapsed.TotalHours:00}:{elapsed.Minutes:00}:{elapsed.Seconds:00}";
        return string.IsNullOrWhiteSpace(custom) ? report : report + "\n\n" + custom;
    }
}
