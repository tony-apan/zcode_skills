$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ManageScript = Join-Path $ScriptDir 'manage.py'
$ForwardedArgs = @($args)
if ($ForwardedArgs.Count -eq 1 -and $ForwardedArgs[0] -eq '-Help') {
    $ForwardedArgs = @('--help')
}

$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $PyLauncher) {
    & $PyLauncher.Source -3 $ManageScript install @ForwardedArgs
    exit $LASTEXITCODE
}

$Python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $Python) {
    Write-Error 'Python 3.9 or newer is required (py -3 or python).'
    exit 1
}

& $Python.Source $ManageScript install @ForwardedArgs
exit $LASTEXITCODE
