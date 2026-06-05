# ---------------------------------------------------------------------------
# QuantOracle launcher (Windows)
# Mirror of launch_linux.sh — same logic, PowerShell syntax.
#
# First run (or after reset.ps1): creates the container and DB from init.sql.
# Subsequent runs: starts the existing container.
# To fully reset: run reset.ps1 first.
# ---------------------------------------------------------------------------

$Container = "QuantOracle"
$Volume    = "QuantOracleData"

# Stop container on exit (Ctrl+C or script end)
$CleanupBlock = {
    Write-Host "`nStopping Postgres container..."
    docker stop $Container 2>$null | Out-Null
}

try {
    # Check if container already exists
    $exists = docker ps -a --format "{{.Names}}" 2>$null | Where-Object { $_ -eq $Container }

    if ($exists) {
        Write-Host "Starting existing $Container container..."
        docker start $Container | Out-Null
    } else {
        Write-Host "Creating $Container container (first run)..."
        docker run --name $Container `
            -e POSTGRES_PASSWORD=qu0cle `
            -e POSTGRES_DB=stocks `
            -v "${Volume}:/var/lib/postgresql" `
            -v "${PWD}/init.sql:/docker-entrypoint-initdb.d/init.sql" `
            -p 5432:5432 `
            -d postgres | Out-Null
    }

    # Wait for Postgres to be ready (up to 30s)
    Write-Host "Waiting for Postgres to be ready..."
    $ready = $false
    for ($i = 1; $i -le 30; $i++) {
        $result = docker exec $Container pg_isready -U postgres -d stocks 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Postgres is ready."
            $ready = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        Write-Host "WARNING: Postgres did not become ready in 30s — starting anyway."
    }

    fastapi run main.py

} finally {
    & $CleanupBlock
}
