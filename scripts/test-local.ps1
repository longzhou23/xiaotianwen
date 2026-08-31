[CmdletBinding()]
param(
    [ValidateSet('quick', 'refactor', 'full-offline', 'integration', 'ui')]
    [string]$Profile = 'quick',
    [string]$Case,
    [string]$Tag,
    [switch]$BaselineApproved,
    [string]$RunId,
    [switch]$NoOpen
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonCommand = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } elseif (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }

$arguments = @('-m', 'tests.harness.cli')
if ($Profile -eq 'ui') {
    $arguments += 'ui'
    if ($NoOpen) { $arguments += '--no-open' }
} else {
    $arguments += @('run', '--profile', $Profile)
    if ($Case) { $arguments += @('--case', $Case) }
    if ($Tag) { $arguments += @('--tag', $Tag) }
    if ($BaselineApproved) { $arguments += @('--baseline', 'approved') }
    if ($RunId) { $arguments += @('--run-id', $RunId) }
}

Push-Location -LiteralPath $repositoryRoot
try {
    & $pythonCommand @arguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
