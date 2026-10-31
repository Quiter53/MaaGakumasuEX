using System;

namespace MFAAvalonia.Controls.Liquid;

/// <summary>Page keys of the liquid shell navigation.</summary>
public static class LiquidPages
{
    public const string Tasks = "tasks";
    public const string CardRules = "card-rules";
    public const string Drinks = "drinks";
    public const string CardList = "card-list";
    public const string Monitor = "monitor";
    public const string Screenshot = "screenshot";
    public const string Settings = "settings";
}

/// <summary>
/// Navigation entry for code that used to select SukiSideMenu items directly.
/// The shell registers itself once it is loaded.
/// </summary>
public static class LiquidNavigation
{
    public interface IHost
    {
        string CurrentPage { get; }
        void Navigate(string page);
        void NavigateSettings(string? section);
    }

    private static IHost? _host;
    public static event Action<string>? PageChanged;

    public static string CurrentPage => _host?.CurrentPage ?? LiquidPages.Tasks;

    internal static void Register(IHost host) => _host = host;
    internal static void RaisePageChanged(string page) => PageChanged?.Invoke(page);

    public static void Navigate(string page) => _host?.Navigate(page);
    public static void NavigateSettings(string? section = null) => _host?.NavigateSettings(section);
}
