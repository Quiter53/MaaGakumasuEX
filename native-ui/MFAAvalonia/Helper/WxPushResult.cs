using System.Text.Json;

namespace MFAAvalonia.Helper;

public static class WxPushResult
{
    public static bool Accepted(string body)
    {
        try
        {
            using var document = JsonDocument.Parse(body);
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object) return false;
            bool accepted = false;
            if (root.TryGetProperty("code", out var code))
            {
                if (!code.TryGetInt32(out int value) || value != 1000) return false;
                accepted = true;
            }
            if (root.TryGetProperty("success", out var success))
            {
                if (success.ValueKind == JsonValueKind.False) return false;
                accepted |= success.ValueKind == JsonValueKind.True;
            }
            if (!accepted) return false;
            if (root.TryGetProperty("data", out var data) && data.ValueKind == JsonValueKind.Array)
            {
                if (data.GetArrayLength() == 0) return false;
                foreach (var recipient in data.EnumerateArray())
                    if (recipient.TryGetProperty("code", out var deliveryCode)
                        && (!deliveryCode.TryGetInt32(out int deliveryValue) || deliveryValue != 1000)) return false;
            }
            return true;
        }
        catch (JsonException) { return false; }
        catch (System.InvalidOperationException) { return false; }
    }
}
