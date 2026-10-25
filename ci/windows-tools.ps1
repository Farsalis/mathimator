# Install the pinned tools a job on the hosted Windows runner needs into the
# checkout, and put them on PATH for the rest of the job (ci/windows.yml):
#
#   ./ci/windows-tools.ps1 [-Uv] [-Node] [-Rust]
#
#   -Uv    uv UV_VERSION from packaging/versions.env, checked against
#          UV_WINDOWS_SHA256, as packaging/build-bundle.ps1 checks it.
#   -Node  Node.js $env:NODE_VERSION, checked against the SHASUMS256.txt that
#          nodejs.org publishes for the release.
#   -Rust  rustup, then the toolchain that desktop/src-tauri/rust-toolchain.toml
#          names, with CARGO_HOME at .cargo in the checkout.
#
# Run it with & or ./ rather than in a new powershell.exe: PATH and CARGO_HOME
# belong to this process, so a child process would lose them.

param([switch]$Uv, [switch]$Node, [switch]$Rust)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Tools = Join-Path $Root ".tools"
New-Item -ItemType Directory -Force -Path $Tools | Out-Null

function Invoke-Checked([string]$File, [string[]]$CommandArgs) {
    & $File @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "exit $LASTEXITCODE from $File"
    }
}

function Get-Download([string]$Url, [string]$Destination) {
    Invoke-Checked "curl.exe" @("-fsSL", "--retry", "5", "--retry-delay", "2", "-o", $Destination, $Url)
}

function Assert-Sha256([string]$Path, [string]$Sha256) {
    $actual = (Get-FileHash -Algorithm SHA256 $Path).Hash
    if ([string]::IsNullOrEmpty($Sha256) -or $actual -ne $Sha256) {
        throw "SHA-256 of $Path is $actual, not the pinned '$Sha256'"
    }
}

function Add-Path([string]$Directory) {
    $env:PATH = "$Directory;$env:PATH"
}

if ($Uv) {
    $pins = @{}
    foreach ($line in Get-Content -LiteralPath (Join-Path $Root "packaging\versions.env")) {
        if ($line -match "^([A-Z0-9_]+)=(.*)$") {
            $pins[$Matches[1]] = $Matches[2]
        }
    }
    $zip = Join-Path $Tools "uv-x86_64-pc-windows-msvc.zip"
    Get-Download "https://github.com/astral-sh/uv/releases/download/$($pins.UV_VERSION)/uv-x86_64-pc-windows-msvc.zip" $zip
    Assert-Sha256 $zip $pins.UV_WINDOWS_SHA256
    $dir = Join-Path $Tools "uv"
    Expand-Archive -Force -Path $zip -DestinationPath $dir
    Add-Path $dir
    Invoke-Checked "uv" @("--version")
}

if ($Node) {
    if ([string]::IsNullOrEmpty($env:NODE_VERSION)) {
        throw "NODE_VERSION is not set"
    }
    $name = "node-v$env:NODE_VERSION-win-x64"
    $release = "https://nodejs.org/dist/v$env:NODE_VERSION"
    $sums = Join-Path $Tools "node-SHASUMS256.txt"
    Get-Download "$release/SHASUMS256.txt" $sums
    $entry = Select-String -LiteralPath $sums -Pattern "^([0-9a-f]{64})  $([regex]::Escape("$name.zip"))$"
    if (-not $entry) {
        throw "SHASUMS256.txt of Node.js $env:NODE_VERSION lists no $name.zip"
    }
    $zip = Join-Path $Tools "$name.zip"
    Get-Download "$release/$name.zip" $zip
    Assert-Sha256 $zip $entry.Matches[0].Groups[1].Value
    Expand-Archive -Force -Path $zip -DestinationPath $Tools
    Add-Path (Join-Path $Tools $name)
    Invoke-Checked "node" @("--version")
}

if ($Rust) {
    $env:CARGO_HOME = Join-Path $Root ".cargo"
    $init = Join-Path $Tools "rustup-init.exe"
    Get-Download "https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe" $init
    Invoke-Checked $init @("-y", "--no-modify-path", "--profile", "minimal", "--default-toolchain", "none")
    Add-Path (Join-Path $env:CARGO_HOME "bin")
    # rustup installs the toolchain of the rust-toolchain.toml it finds from the
    # working directory, with that file's components.
    Push-Location (Join-Path $Root "desktop\src-tauri")
    try {
        Invoke-Checked "rustup" @("toolchain", "install")
        Invoke-Checked "cargo" @("--version")
    } finally {
        Pop-Location
    }
}
