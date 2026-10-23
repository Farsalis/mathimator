# Build the phase 5 offline runtime under packaging/bundle/ on Windows.
#
# The product installer is the Windows NSIS bundle described in
# desktop/src-tauri/tauri.windows.conf.json. This script prepares the runtime
# that installer embeds: a uv virtualenv, preseeded TinyTeX, Typst 0.15.0,
# the worker package, and the reference-60 sample. It does not build the
# NSIS installer and it does not sign anything.
#
# Python selection matches packaging/build-bundle.sh: CPython 3.14.9 when uv
# publishes it, otherwise the newest CPython 3.14 uv publishes for this
# platform, then CPython 3.13.17 if manim[typst]==0.22.0 has no wheel.
# The Manim pin does not change.
#
# Windows assets:
#   TinyTeX-1-windows-v2026.10.exe
#   typst-x86_64-pc-windows-msvc.zip
#   uv venv python at venv/Scripts/python.exe

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

function Get-Uv {
    # A uv already on PATH can be too old to list current python-build-standalone
    # releases. uv 0.10.9 reported that 3.14.9 did not exist and selected 3.14.3.
    # uv 0.13.5 lists cpython-3.14.9-windows-x86_64-none. Always use a copy
    # downloaded from the latest GitHub release into the cache.
    $cached = Join-Path $Cache "uv\uv.exe"
    if (Test-Path $cached) {
        return $cached
    }
    $zip = Join-Path $Cache "uv-x86_64-pc-windows-msvc.zip"
    $url = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
    Write-Log "downloading the current uv release"
    if (Test-Path $zip) { Remove-Item -Force $zip }
    Download-File $url $zip
    $stage = Join-Path $Cache "uv-stage"
    if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
    Expand-Archive -Path $zip -DestinationPath $stage -Force
    New-Item -ItemType Directory -Force -Path (Split-Path $cached) | Out-Null
    $found = Get-ChildItem -Path $stage -Filter uv.exe -Recurse | Select-Object -First 1
    if (-not $found) {
        throw "uv.exe was not in $zip"
    }
    Copy-Item $found.FullName $cached -Force
    return $cached
}

function Download-File([string]$Url, [string]$Destination) {
    if (Test-Path $Destination) {
        return
    }
    $partial = "$Destination.partial"
    if (Test-Path $partial) { Remove-Item -Force $partial }
    & curl.exe -fL --retry 5 --retry-delay 2 -o $partial $Url
    if ($LASTEXITCODE -ne 0) {
        throw "download failed: $Url"
    }
    Move-Item -Force $partial $Destination
    Unblock-File -Path $Destination
}

function Invoke-Checked([string]$File, [string[]]$CommandArgs) {
    Write-Log ("run {0} {1}" -f $File, ($CommandArgs -join " "))
    & $File @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "exit $LASTEXITCODE from $File"
    }
}

$TinytexVersion = "2026.10"
$TinytexArchive = "TinyTeX-1-windows-v$TinytexVersion.exe"
$TinytexUrl = "https://github.com/rstudio/tinytex-releases/releases/download/v$TinytexVersion/$TinytexArchive"
$TypstVersion = "0.15.0"
$TypstArchive = "typst-x86_64-pc-windows-msvc.zip"
$TypstUrl = "https://github.com/typst/typst/releases/download/v$TypstVersion/$TypstArchive"
$ManimSpec = "manim[typst]==0.22.0"
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
$Uv = Get-Uv
Write-Log "uv $Uv"
& $Uv --version

function Select-Python([string]$UvExe) {
    $requested = "3.14.9"
    & $UvExe python install $requested
    if ($LASTEXITCODE -eq 0) {
        return @{ Version = $requested; Reason = "uv published $requested" }
    }
    $list = & $UvExe python list --all-versions
    $versions = @()
    foreach ($line in $list) {
        if ($line -match "freethreaded") { continue }
        if ($line -match "cpython-(3\.14\.\d+)-windows-x86_64") {
            $versions += $Matches[1]
        }
    }
    if ($versions.Count -eq 0) {
        throw "uv publishes no CPython 3.14 for windows-x86_64"
    }
    $newest = $versions | Sort-Object { [version]$_ } | Select-Object -Last 1
    & $UvExe python install $newest
    if ($LASTEXITCODE -ne 0) {
        throw "uv could not install CPython $newest"
    }
    return @{
        Version = $newest
        Reason = "uv has no CPython $requested build for windows-x86_64; using $newest"
    }
}

function Install-Manim([string]$UvExe, [string]$Version) {
    $venv = Join-Path $Dest "venv"
    if (Test-Path $venv) { Remove-Item -Recurse -Force $venv }
    Invoke-Checked $UvExe @("venv", "--python", $Version, $venv)
    $python = Join-Path $venv "Scripts\python.exe"
    if (-not (Test-Path $python)) {
        throw "venv python missing at $python"
    }
    Invoke-Checked $UvExe @("pip", "install", "--python", $python, $ManimSpec)
    $installed = & $python -c "import manim, sys; assert manim.__version__ == '0.22.0', manim.__version__; print(sys.version.split()[0])"
    if ($LASTEXITCODE -ne 0) {
        throw "manim import failed on $Version"
    }
    return @{ Python = $python; VersionLine = ($installed | Select-Object -Last 1) }
}

$Python = Join-Path $Dest "venv\Scripts\python.exe"
$pyVersion = $null
$pyReason = $null
if (Test-Path $Python) {
    $probe = & $Python -c "import manim, sys; print(manim.__version__); print(sys.version.split()[0])"
    if ($LASTEXITCODE -eq 0 -and @($probe)[0] -eq "0.22.0") {
        $pyVersion = @($probe)[1]
        if ($pyVersion -eq "3.14.9") {
            $pyReason = "uv published 3.14.9"
        } else {
            $pyReason = "reused existing venv; interpreter $pyVersion"
        }
        Write-Log "reusing venv $pyVersion manim 0.22.0"
    }
}
if (-not $pyVersion) {
    $choice = Select-Python $Uv
    $pyVersion = $choice.Version
    $pyReason = $choice.Reason
    Write-Log "Python selection: $pyReason"
    try {
        $installed = Install-Manim $Uv $pyVersion
    } catch {
        if ($pyVersion -like "3.14.*") {
            Write-Log "manim[typst]==0.22.0 did not install on $pyVersion; trying 3.13.17"
            & $Uv python install 3.13.17
            if ($LASTEXITCODE -ne 0) {
                throw "uv has no CPython 3.13.17 build"
            }
            $pyVersion = "3.13.17"
            $pyReason = "$pyReason; manim wheel missing, switched to 3.13.17"
            $installed = Install-Manim $Uv $pyVersion
        } else {
            throw
        }
    }
    $Python = $installed.Python
}
Write-Log "bundled python $pyVersion manim 0.22.0"

function Find-Latex([string]$RootDir) {
    if (-not (Test-Path $RootDir)) { return $null }
    return Get-ChildItem -Path $RootDir -Filter latex.exe -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Expand-TinyTeXRoot([string]$Destination) {
    # TinyTeX-1-windows-v2026.10.exe is a 7-Zip SFX, not an Inno Setup installer.
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

$tinytexRoot = Join-Path $Dest "tinytex"
Expand-TinyTeXRoot $tinytexRoot
$latex = Find-Latex $tinytexRoot
if (-not $latex) {
    $installer = Join-Path $Cache $TinytexArchive
    Download-File $TinytexUrl $installer
    if (Test-Path $tinytexRoot) { Remove-Item -Recurse -Force $tinytexRoot }
    New-Item -ItemType Directory -Force -Path $tinytexRoot | Out-Null
    Write-Log "extracting TinyTeX 7-Zip SFX into $tinytexRoot"
    & $installer -y "-o$tinytexRoot"
    if ($LASTEXITCODE -ne 0) {
        throw "TinyTeX installer exited $LASTEXITCODE"
    }
    Expand-TinyTeXRoot $tinytexRoot
    $latex = Find-Latex $tinytexRoot
}
if (-not $latex) {
    throw "latex.exe was not found under $tinytexRoot"
}
$TinytexBin = $latex.DirectoryName
Write-Log "TinyTeX bin $TinytexBin"

# tlmgr.bat returns to cmd with perl's errorlevel, but PowerShell reports
# that .bat exit code as 0. Call tlperl directly so a failed install is visible.
$TlmgrPerl = Join-Path $tinytexRoot "tlpkg\tlperl\bin\perl.exe"
$TlmgrPl = Join-Path $tinytexRoot "texmf-dist\scripts\texlive\tlmgr.pl"
if (-not (Test-Path $TlmgrPerl) -or -not (Test-Path $TlmgrPl)) {
    throw "tlmgr.pl was not found under $tinytexRoot"
}

function Invoke-Tlmgr([string[]]$CommandArgs) {
    $previousPerl = $env:PERL5LIB
    $previousPath = $env:PATH
    $env:PERL5LIB = Join-Path $tinytexRoot "tlpkg\tlperl\lib"
    $env:PATH = "$(Join-Path $tinytexRoot 'tlpkg\tlperl\bin');$TinytexBin;$previousPath"
    try {
        Write-Log ("run perl tlmgr.pl " + ($CommandArgs -join " "))
        # Stop turns native stderr, once merged with 2>&1, into a terminating error.
        $previousAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $output = & $TlmgrPerl $TlmgrPl @CommandArgs 2>&1
            $code = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousAction
        }
        $text = @($output | ForEach-Object { "$_" })
        $script:LastTlmgrOutput = ($text -join "`n")
        $text | ForEach-Object { Write-Host $_ }
        return $code
    } finally {
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

$null = Invoke-Tlmgr @("option", "repository")
$repoText = $script:LastTlmgrOutput
if ($repoText -match "offline-repo") {
    Write-Log "resetting tlmgr repository before package install"
    Assert-Tlmgr @("option", "repository", "https://mirror.ctan.org/systems/texlive/tlnet")
}
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

$offlineRepo = Join-Path $tinytexRoot "offline-repo"
New-Item -ItemType Directory -Force -Path $offlineRepo | Out-Null
Assert-Tlmgr @("option", "repository", $offlineRepo)
Write-Log "confirming tlmgr install qrcode fails against the empty repository"
$qrcode = Invoke-Tlmgr @("install", "qrcode")
if ($qrcode -eq 0) {
    throw "TinyTeX still installed a package after the repository was disabled"
}
Write-Log "tlmgr install qrcode exited $qrcode"

$typstDir = Join-Path $Dest "typst"
$typstExe = Join-Path $typstDir "typst.exe"
if (-not (Test-Path $typstExe)) {
    $zip = Join-Path $Cache $TypstArchive
    Download-File $TypstUrl $zip
    $stage = Join-Path $Cache "typst-stage"
    if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
    Expand-Archive -Path $zip -DestinationPath $stage -Force
    $found = Get-ChildItem -Path $stage -Filter typst.exe -Recurse | Select-Object -First 1
    if (-not $found) { throw "typst.exe was not in $zip" }
    New-Item -ItemType Directory -Force -Path $typstDir | Out-Null
    Copy-Item $found.FullName $typstExe -Force
}
$typstLine = (& $typstExe --version | Select-Object -First 1)
if ($typstLine -notlike "typst 0.15.0*") {
    throw "bundled typst is $typstLine"
}
Write-Log $typstLine

$workerDest = Join-Path $Dest "worker"
if (Test-Path $workerDest) { Remove-Item -Recurse -Force $workerDest }
New-Item -ItemType Directory -Force -Path (Join-Path $workerDest "mathimator_worker") | Out-Null
Copy-Item -Path (Join-Path $Root "worker\mathimator_worker\*") -Destination (Join-Path $workerDest "mathimator_worker") -Recurse -Force
Get-ChildItem -Path $workerDest -Directory -Filter __pycache__ -Recurse | Remove-Item -Recurse -Force
$sampleDir = Join-Path $Dest "samples\reference-60"
New-Item -ItemType Directory -Force -Path $sampleDir | Out-Null
Copy-Item (Join-Path $Root "tests\fixtures\reference-60\project.json") (Join-Path $sampleDir "project.json") -Force

$pyFull = (& $Python -c "import sys; print(sys.version.split()[0])").Trim()
$manimFull = (& $Python -c "import manim; print(manim.__version__)").Trim()
$null = Invoke-Tlmgr @("--version")
$tlmgrLine = @(
    $script:LastTlmgrOutput -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)[0]
if ([string]::IsNullOrWhiteSpace($tlmgrLine)) {
    throw "tlmgr --version produced no output. kpsewhich must stay on PATH inside Invoke-Tlmgr."
}
$tlmgrLine = $tlmgrLine.Trim()
$relPy = "venv/Scripts/python.exe"
$base = [System.IO.Path]::GetFullPath($Dest)
if (-not $base.EndsWith("\")) { $base = $base + "\" }
$fullBin = [System.IO.Path]::GetFullPath($TinytexBin)
if (-not $fullBin.StartsWith($base)) {
    throw "TinyTeX bin is outside the bundle: $fullBin"
}
$relTex = $fullBin.Substring($base.Length).Replace("\", "/")
$manifest = [ordered]@{
    python = $relPy
    pythonVersion = $pyFull
    pythonSelection = $pyReason
    manim = $manimFull
    typst = "typst/typst.exe"
    typstVersion = $typstLine
    tinytexBin = $relTex
    tinytexVersion = "$tlmgrLine"
    tinytexRelease = $TinytexVersion
    sample = "samples/reference-60/project.json"
    platform = "windows-x86_64"
}
$json = ($manifest | ConvertTo-Json)
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText((Join-Path $Dest "runtime.json"), ($json.Trim() + "`n"), $utf8)
Write-Log "runtime: $Dest\runtime.json"
Write-Log $json
Get-ChildItem $Dest | ForEach-Object { Write-Log ("size {0} {1}" -f $_.Name, $_.Length) }
Write-Log "bundle complete"
