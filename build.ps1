<#
.SYNOPSIS
    Builds the Department of Sandwich Legitimacy end to end.

.DESCRIPTION
    Five stages, in order:

      1. venv      create .venv and install PyInstaller
      2. icon      generate assets/dosl.ico from tools/make_icon.py
      3. forge     assemble and link build/bureau_entropy.dll
      4. test      run the whole self-test suite
      5. exe       freeze dist/DoSL-Counter.exe and dist/dosl.exe

    Any stage can be skipped, and -Stage runs exactly one.

.PARAMETER Stage
    Run a single stage: venv, icon, forge, test, or exe.

.PARAMETER SkipTests
    Freeze without running the self-tests first. Not advisable.

.PARAMETER Clean
    Delete .venv, build, dist and .pyibuild before starting.

.EXAMPLE
    .\build.ps1
.EXAMPLE
    .\build.ps1 -Stage forge
.EXAMPLE
    .\build.ps1 -Clean
#>
[CmdletBinding()]
param(
    [ValidateSet('venv', 'icon', 'forge', 'test', 'exe')]
    [string]$Stage,
    [switch]$SkipTests,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

$venvPython = Join-Path $root '.venv\Scripts\python.exe'

function Invoke-Native {
    <#
        Runs an external program and fails on its exit code, not its stderr.

        Windows PowerShell 5.1 turns ANY stderr output from a native command
        into a terminating error while $ErrorActionPreference is 'Stop'.
        PyInstaller, pip and venv all log ordinary progress to stderr, so a
        perfectly successful build would abort. Exit codes are the only
        signal worth trusting here.
    #>
    param(
        [Parameter(Mandatory)][string]$Exe,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory)][string]$What
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

function Write-Stage([string]$name, [string]$detail) {
    Write-Host ''
    Write-Host ('=' * 74) -ForegroundColor DarkGray
    Write-Host ("  {0,-10} {1}" -f $name.ToUpper(), $detail) -ForegroundColor Cyan
    Write-Host ('=' * 74) -ForegroundColor DarkGray
}

function Get-BasePython {
    # The py launcher, then PATH, then the usual per-user install location.
    $candidates = @()
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) { $candidates += @{ Exe = $launcher.Source; Args = @('-3') } }
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath -and $onPath.Source -notlike '*WindowsApps*') {
        $candidates += @{ Exe = $onPath.Source; Args = @() }
    }
    foreach ($v in '313', '312', '311') {
        $guess = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$v\python.exe"
        if (Test-Path $guess) { $candidates += @{ Exe = $guess; Args = @() } }
    }
    foreach ($c in $candidates) {
        try {
            $version = & $c.Exe @($c.Args + '--version') 2>$null
            if ($LASTEXITCODE -eq 0) { return $c }
        } catch { }
    }
    throw 'No usable Python 3.11+ found. Install it: winget install Python.Python.3.12'
}

function Invoke-Venv {
    Write-Stage 'venv' 'creating .venv and installing PyInstaller'
    if (-not (Test-Path $venvPython)) {
        $base = Get-BasePython
        Write-Host "  base interpreter: $($base.Exe)"
        Invoke-Native $base.Exe ($base.Args + @('-m', 'venv', '.venv')) 'venv creation'
    }
    Invoke-Native $venvPython @('-m', 'pip', 'install', '--upgrade', 'pip', '--quiet') 'pip upgrade'
    Invoke-Native $venvPython @('-m', 'pip', 'install', 'pyinstaller', '--quiet') 'pyinstaller install'
    $pyi = & $venvPython -m PyInstaller --version
    Write-Host "  PyInstaller $pyi" -ForegroundColor Green
}

function Invoke-Icon {
    Write-Stage 'icon' 'writing assets/dosl.ico'
    Invoke-Native $venvPython @('tools\make_icon.py') 'icon generation'
}

function Invoke-Forge {
    Write-Stage 'forge' 'assembling and linking build/bureau_entropy.dll'
    Invoke-Native $venvPython @('-m', 'dosl', 'forge') 'the forge'
}

function Invoke-Test {
    Write-Stage 'test' 'running the self-test suite'
    Invoke-Native $venvPython @('tests\run_all.py') 'self-tests'
}

function Invoke-Exe {
    Write-Stage 'exe' 'freezing dist/DoSL-Counter.exe and dist/dosl.exe'
    # --workpath keeps PyInstaller's scratch out of build/, which belongs to
    # the DLL the forge writes there.
    Invoke-Native $venvPython @(
        '-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level', 'WARN',
        '--workpath', '.pyibuild', 'DoSL.spec') 'PyInstaller'

    Write-Host ''
    Get-ChildItem dist -Filter *.exe | ForEach-Object {
        Write-Host ("  {0,-22} {1,8:N2} MB" -f $_.Name, ($_.Length / 1MB)) -ForegroundColor Green
    }
}

# ---------------------------------------------------------------- main

if ($Clean) {
    Write-Stage 'clean' 'removing .venv, build, dist, .pyibuild'
    foreach ($d in '.venv', 'build', 'dist', '.pyibuild') {
        if (Test-Path $d) { Remove-Item $d -Recurse -Force; Write-Host "  removed $d" }
    }
}

$stages = if ($Stage) { @($Stage) } else { @('venv', 'icon', 'forge', 'test', 'exe') }
if ($SkipTests) { $stages = $stages | Where-Object { $_ -ne 'test' } }

# Every stage past the first needs the venv to exist.
if ($stages[0] -ne 'venv' -and -not (Test-Path $venvPython)) { Invoke-Venv }

$started = Get-Date
foreach ($s in $stages) {
    switch ($s) {
        'venv'  { Invoke-Venv }
        'icon'  { Invoke-Icon }
        'forge' { Invoke-Forge }
        'test'  { Invoke-Test }
        'exe'   { Invoke-Exe }
    }
}

$elapsed = (Get-Date) - $started
Write-Host ''
Write-Host ("Done in {0:N1}s. The Department is open." -f $elapsed.TotalSeconds) -ForegroundColor Cyan
if ($stages -contains 'exe') {
    Write-Host '  dist\DoSL-Counter.exe    double-click to open the counter'
    Write-Host '  dist\dosl.exe            dosl submit --help'
}
