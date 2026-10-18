using System.Reflection;
using System.Runtime.CompilerServices;
using MaaFramework.Binding.Buffers;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;
using MFAAvalonia.ViewModels.Pages;
using Newtonsoft.Json.Linq;

// Exercise the production focus parser without constructing devices or settings
// windows. Logging is queued as usual; this console does not run the UI dispatcher.
const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
var processor = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
var viewModel = (TaskQueueViewModel)RuntimeHelpers.GetUninitializedObject(typeof(TaskQueueViewModel));
typeof(TaskQueueViewModel).GetField("_processorField", flags)!.SetValue(viewModel, processor);
var handler = new FocusHandler(new AutoInitDictionary(), viewModel);
int passed = 0;
void Check(bool ok, string name)
{
    if (!ok) throw new Exception(name);
    passed++;
    Console.WriteLine("PASS " + name);
}

const string currentEvent = "Node.Action.Starting";
void CheckFocus(JObject model, int expectedImages, bool expectedAbort, string name, string details = "{}")
{
    int images = 0, aborted = 0;
    handler.DisplayFocus(model, currentEvent, details, onAborted: () => aborted++, imageProvider: () =>
    {
        images++;
        return null; // A missing image must still allow text and legacy processing.
    });
    Check(images == expectedImages && aborted == (expectedAbort ? 1 : 0), name);
}

JObject Model(JToken content) => new()
{
    ["focus"] = new JObject { [currentEvent] = content, ["aborted"] = true }
};

CheckFocus(new JObject(), 0, false, "missing focus performs no image query");
CheckFocus(new JObject { ["focus"] = JValue.CreateNull() }, 0, false, "null focus performs no image query");
CheckFocus(new JObject { ["focus"] = new JObject() }, 0, false, "empty focus performs no image query");
CheckFocus(new JObject { ["focus"] = new JObject { ["Node.Action.Succeeded"] = "{image}", ["aborted"] = true } },
    0, true, "another event's image does not suppress legacy abort or request an image");
CheckFocus(new JObject { ["focus"] = "legacy text" }, 0, false, "legacy string does not request an image");
CheckFocus(new JObject { ["focus"] = new JObject { ["start"] = "legacy {image}", ["aborted"] = true } },
    0, true, "legacy placeholders and abort retain their old path");
CheckFocus(Model("plain text"), 0, true, "new protocol text retains legacy abort without image query");
CheckFocus(Model("{name}"), 0, true, "detail placeholders do not request images", "{\"name\":\"{image}\"}");
CheckFocus(Model("{Image}"), 0, true, "placeholder matching retains case sensitivity");
CheckFocus(Model("{image}"), 1, true, "current string image requests once and still runs legacy abort");
CheckFocus(Model("{image} {image}"), 1, true, "repeated image placeholders request once");
CheckFocus(Model(new JArray("text", "{image}", "{image}")), 1, true,
    "array items share one image request even when no image is available");
CheckFocus(Model(new JArray("text", "{name}")), 0, true, "plain text array requests no image");
CheckFocus(Model(new JArray(new JObject { ["content"] = "{image}" }, 42)), 0, true,
    "unsupported array items retain their ignored behavior");
CheckFocus(Model(new JObject { ["content"] = "plain", ["display"] = new JArray() }), 0, true,
    "object text requests no image");
CheckFocus(Model(new JObject { ["content"] = "{image}", ["display"] = new JArray() }), 1, true,
    "object content supports lazy images");
CheckFocus(Model(new JObject { ["display"] = "{image}" }), 0, true,
    "image text outside content does not request an image");
CheckFocus(Model(JValue.CreateNull()), 0, true, "null current event keeps legacy abort");

// Content may come from a file or localization key, rather than the event token.
var fixtureDirectory = Path.Combine(AppContext.BaseDirectory, "focus-fixtures");
Directory.CreateDirectory(fixtureDirectory);
var imageFile = Path.Combine(fixtureDirectory, "image.md");
var textFile = Path.Combine(fixtureDirectory, "text.md");
File.WriteAllText(imageFile, "image from a file: {image}");
File.WriteAllText(textFile, "text from a file");
CheckFocus(Model(imageFile), 1, true, "file contents are resolved before checking image placeholders");
CheckFocus(Model(textFile), 0, true, "plain file contents request no image");
var langs = (Dictionary<string, Dictionary<string, string>>)typeof(LanguageHelper)
    .GetField("Langs", BindingFlags.NonPublic | BindingFlags.Static)!.GetValue(null)!;
langs[LanguageHelper.CurrentLanguage] = new()
{
    ["focus.image"] = "localized {image}",
    ["focus.text"] = "localized text"
};
CheckFocus(Model("$focus.image"), 1, true, "localized image contents request an image");
CheckFocus(Model("$focus.text"), 0, true, "localized text contents request no image");

// Test the actual substitution result when image acquisition fails. Details keep
// their original order after the attempted image replacement.
var replace = typeof(FocusHandler).GetMethod("ReplacePlaceholders", flags)!;
int unavailableCalls = 0;
var unavailable = new Lazy<MaaImageBuffer?>(() => { unavailableCalls++; return null; });
var text = (string)replace.Invoke(handler, ["{name}: {image}", new JObject { ["name"] = "result" }, unavailable])!;
Check(text == "result: {image}" && unavailableCalls == 1, "missing image preserves text and detail substitution");
replace.Invoke(handler, ["{image}", null, unavailable]);
Check(unavailableCalls == 1, "failed image acquisition is not repeated within the message");
Console.WriteLine($"FOCUS_IMAGE_TESTS_OK: {passed}");
