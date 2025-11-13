
# TRIDENT

TRIDENT (Teleoperated Robotic Interface for Dynamic Environmental Navigation & Tasks) is an open‑source, 5‑degree‑of‑freedom underwater manipulator system. It mirrors a miniature “topside” controller on land and drives a “subsea” arm through a network relay and web interface. The project targets remote operation scenarios such as ROV manipulation, environmental sampling, and research experiments.

## Table of Contents
1. Project Overview

2. Architecture

3. Repository Structure

4. Installation

5. Running the System

6. API & Communication

7. Simulation Mode

8. Development Notes

9. License
## Project Overview
TRIDENT is a robust control pipeline for an underwater robotic arm:

- Topside Controller
  - Measures joint positions via AS5600 magnetic sensors and streams data to a network relay.

- Relay (Mac/Web Server)
  - Coordinates communication, logs telemetry, and provides a real‑time web dashboard for monitoring and manual control.

- Subsea Controller
  - Receives joint commands and drives HSR‑M9382TH servos using PWM via a Raspberry Pi.

- Web Interface
  - Offers interactive status panels, manual override sliders, and visualization of joint angles and system health.

The codebase is written primarily in Python and makes heavy use of asynchronous event loops (```asyncio```) for efficient networking.
## Architecture

TRIDENT uses a distributed architecture optimized for minimal load on the subsea Raspberry Pi. Most processing happens on the MacBook (relay + web interface), while the subsea controller only handles critical servo control.

```
┌─────────────────────────────────────────────────────────────────┐
│                    MacBook (Heavy Processing)                    │
│  ┌────────────────┐   ┌──────────────┐   ┌──────────────────┐  │
│  │ Topside Sensors│──▶│ Relay Server │──▶│ Web Dashboard    │  │
│  │  (Optional)    │   │   Python     │   │ Browser + 3D viz │  │
│  └────────────────┘   └──────┬───────┘   └──────────────────┘  │
└────────────────────────────────┼──────────────────────────────────┘
                                 │ WebSocket
                                 │ ws://blueos.local:9091/ws
┌────────────────────────────────┼──────────────────────────────────┐
│        Raspberry Pi (BlueOS - Minimal Processing)                 │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │          TRIDENT BlueOS Extension (Container)            │    │
│  │  ┌──────────────┐          ┌────────────────────────┐   │    │
│  │  │   Subsea     │─────────▶│   Servo Controller     │   │    │
│  │  │   Service    │          │ 4x HSR-M9382TH Servos  │   │    │
│  │  │  Port 9091   │          │   via GPIO PWM         │   │    │
│  │  └──────────────┘          └────────────────────────┘   │    │
│  └──────────────────────────────────────────────────────────┘    │
└───────────────────────────────────────────────────────────────────┘
```

### Key Design Principles

1. **Minimal Subsea Load**: Only servo control runs on Raspberry Pi/BlueOS
2. **Heavy Mac Processing**: Logging, telemetry, web serving, sensor processing on MacBook
3. **BlueOS Integration**: Containerized extension for easy deployment and management
4. **Real-time Communication**: WebSocket connection between relay and subsea

## Repository Structure

```
TRIDENT/
├── blueos-extension/         # BlueOS containerized extension (NEW)
│   ├── Dockerfile            # Container definition
│   ├── docker-compose.yml    # Local dev configuration
│   ├── requirements.txt      # Python dependencies
│   ├── start.sh             # Container startup script
│   ├── README.md            # BlueOS extension docs
│   └── app/
│       └── main.py          # Minimal subsea controller
├── raspberrypi/             # Legacy standalone scripts
│   ├── subsea/
│   │   └── subseamain.py   # Original subsea controller
│   └── topside/
│       └── topsidemain.py  # Sensor reader (optional)
├── relay/                   # Mac relay server
│   ├── relay.py            # Relay & web server
│   ├── config.json         # Configuration file
│   └── static/
│       └── index.html      # Web dashboard with 3D viz
├── README.md
├── LICENSE                 # GPL v3
└── .gitignore
```
## Installation

### Prerequisites

**For BlueOS Subsea System:**
- Raspberry Pi 3B+ or 4 running BlueOS 1.1+
- 4x HSR-M9382TH servos (or compatible)
- Adequate power supply for servos
- Network connection to Mac

**For Mac Relay:**
- macOS (or Linux/Windows)
- Python 3.9+
- Network connection to BlueOS system

### 1. Install BlueOS Extension (Subsea)

#### Option A: From BlueOS Extension Manager (Recommended)
```
1. Navigate to http://blueos.local in browser
2. Open Extensions Manager
3. Search for "TRIDENT"
4. Click Install
```

#### Option B: Manual Installation
```bash
# SSH into BlueOS Raspberry Pi
ssh pi@blueos.local

# Clone and deploy
git clone https://github.com/ccavens/TRIDENT.git
cd TRIDENT/blueos-extension
docker-compose up -d
```

See [blueos-extension/README.md](blueos-extension/README.md) for detailed extension documentation.

### 2. Install Mac Relay

```bash
# Clone repository
git clone https://github.com/ccavens/TRIDENT.git
cd TRIDENT/relay

# Install Python dependencies
pip install -r requirements.txt
# or
pip install aiohttp aiohttp-cors psutil numpy

# Update configuration
# Edit config.json to set your BlueOS IP address:
# "subsea_url": "ws://blueos.local:9091/ws"
```

### 3. Hardware Setup

**GPIO Connections (on Raspberry Pi):**
- Joint 1 (Yaw): GPIO 12
- Joint 2 (Pitch): GPIO 13
- Joint 3 (Pitch): GPIO 14
- Joint 4 (Roll): GPIO 15

**Servo Power:**
- Use external power supply (5-7.4V depending on servos)
- Common ground between Pi and servo power
- Proper current capacity for all servos

## Running the System

### Quick Start

**1. Start BlueOS Extension (Subsea)**

The extension should auto-start with BlueOS. Check status:
```bash
# Via BlueOS web interface
http://blueos.local:9091/status

# Or via command line
docker ps | grep trident-subsea
```

**2. Start Mac Relay**

```bash
cd TRIDENT/relay
python3 relay.py
```

The relay will:
- Start on port 9090 (relay) and 8090 (web interface)
- Connect to BlueOS subsea extension at ws://blueos.local:9091/ws
- Serve web dashboard

**3. Open Web Dashboard**

Navigate to `http://localhost:8090` in your browser.

You should see:
- 3D visualization of the arm
- Joint angle controls
- System status (connection, latency, CPU)
- Emergency stop and reset buttons

### Control Modes

**Follow Mode (Default):**
- If you have topside sensors, the arm follows the physical controller
- Sliders are disabled for display only

**Manual Mode:**
- Click "Manual Mode" button
- Use sliders to control each joint directly
- Useful for testing without topside sensors

### Optional: Run Topside Sensors

If you have a separate Raspberry Pi with AS5600 angle sensors:

```bash
# On topside Raspberry Pi
cd TRIDENT/raspberrypi/topside
python3 topsidemain.py
```

This will read sensors and send joint angles to the relay.
## API & Communication

### Communication Flow

```
Topside Sensors → Mac Relay → BlueOS Extension → Servos
                      ↓
                 Web Browser
```

### BlueOS Extension API

**WebSocket**: `ws://<blueos-ip>:9091/ws`

Send commands:
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

**HTTP REST API**:
- `GET /status` - Current joint states
- `GET /health` - Health check
- `GET /info` - Extension info
- `POST /command` - Send command via HTTP

### Relay API

**WebSocket for Servos**: `ws://localhost:9090/ws`
- Connect subsea controller (or extension) here

**WebSocket for Web Clients**: `ws://localhost:8090/web/ws`
- Web dashboard connects here for real-time updates

**HTTP REST API** (on port 8090):
- `GET /status` - System status
- `POST /command` - Submit command
- `GET /telemetry` - Historical data
- `GET /config` - Get configuration
- `POST /config` - Update configuration

See [blueos-extension/README.md](blueos-extension/README.md) for detailed API documentation.
## Simulation Mode

All components gracefully degrade when hardware is unavailable:

**BlueOS Extension (Subsea)**:
- Automatically detects if pigpio/GPIO is unavailable
- Logs intended PWM signals instead of actuating servos
- Perfect for testing logic without physical hardware

**Topside Sensors** (if used):
- Generates synthetic sensor values when I2C unavailable
- Emulates realistic joint motion

**Relay**:
- Supports `simulation.enabled` flag in config.json
- Can inject artificial latency and noise for testing
- Useful for testing system behavior under poor network conditions

### Running in Simulation Mode

```bash
# Test BlueOS extension locally (no GPIO)
cd blueos-extension
docker-compose up

# Test relay without subsea connection
cd relay
# Edit config.json: "simulation": {"enabled": true}
python3 relay.py

# Open web dashboard
open http://localhost:8090
```
## Features

- **BlueOS Integration**: Containerized extension for easy deployment
- **Minimal Subsea Load**: <5% CPU usage on Raspberry Pi, all heavy processing on Mac
- **Real-time Control**: 50Hz servo update rate, <20ms command latency
- **3D Visualization**: Three.js-based web interface with interactive arm model
- **Safety Features**: Emergency stop, rate limiting, angle clamping, connection timeout
- **Graceful Degradation**: Simulation mode when hardware unavailable
- **Telemetry Logging**: JSONL format with timestamps
- **System Monitoring**: CPU, memory, network, latency tracking
- **Manual Control**: Override mode for testing without topside sensors

## Performance

**Subsea Container (Raspberry Pi)**:
- CPU Usage: <5% idle, <15% active
- Memory: ~50MB RAM
- Update Rate: 50Hz control loop
- Latency: <20ms command processing

**Mac Relay**:
- Handles all logging, telemetry, web serving
- Real-time WebSocket communication
- System monitoring and data aggregation

## Development & Contributing

### Project Structure
- **blueos-extension/**: Containerized subsea controller for BlueOS
- **relay/**: Mac relay server and web interface
- **raspberrypi/**: Legacy standalone scripts (reference)

### Building the Extension

```bash
cd blueos-extension
docker build -t trident-subsea:latest .
docker tag trident-subsea:latest <dockerhub-user>/trident-subsea:latest
docker push <dockerhub-user>/trident-subsea:latest
```

### Testing

```bash
# Test subsea extension locally
cd blueos-extension
docker-compose up

# Test relay
cd relay
python3 relay.py

# Check status
curl http://localhost:9091/status
curl http://localhost:8090/status
```

## Troubleshooting

**Extension won't start**:
- Check Docker logs: `docker logs trident-subsea`
- Verify privileged mode and device mapping
- Ensure pigpio is available

**Can't connect from relay**:
- Verify BlueOS IP: `ping blueos.local`
- Check firewall settings
- Test WebSocket: `wscat -c ws://blueos.local:9091/ws`

**Servos not responding**:
- Verify GPIO connections (pins 12-15)
- Check servo power supply
- Ensure pigpio daemon running: `pgrep pigpiod`

**Web dashboard not loading**:
- Check relay is running: `curl http://localhost:8090/status`
- Verify port 8090 is not in use
- Check browser console for errors

## Support

- **Issues**: [GitHub Issues](https://github.com/ccavens/TRIDENT/issues)
- **BlueOS Docs**: [blueos.cloud](https://blueos.cloud)
- **Hardware**: HSR-M9382TH servo documentation
## License

[GNU General Public License v3.0](https://choosealicense.com/licenses/gpl-3.0/)

