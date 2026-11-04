// Run a temporary copy in staging with the portable runtimeconfig/depsfile.
// Uses the real NetBeauty startup hook; deliberately adds no custom resolver.
using System.Globalization;
using System.Diagnostics;
using System.Reflection;
using System.Resources;
using System.Text.Json;
using System.Xml.Linq;

try
{
var core = Assembly.Load("MFAAvalonia.Core");
var expectedCore = Path.Combine(AppContext.BaseDirectory, "libs", "MFAAvalonia.Core.dll");
if (Path.GetFullPath(core.Location) != Path.GetFullPath(expectedCore))
    throw new Exception("Core did not load from libs");
using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "MFAAvalonia.deps.json")));
var targetName = manifest.RootElement.GetProperty("runtimeTarget").GetProperty("name").GetString()!;
var target = manifest.RootElement.GetProperty("targets").GetProperty(targetName);
foreach (var (name, packageVersion) in new[] {
    ("MailKit", "4.16.0"), ("MimeKit", "4.16.0"), ("BouncyCastle.Cryptography", "2.6.2"),
    ("SharpCompress", "0.48.0"), ("System.Security.Cryptography.Pkcs", "10.0.13") })
{
    var dependency = target.GetProperty(name + "/" + packageVersion);
    var filename = name + ".dll";
    var metadata = dependency.GetProperty("runtime").EnumerateObject().Single(item => Path.GetFileName(item.Name) == filename).Value;
    var assembly = Assembly.Load(new AssemblyName(name));
    var expectedPath = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "libs", filename));
    if (!string.Equals(Path.GetFullPath(assembly.Location), expectedPath, StringComparison.OrdinalIgnoreCase))
        throw new Exception(name + " did not load from the portable libs directory");
    var expectedVersion = Version.Parse(metadata.GetProperty("assemblyVersion").GetString()!);
    if (assembly.GetName().Version != expectedVersion)
        throw new Exception(name + " assembly version differs from the package manifest");
    var expectedFileVersion = metadata.GetProperty("fileVersion").GetString();
    var actualFileVersion = FileVersionInfo.GetVersionInfo(assembly.Location).FileVersion;
    if (expectedFileVersion != actualFileVersion)
        throw new Exception(name + " file version differs from the package manifest");
    Console.WriteLine($"PORTABLE_DEPENDENCY_OK {name}/{packageVersion}: {actualFileVersion}");
}
var manager = new ResourceManager("MFAAvalonia.Assets.Localization.Strings", core);
foreach (var language in new[] { "zh-CN", "en-US", "ja-JP", "zh-Hant" })
{
    var culture = CultureInfo.GetCultureInfo(language);
    var suffix = language == "zh-CN" ? "" : "." + language;
    var source = XDocument.Load(Path.Combine(args[0], "Strings" + suffix + ".resx"));
    var checkedCount = 0;
    foreach (var item in source.Root!.Elements("data"))
    {
        if (item.Attribute("type") != null || item.Attribute("mimetype") != null) continue;
        var key = item.Attribute("name")!.Value;
        var value = item.Element("value")?.Value;
        // XML parsing normalizes CRLF; compiled resources may preserve it.
        if (value?.Replace("\r\n", "\n") != manager.GetString(key, culture)?.Replace("\r\n", "\n"))
            throw new Exception($"Translation mismatch: {language}/{key}");
        checkedCount++;
    }
    if (language != "zh-CN")
    {
        var satellite = core.GetSatelliteAssembly(culture);
        var expected = Path.Combine(AppContext.BaseDirectory, "libs", "locales", language, "MFAAvalonia.Core.resources.dll");
        if (Path.GetFullPath(satellite.Location) != Path.GetFullPath(expected))
            throw new Exception("Satellite did not load from libs/locales");
    }
    Console.WriteLine($"PORTABLE_LANGUAGE_OK {language}: {checkedCount} strings");
}
return 0;
}
catch (Exception error)
{
    Console.Error.WriteLine(error.Message);
    return 1;
}
