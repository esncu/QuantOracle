$container = "QuantOracle"

docker start $container 2>$null
if($LASTEXITCODE -ne 0){
  docker run --name $container `
    -e POSTGRES_PASSWORD=qu0cle `
    -v QuantOracleData:/var/lib/postgresql `
    -v "${PWD}/init.sql:/docker-entrypoint-initdb.d/init.sql" `
    -p 5432:5432 `
    -d postgres | Out-Null
}

try {
    Start-Sleep -Seconds 5
    fastapi run main.py
}
finally {
    docker stop $container | Out-Null
}
