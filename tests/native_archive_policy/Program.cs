using System.Formats.Tar;
using System.IO.Compression;
using System.Text;
using MFAAvalonia.Helper;

var root = Path.GetFullPath(args.Length > 0 ? args[0] : Path.Combine(Path.GetTempPath(), "ex-archive-" + Guid.NewGuid()));
if (Directory.Exists(root)) throw new IOException("Use a new test output directory.");
Directory.CreateDirectory(root);
int passed = 0;
void Check(bool condition, string message)
{
    if (!condition) throw new Exception(message);
    passed++;
}

string MakeZip(string name, string entryName, string contents = "new")
{
    var path = Path.Combine(root, name);
    using var archive = ZipFile.Open(path, ZipArchiveMode.Create);
    using var writer = new StreamWriter(archive.CreateEntry(entryName).Open());
    writer.Write(contents);
    return path;
}

async Task<bool> Extract(string package, string directory, bool async)
    => async ? await UniversalExtractor.ExtractAsync(package, directory) : UniversalExtractor.Extract(package, directory);

foreach (var async in new[] { false, true })
{
    var suffix = async ? "async" : "sync";
    var valid = MakeZip($"valid-{suffix}.zip", "sub/file.txt");
    var validOutput = Path.Combine(root, "valid-" + suffix);
    Check(await Extract(valid, validOutput, async), "Valid ZIP must extract.");
    Check(File.ReadAllText(Path.Combine(validOutput, "sub/file.txt")) == "new", "Nested contents must match.");

    foreach (var (name, entry) in new[]
             {
                 ("parent", "../escaped.txt"),
                 ("backslash", "..\\escaped.txt"),
                 ("absolute", Path.Combine(root, "escaped-absolute.txt")),
                 ("sibling", "../staging-sibling/escaped.txt")
             })
    {
        var package = MakeZip($"{name}-{suffix}.zip", entry);
        Check(!await Extract(package, Path.Combine(root, $"staging-{name}-{suffix}"), async), "Reject " + name);
    }
    Check(!File.Exists(Path.Combine(root, "escaped.txt")), "Traversal must not create a file outside staging.");
    Check(!File.Exists(Path.Combine(root, "escaped-absolute.txt")), "Rooted paths must not be written.");
    Check(!Directory.Exists(Path.Combine(root, "staging-sibling")), "Sibling prefix must not pass the root boundary.");

    // The .bin suffix forces the production ReaderFactory fallback.
    var fallback = MakeZip($"fallback-{suffix}.bin", "file.txt");
    var fallbackOutput = Path.Combine(root, "fallback-" + suffix);
    Directory.CreateDirectory(fallbackOutput);
    File.WriteAllText(Path.Combine(fallbackOutput, "file.txt"), "long previous file contents");
    Check(await Extract(fallback, fallbackOutput, async), "Fallback must support a valid archive.");
    Check(File.ReadAllText(Path.Combine(fallbackOutput, "file.txt")) == "new", "Fallback overwrite must truncate old content.");
    var fallbackEscape = MakeZip($"fallback-escape-{suffix}.bin", "../escaped.txt");
    Check(!await Extract(fallbackEscape, Path.Combine(root, "fallback-escape-" + suffix), async), "Fallback must reject traversal.");
    Check(!File.Exists(Path.Combine(root, "escaped.txt")), "Fallback must keep writes within staging.");

    var linked = Path.Combine(root, $"linked-{suffix}.tar");
    using (var stream = File.Create(linked))
    using (var writer = new TarWriter(stream, TarEntryFormat.Ustar))
        writer.WriteEntry(new UstarTarEntry(TarEntryType.SymbolicLink, "redirect") { LinkName = "../outside" });
    LoggerHelper.Messages.Clear();
    Check(!await Extract(linked, Path.Combine(root, "linked-" + suffix), async), "Archive links must be rejected.");
    Check(LoggerHelper.Messages.Any(message => message.Contains("链接条目")), "Reject the actual link entry.");

    var broken = Path.Combine(root, $"broken-{suffix}.zip");
    File.WriteAllText(broken, "incomplete archive", Encoding.UTF8);
    Check(!await Extract(broken, Path.Combine(root, "broken-" + suffix), async), "Corrupt archives must report failure.");
}

Console.WriteLine($"ARCHIVE_POLICY_TESTS_OK: {passed}");
