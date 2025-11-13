#!/bin/bash
set -e

echo "Starting TRIDENT Subsea Controller for BlueOS"
echo "=============================================="

# Start pigpio daemon in background (for hardware mode)
if command -v pigpiod &> /dev/null; then
    echo "Starting pigpio daemon..."
    pigpiod -s 1 || echo "pigpio daemon may already be running"
    sleep 2
else
    echo "pigpiod not found - running in simulation mode"
fi

# Start the Python application
echo "Starting subsea controller..."
exec python3 /app/app/main.py
