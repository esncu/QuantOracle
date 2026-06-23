$Container = "QuantOracle"
$Volume    = "QuantOracleData"

$CleanupBlock = {
    Write-Host "`nStopping Postgres container..."
    docker stop $Container 2>$null | Out-Null
}

try {
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

    Write-Host "Waiting for Postgres to be ready..."
    $ready = $false
    for ($i = 1; $i -le 60; $i++) {
        docker exec $Container pg_isready -U postgres 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Start-Sleep -Seconds 3
            Write-Host "Postgres is ready."
            $ready = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        Write-Host "WARNING: Postgres did not become ready in 60s - starting anyway."
    }

    fastapi run main.py

} finally {
    & $CleanupBlock
}
