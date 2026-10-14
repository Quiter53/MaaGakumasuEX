param([string]$UiRoot = (Join-Path $PSScriptRoot '../build-mfa-ui/MFAAvalonia'))
$ErrorActionPreference = 'Stop'
$overlay = Join-Path $PSScriptRoot '../ui-patches/MFAAvalonia'
foreach ($relative in @('ViewModels/Pages/DrinkSettingsViewModel.cs','Views/Pages/DrinkSettingsView.axaml','Views/Pages/DrinkSettingsView.axaml.cs')) {
    Copy-Item -LiteralPath (Join-Path $overlay $relative) -Destination (Join-Path $UiRoot $relative) -Force
}
# Idempotent integration into the external client source used for builds.
$app = Join-Path $UiRoot 'App.axaml.cs'
$source = [IO.File]::ReadAllText($app)
if (-not $source.Contains('.AddView<DrinkSettingsView, DrinkSettingsViewModel>')) {
    $anchor = '.AddView<CardListSettingsView, CardListSettingsViewModel>(services)'
    if (-not $source.Contains($anchor)) { throw 'Card list registration not found' }
    [IO.File]::WriteAllText($app, $source.Replace($anchor, "$anchor`n            .AddView<DrinkSettingsView, DrinkSettingsViewModel>(services)"), [Text.UTF8Encoding]::new($false))
}
$view = Join-Path $UiRoot 'Views/Mobile/RootViewContent.axaml'
$source = [IO.File]::ReadAllText($view)
if (-not $source.Contains('pages:DrinkSettingsView')) {
    $anchor = '                    </suki:SukiSideMenu.Items>'
    if (-not $source.Contains($anchor)) { throw 'Side menu anchor not found' }
    $item = @'
                        <suki:SukiSideMenuItem IsContentMovable="False" Header="饮料策略"
                            Icon="{fluent:FluentIcon Icon=DrinkCoffee, IconVariant=Regular}"
                            PageContent="{extensions:ServiceProvider ServiceType={x:Type pages:DrinkSettingsView}}" />
'@
    [IO.File]::WriteAllText($view, $source.Replace($anchor, "$item`n$anchor"), [Text.UTF8Encoding]::new($false))
}
