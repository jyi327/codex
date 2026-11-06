# Build-time prerequisites only: Python 3.12, FFmpeg/ffprobe/ffplay and Node.js on PATH.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
function Check-Exit { if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" } }
python -m venv .build-venv
Check-Exit
$python = Join-Path $PSScriptRoot '.build-venv\Scripts\python.exe'
& $python -m pip install 'pyinstaller==6.16.0' -r requirements-ui.txt
Check-Exit
$tools = Join-Path $PSScriptRoot 'build\portable-tools'
New-Item -ItemType Directory -Force $tools | Out-Null
foreach ($tool in @('ffmpeg.exe', 'ffprobe.exe', 'ffplay.exe', 'node.exe')) {
    $source = (Get-Command $tool -ErrorAction Stop).Source
    if ($source -match '[\\/]chocolatey[\\/]bin[\\/]') {
        throw "Found a Chocolatey shim for $tool. Put the actual binary directory first on PATH."
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $tools $tool) -Force
}
# Download the upstream standalone downloader and verify its published checksum.
$release = 'https://github.com/yt-dlp/yt-dlp/releases/latest/download'
Invoke-WebRequest "$release/yt-dlp.exe" -OutFile "$tools\yt-dlp.exe"
Invoke-WebRequest "$release/yt-dlp_win.zip" -OutFile "$tools\yt-dlp_win.zip"
Invoke-WebRequest "$release/SHA2-256SUMS" -OutFile "$tools\SHA2-256SUMS"
foreach ($asset in @('yt-dlp.exe', 'yt-dlp_win.zip')) {
    $pattern = '^([a-fA-F0-9]{64})\s+\*?' + [regex]::Escape($asset) + '$'
    $line = @(Get-Content "$tools\SHA2-256SUMS" | Where-Object { $_ -match $pattern })
    if ($line.Count -ne 1) { throw "Expected exactly one checksum for $asset" }
    $expected = ($line[0] -split '\s+')[0]
    if ((Get-FileHash (Join-Path $tools $asset) -Algorithm SHA256).Hash -ne $expected) { throw "Checksum mismatch: $asset" }
}
$downloader = Join-Path $tools 'downloader'
if (Test-Path $downloader) { Remove-Item $downloader -Recurse -Force }
Expand-Archive "$tools\yt-dlp_win.zip" -DestinationPath $downloader
if (-not (Test-Path "$downloader\yt-dlp.exe")) { throw 'Unpackaged downloader executable not found' }
function Measure-DownloaderStartup($executable) {
    $times = @()
    foreach ($sample in 1..3) {
        $timer = [System.Diagnostics.Stopwatch]::StartNew()
        & $executable --version | Out-Null
        Check-Exit
        $timer.Stop()
        $times += $timer.ElapsedMilliseconds
    }
    return ($times | Sort-Object)[1]
}
$oldStartup = Measure-DownloaderStartup "$tools\yt-dlp.exe"
$newStartup = Measure-DownloaderStartup "$downloader\yt-dlp.exe"
$startupReport = "Downloader startup median (3 runs): self-extracting $oldStartup ms; unpackaged $newStartup ms. Measured on the build runner, not a YouTube download."
Write-Host $startupReport
if ($env:GITHUB_STEP_SUMMARY) { $startupReport | Out-File $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8 }
& $python -m PyInstaller --noconfirm --clean --windowed --onedir --name YouTubeToUSB --collect-all customtkinter --add-binary "$tools\ffmpeg.exe;tools" --add-binary "$tools\ffprobe.exe;tools" --add-binary "$tools\ffplay.exe;tools" --add-binary "$tools\node.exe;tools" --add-data "$downloader;tools/downloader" app.py
Check-Exit
$smoke = Start-Process -FilePath "$PSScriptRoot\dist\YouTubeToUSB\YouTubeToUSB.exe" -ArgumentList '--smoke-test' -PassThru
if (-not $smoke.WaitForExit(30000)) {
    $smoke.Kill()
    throw 'Packaged app did not finish its startup check within 30 seconds'
}
if ($smoke.ExitCode -ne 0) { throw "Packaged app startup failed: $($smoke.ExitCode)" }
$conversionReport = Join-Path $PSScriptRoot 'build\conversion-smoke-error.txt'
Remove-Item $conversionReport -ErrorAction SilentlyContinue
$conversion = Start-Process -FilePath "$PSScriptRoot\dist\YouTubeToUSB\YouTubeToUSB.exe" -ArgumentList @('--conversion-test', "`"$conversionReport`"") -PassThru
if (-not $conversion.WaitForExit(90000)) {
    taskkill /PID $conversion.Id /T /F | Out-Null
    throw 'Bundled audio conversion did not finish within 90 seconds'
}
if ($conversion.ExitCode -ne 0) {
    if (Test-Path $conversionReport) { Get-Content $conversionReport | Write-Host }
    throw "Bundled audio conversion failed: $($conversion.ExitCode)"
}
$previewReport = Join-Path $PSScriptRoot 'build\preview-smoke-error.txt'
Remove-Item $previewReport -ErrorAction SilentlyContinue
$previewCheck = Start-Process -FilePath "$PSScriptRoot\dist\YouTubeToUSB\YouTubeToUSB.exe" -ArgumentList @('--preview-test', "`"$previewReport`"") -PassThru
if (-not $previewCheck.WaitForExit(30000)) {
    taskkill /PID $previewCheck.Id /T /F | Out-Null
    throw 'Bundled preview player did not finish within 30 seconds'
}
if ($previewCheck.ExitCode -ne 0) {
    if (Test-Path $previewReport) { Get-Content $previewReport | Write-Host }
    throw "Bundled preview playback failed: $($previewCheck.ExitCode)"
}
Write-Host '::notice title=Audio preview::Bundled player decoded and played a test tone through a dummy audio device.'
$searchReport = Join-Path $PSScriptRoot 'dist\YouTubeToUSB\SearchCheck.txt'
$searchCheck = Start-Process -FilePath "$PSScriptRoot\dist\YouTubeToUSB\YouTubeToUSB.exe" -ArgumentList @('--search-test', "`"$searchReport`"") -PassThru
if (-not $searchCheck.WaitForExit(60000)) {
    taskkill /PID $searchCheck.Id /T /F | Out-Null
    'Live search check timed out on the build network.' | Out-File $searchReport -Encoding utf8
}
if (Test-Path $searchReport) {
    $searchText = (Get-Content $searchReport -Raw) -replace '[\r\n]+', ' '
    Write-Host $searchText
    if ($searchText.StartsWith('Live search succeeded:')) {
        Write-Host '::notice title=Live YouTube search::Packaged app returned search results successfully.'
    } else {
        Write-Host '::warning title=Live YouTube search::Live search could not be verified on this network. See SearchCheck.txt.'
    }
    if ($searchText.Contains('Thumbnail download succeeded.')) {
        Write-Host '::notice title=Video thumbnails::Packaged app downloaded and decoded a YouTube thumbnail.'
    }
    if ($env:GITHUB_STEP_SUMMARY) { Get-Content $searchReport | Out-File $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8 }
} else {
    throw 'Packaged search check did not produce a report'
}
Copy-Item README.md dist\YouTubeToUSB\README.md -Force
$previewStreamReport = Join-Path $PSScriptRoot 'dist\YouTubeToUSB\PreviewCheck.txt'
$previewStream = Start-Process -FilePath "$PSScriptRoot\dist\YouTubeToUSB\YouTubeToUSB.exe" -ArgumentList @('--preview-stream-test', "`"$previewStreamReport`"") -PassThru
if (-not $previewStream.WaitForExit(90000)) {
    taskkill /PID $previewStream.Id /T /F | Out-Null
    'Live preview check timed out on the build network.' | Out-File $previewStreamReport -Encoding utf8
}
if ((Test-Path $previewStreamReport) -and (Get-Content $previewStreamReport -Raw).StartsWith('Live audio preview succeeded:')) {
    Write-Host '::notice title=Live audio preview::Packaged app streamed a YouTube audio sample through a dummy audio device.'
} else {
    Write-Host '::warning title=Live audio preview::Live audio preview could not be verified on this network. See PreviewCheck.txt.'
}
$startupReport | Out-File dist\YouTubeToUSB\StartupTiming.txt -Encoding utf8
Compress-Archive -Path dist\YouTubeToUSB -DestinationPath dist\YouTubeToUSB-Windows-x64.zip -Force
Write-Host 'Built dist\YouTubeToUSB-Windows-x64.zip. Extract the entire ZIP before starting the app.'
