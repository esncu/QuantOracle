#!/usr/bin/env bash
set -e

cleanup() {
  echo "Stopping container..."
  docker stop QuantOracle >/dev/null 2>&1
}
trap cleanup EXIT

docker start QuantOracle >/dev/null 2>&1 ||
  docker run --name QuantOracle \
    -e POSTGRES_PASSWORD=qu0cle \
    -v QuantOracleData:/var/lib/postgresql/data \
    -v "$(pwd)/init.sql:/docker-entrypoint-initdb.d/init.sql" \
    -p 5432:5432 \
    -d postgres

echo "Waiting for Postgres..."
sleep 5

fastapi run main.py
