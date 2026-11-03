# Prerequisites: PostgreSQL tools on PATH, and this project's installed .venv.
# Creates an isolated localhost-only cluster. Never uses a Railway database.
param([string[]]$TestLabels = @())

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$testData = Join-Path $projectRoot ('.local\postgres-' + [guid]::NewGuid().ToString('N'))
$testPort = 55434
$previousTestUrl = $env:TEST_DATABASE_URL
$clusterStarted = $false
$testsExitCode = 1

Push-Location $projectRoot
try {
    if (Get-NetTCPConnection -LocalPort $testPort -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $testPort is already in use; no server was changed."
    }
    New-Item -ItemType Directory -Path $testData -Force | Out-Null
    & initdb -D $testData -U wordbyword_test -A trust --encoding=UTF8 --no-locale
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the isolated test database.' }
    & pg_ctl -D $testData -l (Join-Path $testData 'server.log') -o "-h 127.0.0.1 -p $testPort" -w -t 20 start
    if ($LASTEXITCODE -ne 0) { throw 'Could not start the isolated test database.' }
    $clusterStarted = $true
    $env:TEST_DATABASE_URL = "postgresql://wordbyword_test@127.0.0.1:$testPort/wordbyword_test"
    & .\.venv\Scripts\python.exe manage.py test @TestLabels --settings=config.postgres_test_settings --noinput
    $testsExitCode = $LASTEXITCODE
}
finally {
    if ($clusterStarted) { & pg_ctl -D $testData -m fast -w -t 20 stop }
    $env:TEST_DATABASE_URL = $previousTestUrl
    Pop-Location
}
exit $testsExitCode
