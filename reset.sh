#!/usr/bin/env bash
set -e

echo "Stopping container (if running)..."
sudo docker stop QuantOracle 2>/dev/null || true

echo "Removing container..."
sudo docker rm QuantOracle 2>/dev/null || true

echo "Removing data volume..."
sudo docker volume rm QuantOracleData 2>/dev/null || true

echo "Done. Run launch_linux.sh to start fresh."
