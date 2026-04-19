#!/usr/bin/env bash
set -e

cleanup() {
  echo "Stopping container..."
  sudo docker stop QuantOracle >/dev/null 2>&1
}
trap cleanup EXIT

sudo docker start QuantOracle >/dev/null 2>&1 ||
  sudo docker run --name QuantOracle \
    -e POSTGRES_PASSWORD=qu0cle \
    -v QuantOracleData:/var/lib/postgresql \
    -v "$(pwd)/init.sql:/docker-entrypoint-initdb.d/init.sql:z" \
    -p 5432:5432 \
    -d postgres

echo "Waiting for Postgres..."
sleep 5

fastapi run main.py
