using System;

namespace MFAAvalonia.Helper;

internal static class ReleaseAssetPolicy
{
    public static bool IsPackage(string? name, string? state)
    {
        if (string.IsNullOrWhiteSpace(name) || (state != null && state != "uploaded"))
            return false;
        foreach (var extension in new[] { ".zip", ".7z", ".tar.gz", ".tgz", ".tar.xz" })
            if (name.EndsWith(extension, StringComparison.OrdinalIgnoreCase))
                return true;
        return false;
    }
}
