# Regenerates tests/fixtures/lecture.m4a from lecture.txt with Windows' built-in TTS (offline).
# Usage: powershell -ExecutionPolicy Bypass -File scripts\make_fixture.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$txt = Join-Path $root 'tests\fixtures\lecture.txt'
$wav = Join-Path $env:TEMP 'lecturelab-fixture.wav'
$out = Join-Path $root 'tests\fixtures\lecture.m4a'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'en-*' } | Select-Object -First 1
if ($voice) { $s.SelectVoice($voice.VoiceInfo.Name) }
$s.Rate = 0
$s.SetOutputToWaveFile($wav)
$s.Speak((Get-Content -LiteralPath $txt -Raw))
$s.Dispose()
ffmpeg -v error -y -i $wav -ac 1 -ar 16000 -c:a aac -b:a 48k -movflags +faststart $out
Remove-Item $wav
Write-Host "Wrote $out"
