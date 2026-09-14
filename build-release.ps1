[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+([-.][0-9A-Za-z.-]+)?$')]
    [string]$Version,

    [ValidateSet('http', 'github')]
    [string]$Source = 'github',

    [ValidatePattern('^https://')]
    [string]$FeedUrl = 'https://github.com/jmlinguito-ux/gdpt',

    [string]$ReleaseNotes = '',
    [string]$OutputDirectory = 'Releases'
)

$ErrorActionPreference = 'Stop'
$projectDirectory = $PSScriptRoot
$generatedDirectory = Join-Path $projectDirectory 'build\release-config'
$generatedConfig = Join-Path $generatedDirectory 'update_config.json'
$packDirectory = Join-Path $projectDirectory 'dist\Ground-Data-Processing-Tool'
$releaseDirectory = Join-Path $projectDirectory $OutputDirectory

New-Item -ItemType Directory -Force -Path $generatedDirectory | Out-Null
@{
    version = $Version
    source = $Source
    url = $FeedUrl.TrimEnd('/')
    prerelease = $false
} | ConvertTo-Json | Set-Content -LiteralPath $generatedConfig -Encoding utf8

$env:GDPT_UPDATE_CONFIG = $generatedConfig
try {
    & py -m PyInstaller --clean --noconfirm (Join-Path $projectDirectory 'Ground-Data-Processing-Tool.spec')
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
} finally {
    Remove-Item Env:GDPT_UPDATE_CONFIG -ErrorAction SilentlyContinue
}

if (-not (Test-Path -LiteralPath (Join-Path $packDirectory 'Ground-Data-Processing-Tool.exe'))) {
    throw "Expected packaged application was not found in $packDirectory."
}
if (-not (Get-Command vpk -ErrorAction SilentlyContinue)) {
    throw 'Velopack CLI is missing. Install it once with: dotnet tool install -g vpk --version 1.2.0'
}

$packArgs = @(
    'pack',
    '--packId', 'GroundDataProcessingTool',
    '--packVersion', $Version,
    '--packDir', $packDirectory,
    '--mainExe', 'Ground-Data-Processing-Tool.exe',
    '--packTitle', 'Ground Data Processing Tool',
    '--packAuthors', 'Ground Data Team',
    '--channel', 'win',
    '--outputDir', $releaseDirectory,
    '--icon', (Join-Path $projectDirectory 'icon.ico'),
    '--splashImage', (Join-Path $projectDirectory 'splash.png')
)
if ($ReleaseNotes) {
    $notesPath = (Resolve-Path -LiteralPath $ReleaseNotes).Path
    $packArgs += @('--releaseNotes', $notesPath)
}

& vpk @packArgs
if ($LASTEXITCODE -ne 0) { throw "Velopack packaging failed with exit code $LASTEXITCODE." }

Write-Host "Release $Version created in $releaseDirectory"
Write-Host 'Distribute the generated Setup.exe for the first install, then publish every generated release file to the configured feed.'
