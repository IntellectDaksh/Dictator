Add-Type -AssemblyName System.Speech
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
Get-Content "$PSScriptRoot\clips.tsv" | ForEach-Object {
  $id, $rate, $voice, $text = $_ -split "`t"
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $s.SelectVoice("Microsoft $voice Desktop"); $s.Rate = [int]$rate
  $s.SetOutputToWaveFile("$PSScriptRoot\clips\$id.wav", $fmt); $s.Speak($text); $s.Dispose()
  Set-Content -Encoding utf8 "$PSScriptRoot\clips\$id.txt" $text
}
