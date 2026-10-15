<#
.SYNOPSIS
  Start LectureLab: backend + built frontend on http://127.0.0.1:8765, tray icon, hotkeys.
.EXAMPLE
  .\start.ps1               # normal start (opens the browser)
  .\start.ps1 -NoBrowser
  .\start.ps1 -Rebuild      # force a frontend rebuild first
#>
param(
    [switch]$NoBrowser,
    [switch]$Rebuild
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

foreach ($tool in 'uv', 'ffmpeg') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Error "$tool was not found on PATH. See README.md > Setup."
    }
}

# Build the frontend when it's missing or older than its sources.
$dist = Join-Path $PSScriptRoot 'frontend\dist\index.html'
$needBuild = $Rebuild -or -not (Test-Path -LiteralPath $dist)
if (-not $needBuild) {
    $built = (Get-Item -LiteralPath $dist).LastWriteTime
    $newest = Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'frontend\src') -Recurse -File |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $needBuild = $newest -and $newest.LastWriteTime -gt $built
}
if ($needBuild) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Error 'npm was not found on PATH; it is needed to build the frontend.'
    }
    Write-Host 'Building frontend...' -ForegroundColor Cyan
    Push-Location -LiteralPath (Join-Path $PSScriptRoot 'frontend')
    try {
        if (-not (Test-Path -LiteralPath 'node_modules')) { npm install --no-fund --no-audit }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    } finally { Pop-Location }
}

# Update the environment first. This fails when lecturelab.exe is in use (another LectureLab
# window, or a dev instance started from it): then start with what is installed instead of
# not starting at all. The code itself is always the current one (editable install).
& uv sync --inexact
$serveArgs = @('run', 'lecturelab', 'serve')
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'Could not update the environment (a file is in use); starting with the installed version.'
    $serveArgs = @('run', '--no-sync', 'lecturelab', 'serve')
}
if ($NoBrowser) { $serveArgs += '--no-browser' }
& uv @serveArgs
