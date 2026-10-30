using System;
using System.Linq;
using MFAAvalonia.Helper;

int passed = 0;
void Check(bool value, string name) { if (!value) throw new Exception(name); passed++; }

var releases = new[]
{
    (Tag: "v2.1.2", Pre: false, Draft: false),
    (Tag: "v2.1.3", Pre: true, Draft: false),
    (Tag: "v9.0.0", Pre: false, Draft: true),
    (Tag: "v3.0.0-alpha", Pre: true, Draft: false),
    (Tag: "v2.0.0", Pre: false, Draft: false),
};
string Latest(bool includePre) => releases
    .Where(r => ExReleasePolicy.IsEligible(r.Tag, r.Pre, r.Draft, includePre))
    .OrderByDescending(r => Version.Parse(r.Tag[1..])).First().Tag;

Check(Latest(false) == "v2.1.2", "Default channel ignores the newer prerelease and drafts");
Check(Latest(true) == "v2.1.3", "Opt-in admits numeric prerelease tags");
Check(Latest(false) == "v2.1.2", "Disabling opt-in returns to the stable channel");
Check(ExReleasePolicy.IsEligible("v2.1.4", false, false, true), "Test channel also admits stable releases");
foreach (bool includePre in new[] { false, true })
{
    Check(!ExReleasePolicy.IsEligible("v2.1.4", false, true, includePre), "Stable draft excluded");
    Check(!ExReleasePolicy.IsEligible("v2.1.4", true, true, includePre), "Prerelease draft excluded");
    Check(!ExReleasePolicy.IsEligible("", false, false, includePre), "Missing version excluded");
    Check(!ExReleasePolicy.IsEligible("garbage", false, false, includePre), "Malformed version excluded");
    Check(ExReleasePolicy.IsEligible("V2.1.2", false, false, includePre), "Uppercase prefix accepted");
    Check(ExReleasePolicy.IsEligible("2.1.2", false, false, includePre), "Numeric version accepted");
}
foreach (var name in new[] { "app-win-x64.zip.sha256", "SHA256SUMS", "app-win-x64.zip.files.json", "app-win-x64.bin", "" })
    Check(!ReleaseAssetPolicy.IsPackage(name, "uploaded"), "Non-package asset excluded: " + name);
Check(!ReleaseAssetPolicy.IsPackage("app-win-x64.zip", "starter"), "Incomplete upload excluded");
foreach (var name in new[] { "app-win-x64.zip", "app-linux-x64.tar.gz", "app-osx-arm64.7z" })
    Check(ReleaseAssetPolicy.IsPackage(name, "uploaded"), "Complete package accepted: " + name);
Console.WriteLine($"RELEASE_POLICY_TESTS_OK: {passed}");
