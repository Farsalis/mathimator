# Build the Windows NSIS installer as a release ships it, for package:windows
# in ci/windows.yml (D73, F061). Node and Rust must be on PATH
# (ci/windows-tools.ps1 -Node -Rust).
#
#   1. packaging/build-bundle.ps1 builds packaging/bundle.
#   2. npm ci, and cargo fetch, because the notices read the installed npm
#      packages and run cargo metadata offline.
#   3. The bundled interpreter runs scripts/generate_notices.py, which writes
#      THIRD_PARTY_NOTICES.txt (D75), then scripts/check_bundle.py, which
#      fails on a bundle that is stale, incomplete or for another platform
#      (D30, D64).
#   4. tauri build makes the installer.
#   5. out/package receives the installer, THIRD_PARTY_NOTICES.txt and
#      SHA256SUMS for both.
#
# Signing (D74): v1 ships unsigned, so the hook is off. Setting the protected
# CI/CD variable WINDOWS_SIGN_COMMAND to a Tauri bundle.windows.signCommand
# (%1 stands for the file) makes tauri build sign mathimator.exe and the
# installer with it. GitLab cannot mask a value with spaces or %, so the
# variable holds only the command line: any secret the signing tool needs,
# such as a certificate password or Azure Trusted Signing credentials, goes in
# its own protected, masked variable, which the tool reads from the
# environment. Adopting signing then takes no change here.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Desktop = Join-Path $Root "desktop"
$ShellCrate = Join-Path $Desktop "src-tauri"
$Bundle = Join-Path $Root "packaging\bundle"
$Out = Join-Path $Root "out\package"
$Utf8 = New-Object System.Text.UTF8Encoding $false

function Invoke-Checked([string]$File, [string[]]$CommandArgs) {
    Write-Host ("run {0} {1}" -f $File, ($CommandArgs -join " "))
    & $File @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "exit $LASTEXITCODE from $File"
    }
}

function Invoke-In([string]$Directory, [string]$File, [string[]]$CommandArgs) {
    Push-Location $Directory
    try {
        Invoke-Checked $File $CommandArgs
    } finally {
        Pop-Location
    }
}

# Windows PowerShell 5.1, the host packaging/build-bundle.sh runs it in.
Invoke-Checked "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "packaging\build-bundle.ps1"))

Invoke-In $Desktop "npm" @("ci", "--prefer-offline", "--no-audit", "--no-fund")
# rustup reads the toolchain from desktop/src-tauri, so cargo runs there.
Invoke-In $ShellCrate "cargo" @("fetch", "--locked")

# -I -B: the scripts read no PYTHON* variable and leave no bytecode behind.
$runtime = Get-Content -Raw (Join-Path $Bundle "runtime.json") | ConvertFrom-Json
$python = Join-Path $Bundle $runtime.python
Invoke-Checked $python @("-I", "-B", (Join-Path $Root "scripts\generate_notices.py"), $Bundle)
Invoke-Checked $python @("-I", "-B", (Join-Path $Root "scripts\check_bundle.py"), $Bundle)

$tauriArgs = @("build")
if ($env:WINDOWS_SIGN_COMMAND) {
    $signing = Join-Path ([System.IO.Path]::GetTempPath()) "mathimator-signing.json"
    $config = @{ bundle = @{ windows = @{ signCommand = $env:WINDOWS_SIGN_COMMAND } } } | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText($signing, $config, $Utf8)
    $tauriArgs += @("--config", $signing)
    Write-Host "signing with WINDOWS_SIGN_COMMAND"
}
Invoke-In $Desktop (Join-Path $Desktop "node_modules\.bin\tauri.cmd") $tauriArgs

$installers = @(Get-ChildItem -LiteralPath (Join-Path $ShellCrate "target\release\bundle\nsis") -Filter "*-setup.exe")
if ($installers.Count -ne 1) {
    throw "expected one NSIS installer, found $($installers.Count)"
}
if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Force -Path $Out | Out-Null
Copy-Item -LiteralPath $installers[0].FullName, (Join-Path $Bundle "THIRD_PARTY_NOTICES.txt") -Destination $Out
# sha256sum's format, so `sha256sum -c SHA256SUMS` checks a download.
$sums = Get-ChildItem -LiteralPath $Out | Sort-Object Name | ForEach-Object {
    "{0}  {1}`n" -f (Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLowerInvariant(), $_.Name
}
[System.IO.File]::WriteAllText((Join-Path $Out "SHA256SUMS"), (-join $sums), $Utf8)
Get-Content -LiteralPath (Join-Path $Out "SHA256SUMS")
