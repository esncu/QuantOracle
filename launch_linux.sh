#!/usr/bin/env bash
set -e

cleanup() {
  echo "Stopping Postgres container..."
  sudo docker stop QuantOracle >/dev/null 2>&1 || true
}
trap cleanup EXIT

if sudo docker ps -a --format '{{.Names}}' | grep -q '^QuantOracle$'; then
  echo "Starting existing QuantOracle container..."
  sudo docker start QuantOracle >/dev/null 2>&1
else
  echo "Creating QuantOracle container (first run)..."

  chmod a+r "$(pwd)/init.sql"

  sudo docker run --name QuantOracle \
    -e POSTGRES_PASSWORD=qu0cle \
    -e POSTGRES_DB=stocks \
    -v QuantOracleData:/var/lib/postgresql \
    -v "$(pwd)/init.sql:/docker-entrypoint-initdb.d/init.sql:z" \
    -p 5432:5432 \
    -d postgres
fi

echo "Waiting for Postgres to be ready..."
for i in $(seq 1 60); do
  if sudo docker exec QuantOracle pg_isready -U postgres >/dev/null 2>&1; then
    sleep 3
    echo "Postgres is ready."
    break
  fi
  sleep 1
done

fastapi run main.py
