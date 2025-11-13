# TRIDENT Deployment Guide

Complete guide for deploying TRIDENT on BlueOS with Mac relay.

## Table of Contents

1. [System Requirements](#system-requirements)
2. [Network Configuration](#network-configuration)
3. [BlueOS Extension Deployment](#blueos-extension-deployment)
4. [Mac Relay Setup](#mac-relay-setup)
5. [Hardware Configuration](#hardware-configuration)
6. [Testing & Verification](#testing--verification)
7. [Production Deployment](#production-deployment)

## System Requirements

### Hardware

**Subsea System:**
- Raspberry Pi 3B+ or 4 (2GB+ RAM recommended)
- BlueOS 1.1 or later installed
- 4x HSR-M9382TH servo motors (or compatible 500-2500μs PWM)
- External servo power supply (5-7.4V, adequate current for all servos)
- GPIO access (pins 12-15)
- Ethernet or WiFi connection

**Topside System (Mac):**
- macOS 10.14+ (or Linux/Windows)
- Python 3.9 or later
- 4GB+ RAM
- Network connection to BlueOS system

**Optional - Topside Sensors:**
- Separate Raspberry Pi
- 4x AS5600 magnetic angle sensors
- I2C connections

### Software

**BlueOS System:**
- Docker (included with BlueOS)
- BlueOS Extension Manager
- Internet connection (for initial setup)

**Mac System:**
- Python 3.9+
- pip package manager
- Git
- Modern web browser (Chrome, Firefox, Safari, Edge)

## Network Configuration

### Basic Network Setup

**Option 1: Direct Connection (Simplest)**
```
Mac ←→ Ethernet ←→ Raspberry Pi (BlueOS)
```

1. Connect Mac directly to Raspberry Pi via Ethernet
2. Configure static IP on Mac:
   - IP: 192.168.2.1
   - Subnet: 255.255.255.0
3. Raspberry Pi should be at 192.168.2.2

**Option 2: WiFi Network (Most Common)**
```
Mac ←→ WiFi Router ←→ Raspberry Pi (BlueOS)
```

1. Connect both devices to same WiFi network
2. Find BlueOS IP address:
   ```bash
   ping blueos.local
   # or check router DHCP leases
   ```

**Option 3: ROV Tether**
```
Mac ←→ Tether Interface ←→ Raspberry Pi (BlueOS)
```

Follow your ROV's standard tether configuration.

### Verify Network Connectivity

```bash
# Test from Mac
ping blueos.local
# or
ping 192.168.2.2

# SSH into BlueOS
ssh pi@blueos.local
# Default password: usually 'raspberry' or configured in BlueOS
```

## BlueOS Extension Deployment

### Method 1: Extension Manager (Easiest)

1. **Access BlueOS Interface**
   ```
   http://blueos.local
   ```

2. **Open Extensions Manager**
   - Navigate to sidebar → Extensions

3. **Install TRIDENT**
   - Search for "TRIDENT"
   - Click "Install"
   - Wait for download and deployment

4. **Verify Installation**
   - Extension should show "Running" status
   - Note the port: 9091

### Method 2: Manual Docker Deployment

1. **SSH into BlueOS**
   ```bash
   ssh pi@blueos.local
   ```

2. **Clone Repository**
   ```bash
   cd ~
   git clone https://github.com/ccavens/TRIDENT.git
   cd TRIDENT/blueos-extension
   ```

3. **Deploy with Docker Compose**
   ```bash
   docker-compose up -d
   ```

4. **Verify Container**
   ```bash
   docker ps | grep trident-subsea
   docker logs trident-subsea
   ```

### Method 3: Build from Source

1. **SSH into BlueOS**
   ```bash
   ssh pi@blueos.local
   ```

2. **Clone and Build**
   ```bash
   cd ~
   git clone https://github.com/ccavens/TRIDENT.git
   cd TRIDENT/blueos-extension

   # Build image
   docker build -t trident-subsea:latest .

   # Run container
   docker run -d \
     --name trident-subsea \
     --privileged \
     --network host \
     --device /dev:/dev \
     --restart unless-stopped \
     trident-subsea:latest
   ```

3. **Verify**
   ```bash
   # Check container status
   docker ps

   # Check logs
   docker logs -f trident-subsea

   # Test endpoint
   curl http://localhost:9091/status
   ```

## Mac Relay Setup

### 1. Install Prerequisites

**Install Python 3.9+** (if not already installed):
```bash
# Check current version
python3 --version

# macOS with Homebrew
brew install python@3.11

# Or download from python.org
```

### 2. Clone Repository

```bash
cd ~/Documents  # or your preferred location
git clone https://github.com/ccavens/TRIDENT.git
cd TRIDENT/relay
```

### 3. Install Dependencies

**Option A: Using pip directly**
```bash
pip3 install aiohttp aiohttp-cors psutil numpy
```

**Option B: Using requirements.txt**
```bash
# Create requirements.txt if needed
cat > requirements.txt << EOF
aiohttp==3.9.1
aiohttp-cors==0.7.0
psutil==5.9.6
numpy==1.26.2
EOF

# Install
pip3 install -r requirements.txt
```

**Option C: Using virtual environment (recommended)**
```bash
# Create virtual environment
python3 -m venv venv

# Activate
source venv/bin/activate  # macOS/Linux
# or
venv\Scripts\activate  # Windows

# Install dependencies
pip install aiohttp aiohttp-cors psutil numpy
```

### 4. Configure Relay

Edit `config.json`:

```bash
cd relay
cat > config.json << EOF
{
  "network": {
    "relay_port": 9090,
    "web_port": 8090,
    "subsea_url": "ws://blueos.local:9091/ws",
    "control_timeout": 5.0,
    "subsea_timeout": 10.0,
    "reconnect_delay": 5.0,
    "heartbeat_interval": 1.0
  },
  "data": {
    "buffer_size": 1000,
    "log_enabled": true,
    "log_dir": "logs",
    "record_telemetry": true
  },
  "safety": {
    "max_latency": 100,
    "auto_reconnect": true,
    "rate_limit": 100
  },
  "simulation": {
    "enabled": false,
    "add_noise": false,
    "latency_ms": 20
  }
}
EOF
```

**Important**: Update `subsea_url` with your actual BlueOS IP:
- Use `ws://blueos.local:9091/ws` if mDNS works
- Or use `ws://192.168.2.2:9091/ws` (replace with actual IP)

### 5. Test Configuration

```bash
# Test network connectivity
ping blueos.local

# Test WebSocket endpoint
curl http://blueos.local:9091/status

# Should return JSON with joint states
```

## Hardware Configuration

### GPIO Pin Connections

**Servo Signal Wires (Raspberry Pi):**
```
Joint 1 (Yaw)   → GPIO 12 (Pin 32)
Joint 2 (Pitch) → GPIO 13 (Pin 33)
Joint 3 (Pitch) → GPIO 14 (Pin 8)
Joint 4 (Roll)  → GPIO 15 (Pin 10)

Ground → GND (Pin 6, 9, 14, 20, 25, 30, 34, 39)
```

### Power Connections

**Critical: Servo Power Supply**

```
Servo Power Supply (5-7.4V):
  [+] → Servo power rails (red wires)
  [-] → Servo ground (black wires) AND Raspberry Pi GND

WARNING: Do NOT power servos from Raspberry Pi 5V!
Use external power supply with adequate current capacity.
```

**Current Requirements:**
- Each HSR-M9382TH: ~500mA idle, 2A peak
- 4 servos total: Recommend 10A+ power supply
- Voltage: 5V-7.4V (check servo specs)

### Servo Calibration

After wiring, test each servo:

```bash
# SSH into BlueOS
ssh pi@blueos.local

# Test servo directly with pigpio
pigs s 12 1500  # Joint 1 center
pigs s 13 1500  # Joint 2 center
pigs s 14 1500  # Joint 3 center
pigs s 15 1500  # Joint 4 center

# Test range
pigs s 12 500   # Joint 1 min
pigs s 12 2500  # Joint 1 max
pigs s 12 1500  # Joint 1 center
```

**Safety**: Test servos with NO load first!

## Testing & Verification

### 1. Test BlueOS Extension

```bash
# Check container is running
ssh pi@blueos.local
docker ps | grep trident-subsea

# Expected output:
# trident-subsea    Up 5 minutes    (healthy)

# Check logs
docker logs trident-subsea

# Expected:
# "Starting TRIDENT Subsea Controller on port 9091"
# "Servo controller initialized"
# "Web application created"
```

**Test endpoints:**
```bash
# From Mac or from BlueOS
curl http://blueos.local:9091/health
# Expected: {"status": "healthy", "uptime": 123.45, "mode": "real"}

curl http://blueos.local:9091/status
# Expected: JSON with joint states
```

### 2. Test Mac Relay

```bash
cd TRIDENT/relay

# Start relay
python3 relay.py

# Expected output:
# "Starting relay on port 9090"
# "Starting web server on port 8090"
# "Connected to subsea at ws://blueos.local:9091/ws"
```

**In another terminal:**
```bash
# Test relay status
curl http://localhost:8090/status

# Expected: JSON with system status and connections
```

### 3. Test Web Dashboard

1. Open browser: `http://localhost:8090`
2. Verify:
   - 3D arm visualization loads
   - Connection indicators show "Connected"
   - Joint sliders are responsive
   - System stats update in real-time

### 4. Test Manual Control

1. In web dashboard, click "Manual Mode"
2. Move Joint 1 slider
3. Verify:
   - 3D model updates
   - Physical servo moves (if connected)
   - Latency < 100ms

### 5. Test Emergency Stop

1. Click "Emergency Stop" button
2. Verify:
   - All servos stop immediately
   - Dashboard shows emergency state
   - Servos cannot be commanded

3. Click "Reset" to resume

## Production Deployment

### Auto-Start Configuration

**BlueOS Extension:**
- Already configured to auto-start with Docker `--restart unless-stopped`
- Will start automatically when BlueOS boots

**Mac Relay (macOS):**

Create launch agent:

```bash
# Create plist file
cat > ~/Library/LaunchAgents/com.trident.relay.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.trident.relay</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>/Users/YOUR_USERNAME/Documents/TRIDENT/relay/relay.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/trident-relay.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/trident-relay-error.log</string>
</dict>
</plist>
EOF

# Update YOUR_USERNAME in the file
# Then load
launchctl load ~/Library/LaunchAgents/com.trident.relay.plist
```

### Monitoring

**Check BlueOS Extension:**
```bash
ssh pi@blueos.local
docker stats trident-subsea
docker logs -f --tail 100 trident-subsea
```

**Check Mac Relay:**
```bash
tail -f logs/*.jsonl  # Telemetry logs
tail -f /tmp/trident-relay.log  # If using launchd
```

**Web Dashboard:**
- Monitor connection status indicators
- Watch latency (should be <100ms)
- Check CPU/memory graphs

### Backup Configuration

```bash
# Backup Mac relay config
cp relay/config.json relay/config.json.backup

# Backup BlueOS extension (if modified)
ssh pi@blueos.local
docker commit trident-subsea trident-subsea-backup:$(date +%Y%m%d)
```

### Updates

**Update BlueOS Extension:**
```bash
ssh pi@blueos.local
cd TRIDENT/blueos-extension
git pull
docker-compose down
docker-compose build
docker-compose up -d
```

**Update Mac Relay:**
```bash
cd TRIDENT
git pull
# Restart relay (or launchd service)
```

## Troubleshooting

See main [README.md](README.md) troubleshooting section for common issues.

## Next Steps

- Configure topside sensors (if applicable)
- Calibrate servo ranges for your specific arm
- Customize 3D visualization
- Set up additional safety features
- Configure data logging retention

---

For issues or questions:
- GitHub Issues: https://github.com/ccavens/TRIDENT/issues
- BlueOS Documentation: https://blueos.cloud
