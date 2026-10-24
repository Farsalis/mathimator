# Build the phase 5 offline runtime under packaging/bundle/ on Windows.
#
# The product installer is the Windows NSIS bundle described in
# desktop/src-tauri/tauri.windows.conf.json, which ships packaging/bundle as
# runtime/. This script prepares that runtime: a relocatable CPython with the
# Manim render stack from uv.lock installed into it, preseeded TinyTeX, Typst,
# the worker package, the reference-60 sample, and runtime.json. It does not
# build the NSIS installer and it does not sign anything.
#
# packaging/versions.env pins uv, CPython, TinyTeX, the TeX Live snapshot and
# Typst, with a SHA-256 for every download. The interpreter is PYTHON_VERSION,
# or PYTHON_FALLBACK_VERSION only when a locked package has no wheel for it.
# runtime.json is written last, after the bundle has passed its checks.
# `tauri build` then runs scripts/generate_notices.py, which writes
# THIRD_PARTY_NOTICES.txt, and scripts/check_bundle.py, which checks the
# finished bundle, before it makes the installer (beforeBundleCommand).
#
# Windows assets:
#   uv-x86_64-pc-windows-msvc.zip
#   TinyTeX-1-windows-v<TINYTEX_VERSION>.exe
#   typst-x86_64-pc-windows-msvc.zip
#   python-build-standalone CPython at python/python.exe

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Dest = Join-Path $Root "packaging\bundle"
$Cache = Join-Path $Root "packaging\.cache"
$Log = Join-Path $Root "out\bundle.log"
New-Item -ItemType Directory -Force -Path $Cache, $Dest, (Split-Path $Log) | Out-Null

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    Add-Content -Path $Log -Value $line -Encoding utf8
    Write-Host $line
}

function Read-Pins([string]$Path) {
    $pins = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match "^([A-Z0-9_]+)=(.*)$") {
            $pins[$Matches[1]] = $Matches[2]
        }
    }
    return $pins
}

function Download-File([string]$Url, [string]$Destination, [string]$Sha256) {
    if ([string]::IsNullOrEmpty($Sha256)) {
        throw "packaging\versions.env pins no SHA-256 for $Url"
    }
    if (Test-Path $Destination) {
        if ((Get-FileHash -Algorithm SHA256 $Destination).Hash -eq $Sha256) {
            return
        }
        Write-Log "cached $Destination does not match its pinned SHA-256; downloading it again"
        Remove-Item -Force $Destination
    }
    $partial = "$Destination.partial"
    if (Test-Path $partial) { Remove-Item -Force $partial }
    & curl.exe -fL --retry 5 --retry-delay 2 -o $partial $Url
    if ($LASTEXITCODE -ne 0) {
        throw "download failed: $Url"
    }
    $actual = (Get-FileHash -Algorithm SHA256 $partial).Hash
    if ($actual -ne $Sha256) {
        Remove-Item -Force $partial
        throw "SHA-256 of $Url is $actual, not the pinned $Sha256"
    }
    Move-Item -Force $partial $Destination
}

function Expand-Fresh([string]$Archive, [string]$Destination) {
    if (Test-Path $Destination) { Remove-Item -Recurse -Force $Destination }
    Expand-Archive -Path $Archive -DestinationPath $Destination -Force
}

function Invoke-Checked([string]$File, [string[]]$CommandArgs) {
    Write-Log ("run {0} {1}" -f $File, ($CommandArgs -join " "))
    & $File @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "exit $LASTEXITCODE from $File"
    }
}

# Returns the exit code and leaves stdout and stderr in $script:LastOutput.
function Invoke-Captured([string]$File, [string[]]$CommandArgs) {
    Write-Log ("run {0} {1}" -f $File, ($CommandArgs -join " "))
    # Stop turns native stderr, once merged with 2>&1, into a terminating error.
    $previousAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $File @CommandArgs 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousAction
    }
    $text = @($output | ForEach-Object { "$_" })
    $script:LastOutput = ($text -join "`n")
    $text | ForEach-Object { Write-Host $_ }
    return $code
}

$Pins = Read-Pins (Join-Path $PSScriptRoot "versions.env")
$UvVersion = $Pins.UV_VERSION
$PythonVersion = $Pins.PYTHON_VERSION
$PythonFallbackVersion = $Pins.PYTHON_FALLBACK_VERSION
$TinytexVersion = $Pins.TINYTEX_VERSION
$TinytexArchive = "TinyTeX-1-windows-v$TinytexVersion.exe"
$TinytexUrl = "https://github.com/rstudio/tinytex-releases/releases/download/v$TinytexVersion/$TinytexArchive"
$TlnetRepository = $Pins.TLNET_REPOSITORY
$TypstVersion = $Pins.TYPST_VERSION
$TypstArchive = "typst-$TypstVersion-x86_64-pc-windows-msvc.zip"
$TypstUrl = "https://github.com/typst/typst/releases/download/v$TypstVersion/typst-x86_64-pc-windows-msvc.zip"
# Where tlmgr points once the bundle is built. The .invalid domain never
# resolves (RFC 6761), so tlmgr cannot download, and unlike an empty local
# folder it records no build path.
$DisabledRepository = "https://offline.invalid/tlnet"
$Packages = @(
    "standalone",
    "preview",
    "babel",
    "babel-english",
    "amsmath",
    "amsfonts",
    "dvisvgm"
)

Write-Log "bundle destination $Dest"
# Neither may survive from an older build: runtime.json is written last, and
# the notices describe the finished bundle.
$Manifest = Join-Path $Dest "runtime.json"
foreach ($stale in @($Manifest, (Join-Path $Dest "THIRD_PARTY_NOTICES.txt"))) {
    if (Test-Path $stale) { Remove-Item -Force $stale }
}

function Get-Uv {
    # The uv release decides which CPython builds exist (uv 0.10.9 did not list
    # 3.14.9) and resolves the lock, so a uv on PATH is never used. The cached
    # binary is extracted again from the checked archive on every run.
    $zip = Join-Path $Cache "uv-$UvVersion-x86_64-pc-windows-msvc.zip"
    $url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
    Download-File $url $zip $Pins.UV_WINDOWS_SHA256
    $dir = Join-Path $Cache "uv-$UvVersion"
    Expand-Fresh $zip $dir
    $uv = Join-Path $dir "uv.exe"
    if (-not (Test-Path $uv)) {
        throw "uv.exe was not in $zip"
    }
    $uvLine = (& $uv --version | Select-Object -First 1)
    if ($uvLine -notmatch "^uv $([regex]::Escape($UvVersion))(\s|$)") {
        throw "$zip holds $uvLine, not uv $UvVersion"
    }
    return $uv
}

$Uv = Get-Uv
Write-Log "uv $Uv"

$LockFile = Join-Path $Root "uv.lock"
$Requirements = Join-Path $Cache "render-requirements.txt"
$BuildConstraints = Join-Path $Cache "render-build-constraints.txt"
$PythonCache = Join-Path $Cache "python"
$PythonHome = Join-Path $Dest "python"
$Python = Join-Path $PythonHome "python.exe"

function Install-Python([string]$Version) {
    # A real interpreter rather than a venv, whose pyvenv.cfg would point at a
    # CPython that exists only on the build machine. --reinstall downloads the
    # build again on every run, so uv checks its SHA-256 instead of trusting
    # the tree a previous run extracted.
    Invoke-Checked $Uv @("python", "install", $Version, "--install-dir", $PythonCache, "--no-bin", "--no-registry", "--reinstall")
    $source = Join-Path $PythonCache "cpython-$Version-windows-x86_64-none"
    if (-not (Test-Path (Join-Path $source "python.exe"))) {
        throw "uv did not install CPython $Version at $source"
    }
    if (Test-Path $PythonHome) { Remove-Item -Recurse -Force $PythonHome }
    Copy-Item -Recurse $source $PythonHome
}

function Sync-RenderStack {
    # Wheels only, except for packages that publish no wheel at all, so a
    # package without a wheel for this interpreter fails the resolution instead
    # of being compiled on the build machine. The copy keeps uv's
    # EXTERNALLY-MANAGED marker, which keeps pip out of the shipped interpreter.
    $syncArgs = @("pip", "sync", "--python", $Python, "--break-system-packages", "--require-hashes", "--only-binary", ":all:", "--build-constraints", $BuildConstraints)
    foreach ($name in $SourceOnly) {
        $syncArgs += @("--no-binary", $name)
    }
    return Invoke-Captured $Uv ($syncArgs + $Requirements)
}

# The render stack is the lock's render group, with the hashes uv.lock records.
Invoke-Checked $Uv @("export", "--project", $Root, "--frozen", "--only-group", "render", "--no-emit-workspace", "--quiet", "--output-file", $Requirements)
Install-Python $PythonVersion
# A package with no wheel at all is built from its sdist, with the build
# backend pinned in the lock rather than the newest one on build day.
$readLock = @'
import sys, tomllib
from pathlib import Path
lock = tomllib.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
pins = lock['manifest'].get('build-constraints', [])
Path(sys.argv[2]).write_text(''.join(c['name'] + c['specifier'] + '\n' for c in pins), encoding='utf-8')
print(' '.join(p['name'] for p in lock['package'] if 'sdist' in p and not p.get('wheels')))
'@
$SourceOnly = @((& $Python -I -B -c $readLock $LockFile $BuildConstraints) -split " " | Where-Object { $_ })
if ($LASTEXITCODE -ne 0) {
    throw "could not read $LockFile"
}
$pythonSelection = "CPython $PythonVersion"
if ((Sync-RenderStack) -ne 0) {
    # uv reports a package without a usable wheel this way. Any other failure,
    # such as the network, a hash or the disk, stops the build.
    $noWheel = [regex]::Match($script:LastOutput, "Because (\S+) has no usable wheels")
    if (-not $noWheel.Success) {
        throw "uv pip sync failed on CPython $PythonVersion"
    }
    $pythonSelection = "CPython ${PythonFallbackVersion}: $($noWheel.Groups[1].Value) has no CPython $PythonVersion wheel"
    Write-Log $pythonSelection
    Install-Python $PythonFallbackVersion
    if ((Sync-RenderStack) -ne 0) {
        throw "uv pip sync failed on CPython $PythonFallbackVersion"
    }
}
# Console-script launchers hold the build path of python.exe. The worker runs
# Manim with python -m, so none is needed.
$launchers = Join-Path $PythonHome "Scripts"
if (Test-Path $launchers) { Remove-Item -Recurse -Force $launchers }
Write-Log "bundled python: $pythonSelection"

function Find-Latex([string]$RootDir) {
    if (-not (Test-Path $RootDir)) { return $null }
    return Get-ChildItem -Path $RootDir -Filter latex.exe -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Expand-TinyTeXRoot([string]$Destination) {
    # The TinyTeX Windows asset is a 7-Zip SFX, not an Inno Setup installer.
    # /VERYSILENT is rejected. -y -o extracts an inner TinyTeX\ directory.
    $nested = Join-Path $Destination "TinyTeX"
    if (-not (Test-Path (Join-Path $nested "bin"))) {
        return
    }
    Write-Log "flattening $nested into $Destination"
    Get-ChildItem -Force -LiteralPath $nested | ForEach-Object {
        $target = Join-Path $Destination $_.Name
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Recurse -Force
        }
        Move-Item -LiteralPath $_.FullName -Destination $Destination -Force
    }
    Remove-Item -LiteralPath $nested -Force
}

# The stamp names the TinyTeX release the tree was extracted from, so a version
# bump replaces the tree instead of reusing it.
$tinytexRoot = Join-Path $Dest "tinytex"
$tinytexStamp = Join-Path $tinytexRoot ".mathimator-tinytex-release"
if (-not (Test-Path $tinytexStamp) -or (Get-Content -Raw $tinytexStamp).Trim() -ne $TinytexVersion) {
    $installer = Join-Path $Cache $TinytexArchive
    Download-File $TinytexUrl $installer $Pins.TINYTEX_WINDOWS_SHA256
    if (Test-Path $tinytexRoot) { Remove-Item -Recurse -Force $tinytexRoot }
    New-Item -ItemType Directory -Force -Path $tinytexRoot | Out-Null
    Write-Log "extracting TinyTeX 7-Zip SFX into $tinytexRoot"
    & $installer -y "-o$tinytexRoot"
    if ($LASTEXITCODE -ne 0) {
        throw "TinyTeX installer exited $LASTEXITCODE"
    }
    Expand-TinyTeXRoot $tinytexRoot
    Set-Content -Path $tinytexStamp -Value $TinytexVersion -Encoding ascii
}
$latex = Find-Latex $tinytexRoot
if (-not $latex) {
    throw "latex.exe was not found under $tinytexRoot"
}
$TinytexBin = $latex.DirectoryName
Write-Log "TinyTeX bin $TinytexBin"

# tlmgr.bat returns to cmd with perl's errorlevel, but PowerShell reports
# that .bat exit code as 0. Call tlperl directly so a failed install is visible.
# tlmgr writes its own path into the files it generates (language.dat), so it
# runs from the TinyTeX root by a relative path.
$TlmgrPerl = Join-Path $tinytexRoot "tlpkg\tlperl\bin\perl.exe"
$TlmgrPl = "texmf-dist\scripts\texlive\tlmgr.pl"
if (-not (Test-Path $TlmgrPerl) -or -not (Test-Path (Join-Path $tinytexRoot $TlmgrPl))) {
    throw "tlmgr.pl was not found under $tinytexRoot"
}

function Invoke-Tlmgr([string[]]$CommandArgs) {
    $previousPerl = $env:PERL5LIB
    $previousPath = $env:PATH
    $env:PERL5LIB = Join-Path $tinytexRoot "tlpkg\tlperl\lib"
    $env:PATH = "$(Join-Path $tinytexRoot 'tlpkg\tlperl\bin');$TinytexBin;$previousPath"
    Push-Location $tinytexRoot
    try {
        return Invoke-Captured $TlmgrPerl (@($TlmgrPl) + $CommandArgs)
    } finally {
        Pop-Location
        if ([string]::IsNullOrEmpty($previousPerl)) {
            Remove-Item Env:PERL5LIB -ErrorAction SilentlyContinue
        } else {
            $env:PERL5LIB = $previousPerl
        }
        $env:PATH = $previousPath
    }
}

function Assert-Tlmgr([string[]]$CommandArgs) {
    $code = Invoke-Tlmgr $CommandArgs
    if ($code -is [System.Array]) {
        $code = $code[-1]
    }
    if ([int]$code -ne 0) {
        throw "tlmgr exited $code ($($CommandArgs -join ' '))"
    }
}

# A rerun finds the disabled repository from the last build, so point tlmgr at
# the pinned snapshot before anything reaches a repository.
Assert-Tlmgr @("option", "repository", $TlnetRepository)
Write-Log "tlmgr update --self"
Assert-Tlmgr @("update", "--self")
$latexExe = Join-Path $TinytexBin "latex.exe"
if (-not (Test-Path $latexExe)) {
    Assert-Tlmgr @("install", "latex-bin", "latex")
}
Write-Log ("tlmgr install " + ($Packages -join " "))
Assert-Tlmgr @(@("install") + $Packages)

$warmup = Join-Path $env:TEMP ("mathimator-tinytex-warmup-" + [guid]::NewGuid().ToString("n"))
New-Item -ItemType Directory -Force -Path $warmup | Out-Null
@"
\documentclass[preview]{standalone}
\usepackage[english]{babel}
\usepackage{amsmath}
\usepackage{amssymb}
\begin{document}
\( y = \sin(x) \)
\end{document}
"@ | Set-Content -Path (Join-Path $warmup "formula.tex") -Encoding ascii
Push-Location $warmup
try {
    Invoke-Checked $latexExe @("-interaction=nonstopmode", "-halt-on-error", "formula.tex")
    $dvisvgm = Join-Path $TinytexBin "dvisvgm.exe"
    Invoke-Checked $dvisvgm @("--page=1", "--no-fonts", "--verbosity=0", "--output=formula.svg", "formula.dvi")
    if (-not (Test-Path (Join-Path $warmup "formula.svg"))) {
        throw "warmup did not write formula.svg"
    }
} finally {
    Pop-Location
    Remove-Item -Recurse -Force $warmup
}

Assert-Tlmgr @("option", "repository", $DisabledRepository)
Write-Log "confirming tlmgr install qrcode fails against the disabled repository"
$qrcode = Invoke-Tlmgr @("install", "qrcode")
if ($qrcode -eq 0) {
    throw "TinyTeX still installed a package after the repository was disabled"
}
Write-Log "tlmgr install qrcode exited $qrcode"
$null = Invoke-Tlmgr @("--version")
$tlmgrLine = @(
    $script:LastOutput -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)[0]
if ([string]::IsNullOrWhiteSpace($tlmgrLine)) {
    throw "tlmgr --version produced no output. kpsewhich must stay on PATH inside Invoke-Tlmgr."
}
$tlmgrLine = $tlmgrLine.Trim()

# tlmgr leaves its temp files, package backups, logs and a 20 MB cache of the
# remote package database behind. Several name the build path, and none is
# used offline.
foreach ($leftover in @("temp\*", "tlpkg\backups\*", "tlpkg\texlive.tlpdb.main.*")) {
    $path = Join-Path $tinytexRoot $leftover
    if (Test-Path $path) { Remove-Item -Recurse -Force $path }
}
Get-ChildItem -Path (Join-Path $tinytexRoot "texmf-var\web2c") -Filter *.log -Recurse -File | Remove-Item -Force

# Typst is Apache-2.0, so its LICENSE and NOTICE ship next to the binary. The
# directory is rebuilt from the checked archive on every run.
$typstZip = Join-Path $Cache $TypstArchive
Download-File $TypstUrl $typstZip $Pins.TYPST_WINDOWS_SHA256
$stage = Join-Path $Cache "typst-stage"
Expand-Fresh $typstZip $stage
$found = Get-ChildItem -Path $stage -Filter typst.exe -Recurse | Select-Object -First 1
if (-not $found) { throw "typst.exe was not in $typstZip" }
$typstDir = Join-Path $Dest "typst"
if (Test-Path $typstDir) { Remove-Item -Recurse -Force $typstDir }
New-Item -ItemType Directory -Force -Path $typstDir | Out-Null
foreach ($name in @("typst.exe", "LICENSE", "NOTICE")) {
    $file = Join-Path $found.DirectoryName $name
    if (-not (Test-Path $file)) { throw "$name was not in $typstZip" }
    Copy-Item $file $typstDir
}
$typstExe = Join-Path $typstDir "typst.exe"
$typstLine = (& $typstExe --version | Select-Object -First 1)
if ($typstLine -notmatch "^typst $([regex]::Escape($TypstVersion))(\s|$)") {
    throw "bundled typst is $typstLine"
}
Write-Log $typstLine

$workerDest = Join-Path $Dest "worker"
if (Test-Path $workerDest) { Remove-Item -Recurse -Force $workerDest }
New-Item -ItemType Directory -Force -Path (Join-Path $workerDest "mathimator_worker") | Out-Null
Copy-Item -Path (Join-Path $Root "worker\mathimator_worker\*") -Destination (Join-Path $workerDest "mathimator_worker") -Recurse -Force
$sampleDir = Join-Path $Dest "samples\reference-60"
New-Item -ItemType Directory -Force -Path $sampleDir | Out-Null
Copy-Item (Join-Path $Root "tests\fixtures\reference-60\project.json") (Join-Path $sampleDir "project.json") -Force

# The bundled interpreter must import Manim on its own: isolated mode reads no
# PYTHON* variable, user site or working directory, and nothing it loads may
# come from outside the bundle.
$probe = @'
import sys
from pathlib import Path
import manim
home = Path(sys.argv[1]).resolve()
for path in (sys.prefix, sys.base_prefix, manim.__file__):
    if not Path(path).resolve().is_relative_to(home):
        sys.exit(f'{path} is outside {home}')
print(sys.version.split()[0], manim.__version__)
'@
$probeLine = @(& $Python -I -B -c $probe $PythonHome)[-1]
if ($LASTEXITCODE -ne 0) {
    throw "the bundled Python cannot import manim on its own"
}
$pyFull, $manimFull = $probeLine -split " "
Write-Log "bundled python $pyFull manim $manimFull"

# The installer puts the runtime under Program Files (D62), where Python run by
# a standard user cannot save the bytecode it compiles, so every start of the
# supervisor and of Manim would compile the standard library, the render stack
# and the worker again. They are compiled here instead, in one process, which
# makes the bytecode the same on every build. Checked-hash bytecode holds a
# hash of its source, which Python checks on import, so a stale file never runs
# in place of its source. -s and -p record each source as runtime\... rather
# than by its build path, -f replaces the timestamped bytecode that the CPython
# build ships, and -W ignore keeps third-party SyntaxWarnings out of the log,
# as CPython's own install does.
Invoke-Checked $Python @("-I", "-W", "ignore", "-m", "compileall", "-q", "-f", "-s", $Dest, "-p", "runtime", "--invalidation-mode", "checked-hash", $PythonHome, $workerDest)

# Everything in packaging/bundle ships. --prune removes entries outside the
# bundle layout, such as a crash dump or the venv of an older build, and any
# bytecode not compiled above, such as another Python version's, copied from
# the checkout with the worker. The check then fails if a file, bytecode
# included, names the checkout or the builder's profile.
Invoke-Checked $Python @("-I", "-B", (Join-Path $Root "scripts\check_bundle.py"), "--prune", $Dest)

$base = [System.IO.Path]::GetFullPath($Dest)
if (-not $base.EndsWith("\")) { $base = $base + "\" }
$fullBin = [System.IO.Path]::GetFullPath($TinytexBin)
if (-not $fullBin.StartsWith($base)) {
    throw "TinyTeX bin is outside the bundle: $fullBin"
}
$relTex = $fullBin.Substring($base.Length).Replace("\", "/")
$manifestData = [ordered]@{
    python = "python/python.exe"
    worker = "worker"
    pythonVersion = $pyFull
    pythonSelection = $pythonSelection
    manim = $manimFull
    lockSha256 = (Get-FileHash -Algorithm SHA256 $LockFile).Hash.ToLowerInvariant()
    typst = "typst/typst.exe"
    typstVersion = $typstLine
    tinytexBin = $relTex
    tinytexVersion = "$tlmgrLine"
    tinytexRelease = $TinytexVersion
    tlnetRepository = $TlnetRepository
    sample = "samples/reference-60/project.json"
    platform = "windows-x86_64"
}
$json = ($manifestData | ConvertTo-Json)
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($Manifest, ($json.Trim() + "`n"), $utf8)
Write-Log "runtime: $Manifest"
Write-Log $json

$total = [int64]0
foreach ($entry in Get-ChildItem -LiteralPath $Dest -Force) {
    if ($entry.PSIsContainer) {
        # The [int64] cast turns the null Sum of an empty folder into 0.
        $bytes = [int64](Get-ChildItem -LiteralPath $entry.FullName -Recurse -File -Force | Measure-Object Length -Sum).Sum
    } else {
        $bytes = [int64]$entry.Length
    }
    $total += $bytes
    Write-Log ("size {0} {1:N0} bytes" -f $entry.Name, $bytes)
}
Write-Log ("size bundle {0:N0} bytes" -f $total)
Write-Log "bundle complete"
