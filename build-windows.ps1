# Build-time prerequisites only: Python 3.12, FFmpeg/ffprobe and Node.js on PATH.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
function Check-Exit { if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" } }
python -m venv .build-venv
Check-Exit
$python = Join-Path $PSScriptRoot '.build-venv\Scripts\python.exe'
& $python -m pip install 'pyinstaller==6.16.0'
Check-Exit
$tools = Join-Path $PSScriptRoot 'build\portable-tools'
New-Item -ItemType Directory -Force $tools | Out-Null
foreach ($tool in @('ffmpeg.exe', 'ffprobe.exe', 'node.exe')) {
    $source = (Get-Command $tool -ErrorAction Stop).Source
    if ($source -match '[\\/]chocolatey[\\/]bin[\\/]') {
        throw "Found a Chocolatey shim for $tool. Put the actual binary directory first on PATH."
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $tools $tool) -Force
}
# Download the upstream standalone downloader and verify its published checksum.
$release = 'https://github.com/yt-dlp/yt-dlp/releases/latest/download'
Invoke-WebRequest "$release/yt-dlp.exe" -OutFile "$tools\yt-dlp.exe"
Invoke-WebRequest "$release/SHA2-256SUMS" -OutFile "$tools\SHA2-256SUMS"
$line = Get-Content "$tools\SHA2-256SUMS" | Where-Object { $_ -match '^([a-fA-F0-9]{64})\s+\*?yt-dlp\.exe$' }
if (@($line).Count -ne 1) { throw 'Expected exactly one yt-dlp.exe checksum' }
$expected = ($line -split '\s+')[0]
if ((Get-FileHash "$tools\yt-dlp.exe" -Algorithm SHA256).Hash -ne $expected) { throw 'yt-dlp checksum mismatch' }
& "$tools\yt-dlp.exe" --version
Check-Exit
& $python -m PyInstaller --noconfirm --clean --windowed --onedir --name YouTubeToUSB --add-binary "$tools\ffmpeg.exe;tools" --add-binary "$tools\ffprobe.exe;tools" --add-binary "$tools\node.exe;tools" --add-binary "$tools\yt-dlp.exe;tools" app.py
Check-Exit
Copy-Item README.md dist\YouTubeToUSB\README.md -Force
Compress-Archive -Path dist\YouTubeToUSB -DestinationPath dist\YouTubeToUSB-Windows-x64.zip -Force
Write-Host 'Built dist\YouTubeToUSB-Windows-x64.zip. Extract the entire ZIP before starting the app.'
