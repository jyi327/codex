# Tests only disposable VHDs created in this job; never selects a physical disk.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'This disposable-disk check runs only on GitHub Actions.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host '::warning title=USB filesystem checks::Virtual FAT32/exFAT checks need an elevated build runner.'
    exit 0
}
foreach ($format in @('FAT32', 'exFAT')) {
    $token = [guid]::NewGuid().ToString('N')
    $disk = Join-Path $env:RUNNER_TEMP "y2usb-test-$token.vhd"
    $setup = Join-Path $env:RUNNER_TEMP "y2usb-setup-$token.txt"
    $detach = Join-Path $env:RUNNER_TEMP "y2usb-detach-$token.txt"
    $used = @(Get-Volume | Where-Object DriveLetter | ForEach-Object { [string]$_.DriveLetter })
    $letter = @('Z','Y','X','W','V','U','T','S','R') | Where-Object { $_ -notin $used -and -not (Test-Path ('{0}:\' -f $_)) } | Select-Object -First 1
    if (-not $letter) { throw 'No unused test drive letter is available.' }
    @"
create vdisk file="$disk" maximum=128 type=expandable
select vdisk file="$disk"
attach vdisk
create partition primary
format fs=$format quick label=Y2USBTEST
assign letter=$letter
"@ | Set-Content -LiteralPath $setup -Encoding ascii
    @"
select vdisk file="$disk"
detach vdisk
"@ | Set-Content -LiteralPath $detach -Encoding ascii
    try {
        $setupText = (& diskpart.exe /s $setup 2>&1) -join "`n"
        if ($LASTEXITCODE -ne 0 -or $setupText -match '(?i)error|failed') { throw "Disposable $format disk setup failed: $setupText" }
        $image = Get-DiskImage -ImagePath $disk
        $imageDisk = $image | Get-Disk
        $partition = Get-Partition -DriveLetter $letter
        $volume = Get-Volume -DriveLetter $letter
        if (-not $image.Attached -or $partition.DiskNumber -ne $imageDisk.Number -or $volume.FileSystemLabel -ne 'Y2USBTEST') {
            throw 'The test volume is not the disposable VHD. Refusing to continue.'
        }
        python scripts/check-usb-filesystem.py ('{0}:\' -f $letter) $format 2>&1 | Tee-Object -Variable checkOutput
        if ($LASTEXITCODE -ne 0) {
            $reason = ($checkOutput -join "`n")
            $reason = $reason.Substring([Math]::Max(0, $reason.Length - 3500)).Replace('%','%25').Replace("`r",'%0D').Replace("`n",'%0A')
            Write-Host "::error title=$format USB deletion::$reason"
            throw "$format USB filesystem check failed"
        }
        Write-Host "::notice title=$format USB deletion::Confirmed deletion passed on a disposable $format filesystem."
    } finally {
        if (Test-Path -LiteralPath $disk) {
            & diskpart.exe /s $detach | Out-Null
            Remove-Item -LiteralPath $disk -Force -ErrorAction SilentlyContinue
        }
        Remove-Item -LiteralPath $setup,$detach -Force -ErrorAction SilentlyContinue
    }
}
