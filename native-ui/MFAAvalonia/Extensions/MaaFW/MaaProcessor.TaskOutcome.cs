using System;
using System.Diagnostics;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MFAAvalonia.Helper;
using MFAAvalonia.Helper.ValueType;

namespace MFAAvalonia.Extensions.MaaFW;

public partial class MaaProcessor
{
    private readonly Stopwatch _taskClock = new();
    private volatile TaskLoopGuard? _taskLogGuard;
    private volatile bool _watchdogStopped;
    private volatile bool _manualStopRequested;
    private bool _queueHadFailure;
    private bool _stopUnconfirmed;
    private string _activeTaskName = "";
    private string _failedTaskName = "";
    private string _failureReasonCode = "";

    private void ResetTaskOutcome()
    {
        _taskLogGuard = null;
        _watchdogStopped = _queueHadFailure = false;
        _manualStopRequested = false;
        _stopUnconfirmed = false;
        _activeTaskName = _failedTaskName = _failureReasonCode = "";
        _taskClock.Restart();
    }

    public void ObserveTaskLog(string message)
    {
        var guard = _taskLogGuard;
        if (guard == null) return;
        if (_isWaitingForModal) guard.Reset(_taskClock.Elapsed);
        else guard.Record(_taskClock.Elapsed, message);
    }

    private void RecordTaskFailure(string code)
    {
        _queueHadFailure = true;
        // A watchdog stop explains why the whole queue was aborted; otherwise retain
        // the first failed task even when the user's continue-on-error option is on.
        if (_failureReasonCode.Length == 0 || code is "loop" or "silent")
        {
            _failureReasonCode = code;
            _failedTaskName = _activeTaskName;
        }
    }

    private async Task MonitorTaskLogsAsync(TaskLoopGuard guard, Task job, CancellationToken token)
    {
        try
        {
            while (!job.IsCompleted)
            {
                await Task.Delay(TimeSpan.FromSeconds(5), token);
                if (job.IsCompleted || token.IsCancellationRequested) return;
                var reason = guard.Check(_taskClock.Elapsed, _isWaitingForModal);
                if (reason == null) continue;
                RecordTaskFailure(reason);
                _watchdogStopped = true;
                AddLog(TaskOutcomeText.Reason(reason, LanguageHelper.CurrentLanguage),
                    (Avalonia.Media.IBrush?)null, observeTaskProgress: false);
                // Use the normal task-stop path. Do not kill the game, exit the UI,
                // run after-task shutdown actions, or advance to the next queued task.
                Stop(MFATask.MFATaskStatus.FAILED);
                return;
            }
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested) { }
    }

    private bool ShouldSendTaskOutcome(string outcome) =>
        !_manualStopRequested && outcome is "success" or "failed";

    private async Task SendTaskOutcomeAsync(string outcome)
    {
        // A manual cancellation stays silent even if aborting the executor
        // times out and the local completion status is subsequently FAILED.
        if (!ShouldSendTaskOutcome(outcome)) return;
        var settings = Instances.ExternalNotificationSettingsUserControlModel;
        string custom = !settings.EnabledCustom ? "" : outcome == "success"
            ? settings.CustomSuccessText : outcome == "failed" ? settings.CustomFailureText : "";
        string name = outcome == "failed" ? _failedTaskName : outcome == "stopped" ? _activeTaskName : "";
        if (string.IsNullOrWhiteSpace(name))
            name = string.Join("、", _tempTasks.Select(t => LanguageHelper.GetLocalizedString(t.Name)));
        var reason = TaskOutcomeText.Reason(outcome == "failed" ? _failureReasonCode : outcome, LanguageHelper.CurrentLanguage);
        if (_stopUnconfirmed) reason += " " + TaskOutcomeText.Reason("stopPending", LanguageHelper.CurrentLanguage);
        var report = TaskOutcomeText.Build(outcome, name, reason, _taskClock.Elapsed, LanguageHelper.CurrentLanguage, custom);
        // Automatic failure (including watchdog stops) still reports its cause.
        // Bound network waiting independently of the cancelled execution token.
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(30));
        try { await ExternalNotificationHelper.ExternalNotificationAsync(report, timeout.Token); }
        catch (Exception ex) { LoggerHelper.Warning($"任务结果通知未能发送：{ex.GetType().Name}"); }
    }
}
