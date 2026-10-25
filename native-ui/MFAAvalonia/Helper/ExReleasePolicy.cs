using System;

namespace MFAAvalonia.Helper;

/// <summary>EX uses numeric versions; only explicit opt-in admits prereleases.</summary>
public static class ExReleasePolicy
{
    public static bool IsEligible(string tag, bool prerelease, bool draft, bool includePrerelease)
    {
        return !draft
            && (!prerelease || includePrerelease)
            && Version.TryParse(tag.TrimStart('v', 'V'), out _);
    }
}
