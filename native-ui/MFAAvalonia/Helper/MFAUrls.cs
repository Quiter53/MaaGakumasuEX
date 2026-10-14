using CommunityToolkit.Mvvm.Input;
using Org.BouncyCastle.Ocsp;
using System;
using System.Diagnostics;
using System.Windows.Input;

namespace MFAAvalonia.Helper;

public class MFAUrls
{
    public const string GitHub = "https://github.com/Quiter53/MaaGakumasuEX";

    public const string GitHubIssues = $"{GitHub}/issues";
    
    public const string GitHubToken = $"https://github.com/settings/tokens";
    
    public const string NewIssueUri = $"{GitHubIssues}/new";

    public const string PurchaseLink = "https://mirrorchyan.com?rid=MFAAvalonia&source=mfaa-software";
}
