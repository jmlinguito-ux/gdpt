[CmdletBinding()]
param(
    [ValidatePattern('^https://github\.com/[^/]+/[^/]+/?$')]
    [string]$RepositoryUrl = 'https://github.com/jmlinguito-ux/gdpt',

    [string]$ReleaseDirectory = 'Releases',
    [string]$Token = $env:GH_TOKEN,
    [switch]$Publish
)

$ErrorActionPreference = 'Stop'
if (-not $Token) {
    throw 'Set GH_TOKEN or pass -Token. The token is used only by vpk and is never embedded in the app.'
}
if (-not (Get-Command vpk -ErrorAction SilentlyContinue)) {
    throw 'Velopack CLI is missing. Install it once with: dotnet tool install -g vpk --version 1.2.0'
}

$uploadArgs = @(
    'upload', 'github',
    '--outputDir', (Join-Path $PSScriptRoot $ReleaseDirectory),
    '--channel', 'win',
    '--repoUrl', $RepositoryUrl.TrimEnd('/'),
    '--token', $Token
)
if ($Publish) { $uploadArgs += @('--publish', 'true') }

& vpk @uploadArgs
if ($LASTEXITCODE -ne 0) { throw "GitHub upload failed with exit code $LASTEXITCODE." }

if ($Publish) { Write-Host 'GitHub release published.' } else { Write-Host 'GitHub draft release uploaded. Review and publish it on GitHub.' }
