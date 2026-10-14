param([string]$UiRoot = (Join-Path $PSScriptRoot '../build-mfa-ui/MFAAvalonia'))
$ErrorActionPreference = 'Stop'
$overlay = Join-Path $PSScriptRoot '../ui-patches/MFAAvalonia'
foreach ($relative in @('ViewModels/Pages/CardRulesViewModel.cs','Views/Pages/CardRulesView.cs','Views/Pages/CardRulesDialogs.cs','Views/Pages/CardRuleCurve.cs')) {
    Copy-Item -LiteralPath (Join-Path $overlay $relative) -Destination (Join-Path $UiRoot $relative) -Force
}
$app = Join-Path $UiRoot 'App.axaml.cs'
$source = [IO.File]::ReadAllText($app)
if (-not $source.Contains('.AddView<CardRulesView, CardRulesViewModel>')) {
    $anchor = '.AddView<DrinkSettingsView, DrinkSettingsViewModel>(services)'
    if (-not $source.Contains($anchor)) { throw 'Drink settings registration not found' }
    [IO.File]::WriteAllText($app, $source.Replace($anchor, "$anchor`n            .AddView<CardRulesView, CardRulesViewModel>(services)"), [Text.UTF8Encoding]::new($false))
}
$view = Join-Path $UiRoot 'Views/Mobile/RootViewContent.axaml'
$source = [IO.File]::ReadAllText($view)
if (-not $source.Contains('pages:CardRulesView')) {
    $anchor = '                    </suki:SukiSideMenu.Items>'
    if (-not $source.Contains($anchor)) { throw 'Side menu anchor not found' }
    $item = @'
                        <suki:SukiSideMenuItem IsContentMovable="False" Header="出牌规则"
                            Icon="{fluent:FluentIcon Icon=Options, IconVariant=Regular}"
                            PageContent="{extensions:ServiceProvider ServiceType={x:Type pages:CardRulesView}}" />
'@
    [IO.File]::WriteAllText($view, $source.Replace($anchor, "$item`n$anchor"), [Text.UTF8Encoding]::new($false))
}
