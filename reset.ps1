$Container = "QuantOracle"
$Volume    = "QuantOracleData"

Write-Host "Stopping container (if running)..."
docker stop $Container 2>$null | Out-Null

Write-Host "Removing container..."
docker rm $Container 2>$null | Out-Null

Write-Host "Removing data volume..."
docker volume rm $Volume 2>$null | Out-Null

Write-Host "Done. Run launch_windows.ps1 to start fresh."
