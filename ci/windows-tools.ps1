# Install the pinned tools that a job on a GitHub-hosted Windows runner needs,
# and put them on PATH for the rest of the job (.github/workflows):
#
#   ./ci/windows-tools.ps1 [-Uv] [-Rust]
#
#   -Uv    uv UV_VERSION from packaging/versions.env, into .tools in the
#          checkout, checked against UV_WINDOWS_SHA256, as
#          packaging/build-bundle.ps1 checks it.
#   -Rust  the toolchain that desktop/src-tauri/rust-toolchain.toml names,
#          installed by the rustup on PATH. GitHub's hosted runners bring one;
#          elsewhere the script first installs rustup, with CARGO_HOME at
#          .cargo in the checkout.
#
# Run it with & or ./ rather than in a new powershell.exe: PATH and CARGO_HOME
# belong to this process, so a child process would lose them. In a GitHub
# Actions step the script also writes them to GITHUB_PATH and GITHUB_ENV, so
# the later steps of the job inherit them.

param([switch]$Uv, [switch]$Rust)

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
    if ($env:GITHUB_PATH) {
        Add-Content -LiteralPath $env:GITHUB_PATH -Value $Directory
    }
}

function Set-JobVariable([string]$Name, [string]$Value) {
    Set-Item -LiteralPath "env:$Name" -Value $Value
    if ($env:GITHUB_ENV) {
        Add-Content -LiteralPath $env:GITHUB_ENV -Value "$Name=$Value"
    }
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

if ($Rust) {
    if (-not (Get-Command rustup -ErrorAction SilentlyContinue)) {
        Set-JobVariable "CARGO_HOME" (Join-Path $Root ".cargo")
        $init = Join-Path $Tools "rustup-init.exe"
        Get-Download "https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe" $init
        Invoke-Checked $init @("-y", "--no-modify-path", "--profile", "minimal", "--default-toolchain", "none")
        Add-Path (Join-Path $env:CARGO_HOME "bin")
    }
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
