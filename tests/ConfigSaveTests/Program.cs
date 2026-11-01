using MFAAvalonia.Helper;
using Newtonsoft.Json.Linq;

// Configuration writes: concurrent writers/readers on one file, newest snapshot wins, short external
// locks are retried, long ones are reported as failures without damaging the previous file.
var dir = Path.Combine(Path.GetTempPath(), "cfg-save-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(dir);
var path = Path.Combine(dir, "instance.json");
int count = 0;
void Check(bool ok, string name) { if (!ok) throw new Exception("FAIL " + name); count++; Console.WriteLine("PASS " + name); }

// 1. Concurrent writers and readers.
long latest = 0;
var errors = new List<Exception>();
var writers = Enumerable.Range(0, 4).Select(w => Task.Run(() =>
{
    for (int i = 0; i < 250; i++)
    {
        var value = Interlocked.Increment(ref latest);
        try { if (!JsonHelper.SaveJson(path, new JObject { ["value"] = value, ["writer"] = w })) throw new IOException("save reported failure"); }
        catch (Exception ex) { lock (errors) errors.Add(ex); }
    }
})).ToArray();
var readerFailures = 0;
var reader = Task.Run(() =>
{
    while (!writers.All(t => t.IsCompleted))
    {
        var existed = File.Exists(path);
        var o = JsonHelper.LoadJson<JObject?>(path, null);
        if (existed && o == null) Interlocked.Increment(ref readerFailures);
    }
});
Task.WaitAll(writers); reader.Wait();
Check(errors.Count == 0, "1000 concurrent saves raise no errors");
Check(readerFailures == 0, "readers never see a partial or locked file");
var final = JObject.Parse(File.ReadAllText(path));
Check(final["value"]!.Value<long>() >= 990, "the file holds one of the newest snapshots");
Check(!File.Exists(path + ".tmp"), "no temporary file left behind");

// 2. Older snapshot never overwrites a newer one (sequence is taken at serialisation time).
JsonHelper.SaveJson(path, new JObject { ["value"] = "newest" });
Check(JObject.Parse(File.ReadAllText(path))["value"]!.ToString() == "newest", "sequential saves keep the last state");

// 3. Short external lock: retried and written.
Task<bool> pending;
using (var held = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.None))
{
    pending = Task.Run(() => JsonHelper.SaveJson(path, new JObject { ["value"] = "after-short-lock" }));
    Thread.Sleep(150);
}
Check(pending.Result && JObject.Parse(File.ReadAllText(path))["value"]!.ToString() == "after-short-lock", "short external lock is retried and the save lands");

// 4. Long external lock: reported as failure, previous content intact (Windows enforces share modes).
if (OperatingSystem.IsWindows())
{
    bool ok;
    using (new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.None))
        ok = JsonHelper.SaveJson(path, new JObject { ["value"] = "should-not-land" });
    Check(!ok, "long external lock is reported as a failed save");
    Check(JObject.Parse(File.ReadAllText(path))["value"]!.ToString() == "after-short-lock", "failed save leaves the previous file intact");
}
else Console.WriteLine("SKIP long external lock (file share modes are only enforced on Windows)");

Directory.Delete(dir, true);
Console.WriteLine($"CONFIG_SAVE_TESTS_PASSED {count} checks");
