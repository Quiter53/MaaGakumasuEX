namespace Avalonia.Controls
{
    public class ProgressBar
    {
        public double Value { get; set; }
    }
}

namespace MFAAvalonia.Helper
{
    public static class LoggerHelper
    {
        public static List<string> Messages { get; } = new();
        public static void Warning(string message) { Messages.Add(message); Console.WriteLine(message); }
        public static void Error(string message) { Messages.Add(message); Console.WriteLine(message); }
    }

    public static class DispatcherHelper
    {
        public static void RunOnMainThread(Action action) => action();
    }
}
