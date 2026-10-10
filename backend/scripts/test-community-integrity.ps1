<#
.SYNOPSIS
Run the complete backend suite against a disposable PostgreSQL 16 database.
.EXAMPLE
powershell -ExecutionPolicy Bypass -File backend/scripts/test-community-integrity.ps1 -Offline
#>
param([switch]$Offline)
$ErrorActionPreference = 'Stop'
$backendRoot = Split-Path -Parent $PSScriptRoot
$testContainer = $null
$testEnvNames = @('HQA_TEST_DATABASE_URL', 'HQA_TEST_DATABASE_USERNAME', 'HQA_TEST_DATABASE_PASSWORD')
$previousEnv = @{}
foreach ($name in $testEnvNames) { $previousEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $testName = 'hqa-board-integrity-' + [Guid]::NewGuid().ToString('N').Substring(0, 12)
    $testContainer = docker run --detach --name $testName --label hqa.task=board-integrity --publish 127.0.0.1::5432 --env POSTGRES_DB=hqa_board_integrity --env POSTGRES_USER=board_test --env POSTGRES_PASSWORD=board_test_local postgres:16-alpine
    if ($LASTEXITCODE -ne 0) { throw 'Could not start the disposable PostgreSQL container' }
    $testContainer = $testContainer.Trim()
    $testPort = (docker port $testContainer 5432/tcp).Trim().Split(':')[-1]
    if ($LASTEXITCODE -ne 0 -or $testPort -notmatch '^\d+$') { throw 'Could not determine the PostgreSQL port' }
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        docker exec $testContainer pg_isready -U board_test -d hqa_board_integrity *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 250
    }
    if (-not $ready) { throw 'PostgreSQL startup timed out' }
    $env:HQA_TEST_DATABASE_URL = "jdbc:postgresql://127.0.0.1:$testPort/hqa_board_integrity"
    $env:HQA_TEST_DATABASE_USERNAME = 'board_test'
    $env:HQA_TEST_DATABASE_PASSWORD = 'board_test_local'
    $mavenArgs = @('-f', (Join-Path $backendRoot 'pom.xml'), 'test')
    if ($Offline) { $mavenArgs += '-o' }
    # Windows PowerShell treats native stderr (including harmless JVM warnings)
    # as terminating errors under Stop. Maven's exit code determines success.
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & mvn @mavenArgs
        $testExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($testExitCode -ne 0) { throw 'Backend integrity tests failed' }
} finally {
    foreach ($name in $testEnvNames) { [Environment]::SetEnvironmentVariable($name, $previousEnv[$name], 'Process') }
    if ($testContainer -match '^[a-f0-9]{64}$') {
        $metadata = docker inspect $testContainer | ConvertFrom-Json
        if ($LASTEXITCODE -eq 0 -and $metadata[0].Config.Labels.'hqa.task' -eq 'board-integrity') {
            docker rm --force $testContainer | Out-Null
        }
    }
}
