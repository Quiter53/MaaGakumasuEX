using Avalonia.Logging;
using Avalonia.Threading;
using System.Collections.Concurrent;
using System.Text.Json;

internal sealed class AuditDiagnostics : ILogSink, IDisposable
{
    private readonly ILogSink? _previous = Avalonia.Logging.Logger.Sink;
    private readonly ConcurrentQueue<string> _errors = new();
    private Dispatcher? _dispatcher;

    public AuditDiagnostics()
    {
        Avalonia.Logging.Logger.Sink = this;
        TaskScheduler.UnobservedTaskException += OnTaskException;
    }

    public void AttachDispatcher()
    {
        // Accessing UIThread before platform setup creates a dispatcher without timer support.
        _dispatcher = Dispatcher.UIThread;
        _dispatcher.UnhandledException += OnDispatcherException;
    }

    private static bool Captured(LogEventLevel level, string area) =>
        level >= LogEventLevel.Warning && area is LogArea.Binding or LogArea.Property or LogArea.Layout
        || level >= LogEventLevel.Error && area == LogArea.Control;

    public bool IsEnabled(LogEventLevel level, string area) => Captured(level, area) || (_previous?.IsEnabled(level, area) ?? false);

    public void Log(LogEventLevel level, string area, object? source, string template) => Log(level, area, source, template, []);

    public void Log(LogEventLevel level, string area, object? source, string template, params object?[] values)
    {
        if (Captured(level, area))
            _errors.Enqueue($"{area} {level}: {template} [{string.Join(", ", values.Select(v => v?.ToString()))}] ({source?.GetType().Name})");
        if (_previous?.IsEnabled(level, area) == true) _previous.Log(level, area, source, template, values);
    }

    private void OnDispatcherException(object? sender, DispatcherUnhandledExceptionEventArgs e) => _errors.Enqueue(e.Exception.ToString());
    private void OnTaskException(object? sender, UnobservedTaskExceptionEventArgs e) => _errors.Enqueue(e.Exception.ToString());

    public bool Save(string output)
    {
        var errors = _errors.Distinct().ToArray();
        File.WriteAllText(Path.Combine(output, "ui-diagnostics.json"), JsonSerializer.Serialize(new { result = errors.Length == 0 ? "pass" : "fail", errors }, new JsonSerializerOptions { WriteIndented = true }));
        foreach (var error in errors) Console.WriteLine("FAIL UI diagnostic " + error);
        Console.WriteLine(errors.Length == 0 ? "UI_DIAGNOSTICS_PASSED" : "UI_DIAGNOSTICS_FAILED");
        return errors.Length == 0;
    }

    public void Dispose()
    {
        Avalonia.Logging.Logger.Sink = _previous;
        if (_dispatcher != null) _dispatcher.UnhandledException -= OnDispatcherException;
        TaskScheduler.UnobservedTaskException -= OnTaskException;
    }
}
