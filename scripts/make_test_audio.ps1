# Generates Polish test recordings (16 kHz mono WAV) for the STT benchmark using the
# built-in Windows "Microsoft Paulina" voice. Real voice tests: python -m wvt.cli mic
param(
    [string]$Benchmark = "$PSScriptRoot\..\wvt\data\benchmark.json",
    [string]$OutDir = "$PSScriptRoot\..\tests\audio"
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$data = Get-Content -Raw -Encoding UTF8 $Benchmark | ConvertFrom-Json
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $synth.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq "pl-PL" -and $_.Enabled } | Select-Object -First 1
if (-not $voice) { throw "Brak polskiego glosu TTS (Microsoft Paulina) w systemie." }
$synth.SelectVoice($voice.VoiceInfo.Name)
$synth.Rate = 1
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$i = 0
foreach ($item in $data.pl_to_en) {
    $i++
    $path = Join-Path $OutDir ("pl_{0:D2}.wav" -f $i)
    $synth.SetOutputToWaveFile($path, $fmt)
    $synth.Speak($item.pl)
}
$synth.SetOutputToNull()
$synth.Dispose()
Write-Output "Zapisano $i plikow w $OutDir (glos: $($voice.VoiceInfo.Name))"
