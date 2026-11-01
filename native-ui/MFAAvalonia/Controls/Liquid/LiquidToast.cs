using MFAAvalonia.Helper;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>
/// Toasts raised by the liquid pages. Pages hosted outside the application shell (isolated
/// UI test hosts) have no toast manager; the message is then only reflected in the page status.
/// </summary>
public static class LiquidToast
{
    private static bool Available => App.Services != null;
    public static void Success(string title, string message) { if (Available) ToastHelper.Success(title, message); }
    public static void Info(string title, string message) { if (Available) ToastHelper.Info(title, message); }
    public static void Warn(string title, string message) { if (Available) ToastHelper.Warn(title, message); }
}
