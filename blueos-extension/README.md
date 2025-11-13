# TRIDENT BlueOS Extension

Lightweight subsea servo controller for underwater robotic arm, optimized for BlueOS.

## Overview

This BlueOS extension provides minimal servo control for the TRIDENT 5-DOF underwater robotic arm. It runs as a containerized service on the Raspberry Pi companion computer with BlueOS, handling only the critical servo control functions to minimize computational load on the subsea system.

## Features

- **Ultra-lightweight**: Minimal CPU and memory footprint
- **Servo Control**: PWM control for 4 servo joints via pigpio
- **WebSocket API**: Real-time command interface for Mac relay
- **REST API**: HTTP endpoints for status and health checks
- **Safety Features**: Emergency stop, rate limiting, angle clamping
- **Graceful Degradation**: Simulation mode when hardware unavailable
- **BlueOS Compatible**: Follows BlueOS extension standards

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    MacBook (Relay + UI)                       │
│  ┌────────────┐   ┌──────────────┐   ┌──────────────────┐   │
│  │  Sensors   │───│ Relay Server │───│ Web Dashboard    │   │
│  │ (Topside)  │   │  (Python)    │   │ (Browser + 3D)   │   │
│  └────────────┘   └──────┬───────┘   └──────────────────┘   │
└─────────────────────────┼────────────────────────────────────┘
                          │ WebSocket
                          │ (ws://blueos.local:9091/ws)
┌─────────────────────────┼────────────────────────────────────┐
│               Raspberry Pi (BlueOS)                           │
│  ┌────────────────────────────────────────────────────────┐  │
│  │         TRIDENT BlueOS Extension Container             │  │
│  │  ┌──────────────┐          ┌───────────────────────┐  │  │
│  │  │   Subsea     │─────────▶│   Servo Controller    │  │  │
│  │  │   Service    │          │   (4x HSR-M9382TH)    │  │  │
│  │  │  (Port 9091) │          │   GPIO 12-15          │  │  │
│  │  └──────────────┘          └───────────────────────┘  │  │
│  └────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────┘
```

## Installation

### Option 1: Install from BlueOS Extension Manager (Recommended)

1. Navigate to BlueOS web interface (typically `http://blueos.local`)
2. Open **Extensions Manager**
3. Search for "TRIDENT"
4. Click **Install**

### Option 2: Manual Docker Installation

**Note**: When installing via Extension Manager, BlueOS assigns ports dynamically. Check the Extensions page for the actual port.

```bash
# SSH into BlueOS Raspberry Pi
ssh pi@blueos.local

# Pull and run the container
docker pull <dockerhub-username>/trident-subsea:latest
docker run -d \
  --name trident-subsea \
  --privileged \
  -p 9091:9091 \
  -v trident-config:/root/.config \
  --restart unless-stopped \
  <dockerhub-username>/trident-subsea:latest
```

### Option 3: Build from Source

```bash
# Clone repository
git clone https://github.com/ccavens/TRIDENT.git
cd TRIDENT/blueos-extension

# Build Docker image
docker build -t trident-subsea:latest .

# Run with docker-compose
docker-compose up -d
```

## Configuration

### Port Configuration

**Default Port**: 9091 (for manual deployment)

**BlueOS Extension Manager**: When installed via Extension Manager, BlueOS assigns a dynamic port. To find the actual port:
1. Open BlueOS web interface (`http://blueos.local`)
2. Navigate to **Extensions**
3. Find TRIDENT extension
4. Note the assigned port (e.g., `http://blueos.local:PORT`)
5. Update your relay `config.json` with: `"subsea_url": "ws://blueos.local:PORT/ws"`

**Manual Deployment**: Port 9091 is fixed when using docker-compose or manual docker run.

### Hardware Setup

Connect servos to GPIO pins:
- **Joint 1 (Yaw)**: GPIO 12
- **Joint 2 (Pitch)**: GPIO 13
- **Joint 3 (Pitch)**: GPIO 14
- **Joint 4 (Roll)**: GPIO 15

Servos should be HSR-M9382TH or compatible (500-2500μs PWM range).

### Network Configuration

Update your Mac relay configuration to point to the BlueOS container.

**For Manual Deployment (docker-compose)**:
```json
{
  "network": {
    "subsea_url": "ws://blueos.local:9091/ws"
  }
}
```

**For BlueOS Extension Manager Installation**:
First, find the assigned port in the BlueOS Extensions page, then:
```json
{
  "network": {
    "subsea_url": "ws://blueos.local:ACTUAL_PORT/ws"
  }
}
```

**Using IP Address**:
```json
{
  "network": {
    "subsea_url": "ws://192.168.2.2:ACTUAL_PORT/ws"
  }
}
```

## API Reference

### WebSocket Endpoint: `/ws`

Connect to `ws://<blueos-ip>:9091/ws`

**Command Format:**
```json
{
  "type": "joint_angles",
  "data": {
    "joint1_yaw": 45.0,
    "joint2_pitch": -30.0,
    "joint3_pitch": 15.0,
    "joint4_roll": 0.0
  }
}
```

**Other Commands:**
```json
{"type": "emergency_stop"}
{"type": "reset"}
{"type": "gripper", "open": true}
```

### HTTP Endpoints

#### GET `/status`
Returns current joint states and system status.

**Response:**
```json
{
  "joints": {
    "joint1_yaw": {
      "angle": 45.0,
      "pwm": 1750,
      "timestamp": 1699999999.123
    }
  },
  "emergency_stopped": false,
  "uptime": 3600.5,
  "last_command": 0.1,
  "hardware_mode": "real"
}
```

#### GET `/health`
Health check endpoint for monitoring.

**Response:**
```json
{
  "status": "healthy",
  "uptime": 3600.5,
  "mode": "real"
}
```

#### GET `/info`
Extension information.

#### POST `/command`
Send commands via HTTP POST (alternative to WebSocket).

## Performance

Optimized for minimal resource usage on Raspberry Pi:
- **CPU Usage**: <5% idle, <15% under load
- **Memory**: ~50MB RAM
- **Update Rate**: 50Hz servo control loop
- **Latency**: <20ms command processing

All heavy processing (logging, telemetry, 3D rendering) happens on the Mac relay.

## Safety Features

- **Rate Limiting**: Maximum 180°/second joint velocity
- **Angle Clamping**: Prevents servo overtravel
- **Emergency Stop**: Immediate servo disable
- **Connection Timeout**: Safe mode if commands stop
- **Graceful Shutdown**: Proper GPIO cleanup

## Troubleshooting

### Container won't start
```bash
# Check container logs
docker logs trident-subsea

# Verify pigpio is available
docker exec -it trident-subsea pigpiod -v
```

### Servos not responding
1. Verify GPIO connections and power supply
2. Check pigpio daemon is running:
   ```bash
   docker exec -it trident-subsea pgrep pigpiod
   ```
3. Ensure container has privileged access and device mapping

### Can't connect from Mac relay
1. Verify BlueOS IP address: `ping blueos.local`
2. Check firewall settings on Raspberry Pi
3. Test WebSocket connection:
   ```bash
   wscat -c ws://blueos.local:9091/ws
   ```

### Running in simulation mode
If you see "running in simulation mode" in logs:
- Hardware (GPIO) is not available
- Useful for testing without physical hardware
- Commands are logged but not actuated

## Development

### Local Testing (Without Hardware)

```bash
cd blueos-extension
docker-compose up
```

Access at `http://localhost:9091/status`

### Code Structure

```
blueos-extension/
├── Dockerfile              # Container definition
├── docker-compose.yml      # Local dev configuration
├── requirements.txt        # Python dependencies
├── start.sh               # Container startup script
└── app/
    └── main.py            # Minimal servo controller
```

## Requirements

- **BlueOS**: Version 1.1 or later
- **Hardware**: Raspberry Pi 3B+ or 4
- **GPIO**: Access to GPIO pins 12-15
- **Power**: Adequate power supply for servos
- **Network**: Ethernet or WiFi connection to Mac

## License

GPL-3.0 - See LICENSE file for details

## Support

- **Issues**: https://github.com/ccavens/TRIDENT/issues
- **Documentation**: https://github.com/ccavens/TRIDENT
- **BlueOS**: https://blueos.cloud

## Version History

- **1.0.0** - Initial release
  - Basic servo control
  - WebSocket and HTTP API
  - BlueOS integration
  - Simulation mode support
