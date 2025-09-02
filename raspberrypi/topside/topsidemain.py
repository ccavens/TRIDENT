#!/usr/bin/env python3
"""
Control Arm Sensor Reader
Reads AS5600 magnetic angle sensors and sends joint positions to Mac relay
"""

import asyncio
import json
import logging
import time
import numpy as np
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
import socket
import struct

# I2C communication
try:
    import smbus2

    HARDWARE_AVAILABLE = True
except ImportError:
    HARDWARE_AVAILABLE = False
    logging.warning("smbus2 not available - running in simulation mode")

# WebSocket client
import aiohttp
from aiohttp import ClientSession

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# AS5600 Register Addresses
AS5600_ADDR = 0x36
AS5600_REG_STATUS = 0x0B
AS5600_REG_RAW_ANGLE = 0x0C
AS5600_REG_ANGLE = 0x0E
AS5600_REG_CONF = 0x07
AS5600_REG_MAGNET = 0x0B

# Configuration
CONFIG = {
    "sensors": {
        "joint1_yaw": {
            "i2c_bus": 1,
            "i2c_address": 0x36,
            "offset": 0,
            "scale": 1.0,
            "inverted": False
        },
        "joint2_pitch": {
            "i2c_bus": 1,
            "i2c_address": 0x37,  # Using I2C multiplexer or different address
            "offset": 0,
            "scale": 1.0,
            "inverted": False
        },
        "joint3_pitch": {
            "i2c_bus": 1,
            "i2c_address": 0x38,
            "offset": 0,
            "scale": 1.0,
            "inverted": False
        },
        "joint4_roll": {
            "i2c_bus": 1,
            "i2c_address": 0x39,
            "offset": 0,
            "scale": 2.0,  # 360 degree range
            "inverted": False
        }
    },
    "network": {
        "relay_host": "127.0.0.1",  # Mac relay IP
        "relay_port": 9090,
        "websocket_url": "ws://127.0.0.1:9090/ws",
        "update_rate": 50,  # Hz
        "reconnect_delay": 5.0
    },
    "calibration": {
        "enabled": True,
        "samples": 100,
        "settle_time": 2.0
    },
    "filtering": {
        "enabled": True,
        "alpha": 0.2  # Exponential moving average filter
    }
}


@dataclass
class SensorReading:
    """Single sensor reading"""
    angle: float  # Angle in degrees
    raw: int  # Raw sensor value
    quality: float  # Magnet quality (0-1)
    timestamp: float


class AS5600Sensor:
    """Interface for AS5600 magnetic angle sensor"""

    def __init__(self, bus: int, address: int, simulation_mode: bool = False):
        self.simulation_mode = simulation_mode or not HARDWARE_AVAILABLE
        self.address = address
        self.bus = None

        if not self.simulation_mode:
            try:
                self.bus = smbus2.SMBus(bus)
                self._check_magnet()
            except Exception as e:
                logger.error(f"Failed to initialize I2C bus {bus}: {e}")
                self.simulation_mode = True

        self.last_angle = 0
        self.sim_angle = 0

    def _check_magnet(self) -> bool:
        """Check if magnet is detected"""
        if self.simulation_mode:
            return True

        try:
            status = self.bus.read_byte_data(self.address, AS5600_REG_MAGNET)
            magnet_detected = bool(status & 0x20)
            if not magnet_detected:
                logger.warning(f"No magnet detected on sensor {hex(self.address)}")
            return magnet_detected
        except Exception as e:
            logger.error(f"Failed to check magnet status: {e}")
            return False

    def read_angle(self) -> Optional[SensorReading]:
        """Read current angle from sensor"""
        if self.simulation_mode:
            # Simulate sensor reading
            self.sim_angle = (self.sim_angle + np.random.randn() * 0.5) % 360
            return SensorReading(
                angle=self.sim_angle,
                raw=int(self.sim_angle * 4096 / 360),
                quality=1.0,
                timestamp=time.time()
            )

        try:
            # Read raw angle (12-bit value)
            raw_bytes = self.bus.read_i2c_block_data(
                self.address, AS5600_REG_RAW_ANGLE, 2
            )
            raw_angle = (raw_bytes[0] << 8) | raw_bytes[1]

            # Convert to degrees
            angle = (raw_angle / 4096.0) * 360.0

            # Read magnet status for quality
            status = self.bus.read_byte_data(self.address, AS5600_REG_MAGNET)
            quality = 1.0 if (status & 0x20) else 0.0

            return SensorReading(
                angle=angle,
                raw=raw_angle,
                quality=quality,
                timestamp=time.time()
            )

        except Exception as e:
            logger.error(f"Failed to read angle from sensor {hex(self.address)}: {e}")
            return None


class ControlArmSensor:
    """Main control arm sensor system"""

    def __init__(self):
        self.sensors: Dict[str, AS5600Sensor] = {}
        self.readings: Dict[str, SensorReading] = {}
        self.filtered_angles: Dict[str, float] = {}
        self.calibration_data: Dict[str, Dict] = {}
        self.connected = False
        self.websocket = None
        self.session = None

        # Initialize sensors
        self._initialize_sensors()

        # Initialize filters
        for joint in CONFIG["sensors"].keys():
            self.filtered_angles[joint] = 0.0

    def _initialize_sensors(self):
        """Initialize all AS5600 sensors"""
        for joint, config in CONFIG["sensors"].items():
            try:
                sensor = AS5600Sensor(
                    bus=config["i2c_bus"],
                    address=config["i2c_address"]
                )
                self.sensors[joint] = sensor
                logger.info(f"Initialized sensor for {joint}")
            except Exception as e:
                logger.error(f"Failed to initialize sensor for {joint}: {e}")
                # Create simulation sensor
                self.sensors[joint] = AS5600Sensor(0, 0, simulation_mode=True)

    async def calibrate_sensors(self):
        """Calibrate all sensors to find zero position"""
        if not CONFIG["calibration"]["enabled"]:
            return

        logger.info("Starting sensor calibration...")
        logger.info(
            f"Please move all joints to home position and hold for {CONFIG['calibration']['settle_time']} seconds")

        await asyncio.sleep(CONFIG["calibration"]["settle_time"])

        for joint, sensor in self.sensors.items():
            readings = []

            # Collect calibration samples
            for _ in range(CONFIG["calibration"]["samples"]):
                reading = sensor.read_angle()
                if reading:
                    readings.append(reading.angle)
                await asyncio.sleep(0.01)

            if readings:
                # Calculate average as offset
                offset = np.mean(readings)
                std_dev = np.std(readings)

                self.calibration_data[joint] = {
                    "offset": offset,
                    "std_dev": std_dev,
                    "samples": len(readings)
                }

                # Update configuration
                CONFIG["sensors"][joint]["offset"] = offset

                logger.info(f"Calibrated {joint}: offset={offset:.2f}°, std={std_dev:.2f}°")
            else:
                logger.error(f"Failed to calibrate {joint}")

        logger.info("Calibration complete")

    def apply_calibration(self, joint: str, angle: float) -> float:
        """Apply calibration and scaling to raw angle"""
        config = CONFIG["sensors"][joint]

        # Apply offset
        angle = angle - config["offset"]

        # Normalize to [-180, 180]
        while angle > 180:
            angle -= 360
        while angle < -180:
            angle += 360

        # Apply scale
        angle = angle * config["scale"]

        # Apply inversion if needed
        if config["inverted"]:
            angle = -angle

        return angle

    def apply_filter(self, joint: str, angle: float) -> float:
        """Apply exponential moving average filter"""
        if not CONFIG["filtering"]["enabled"]:
            return angle

        alpha = CONFIG["filtering"]["alpha"]

        # Handle angle wrapping
        prev = self.filtered_angles[joint]

        # Find shortest angular distance
        delta = angle - prev
        if delta > 180:
            delta -= 360
        elif delta < -180:
            delta += 360

        # Apply filter
        filtered = prev + alpha * delta

        # Normalize
        while filtered > 180:
            filtered -= 360
        while filtered < -180:
            filtered += 360

        self.filtered_angles[joint] = filtered
        return filtered

    async def read_all_sensors(self) -> Dict[str, float]:
        """Read all sensors and return processed angles"""
        angles = {}

        for joint, sensor in self.sensors.items():
            reading = sensor.read_angle()

            if reading and reading.quality > 0.5:
                # Store raw reading
                self.readings[joint] = reading

                # Apply calibration
                angle = self.apply_calibration(joint, reading.angle)

                # Apply filter
                angle = self.apply_filter(joint, angle)

                angles[joint] = angle
            else:
                # Use last known good value
                if joint in self.filtered_angles:
                    angles[joint] = self.filtered_angles[joint]
                else:
                    angles[joint] = 0.0

        return angles

    async def connect_to_relay(self):
        """Establish connection to Mac relay"""
        if not self.session:
            self.session = ClientSession()

        while not self.connected:
            try:
                logger.info(f"Connecting to relay at {CONFIG['network']['websocket_url']}")
                self.websocket = await self.session.ws_connect(
                    CONFIG["network"]["websocket_url"],
                    heartbeat=30
                )
                self.connected = True
                logger.info("Connected to relay")

                # Send initial handshake
                await self.send_message({
                    "type": "handshake",
                    "source": "control_arm",
                    "version": "1.0.0"
                })

            except Exception as e:
                logger.error(f"Failed to connect to relay: {e}")
                await asyncio.sleep(CONFIG["network"]["reconnect_delay"])

    async def send_message(self, message: Dict):
        """Send message to relay"""
        if self.websocket and not self.websocket.closed:
            try:
                await self.websocket.send_str(json.dumps(message))
            except Exception as e:
                logger.error(f"Failed to send message: {e}")
                self.connected = False

    async def sensor_loop(self):
        """Main sensor reading and transmission loop"""
        update_interval = 1.0 / CONFIG["network"]["update_rate"]

        while True:
            try:
                # Ensure connection
                if not self.connected:
                    await self.connect_to_relay()

                # Read sensors
                angles = await self.read_all_sensors()

                # Prepare telemetry
                telemetry = {
                    "timestamp": time.time(),
                    "angles": angles,
                    "qualities": {
                        joint: reading.quality
                        for joint, reading in self.readings.items()
                    },
                    "raw_values": {
                        joint: reading.raw
                        for joint, reading in self.readings.items()
                    }
                }

                # Send to relay
                await self.send_message({
                    "type": "sensor_data",
                    "data": telemetry
                })

                await asyncio.sleep(update_interval)

            except Exception as e:
                logger.error(f"Error in sensor loop: {e}")
                self.connected = False
                await asyncio.sleep(1.0)

    async def handle_commands(self):
        """Handle incoming commands from relay"""
        while True:
            if self.websocket and not self.websocket.closed:
                try:
                    msg = await self.websocket.receive()

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        command = json.loads(msg.data)
                        await self.process_command(command)
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        logger.error(f'WebSocket error: {self.websocket.exception()}')
                        self.connected = False
                    elif msg.type == aiohttp.WSMsgType.CLOSED:
                        logger.info("WebSocket closed")
                        self.connected = False

                except Exception as e:
                    logger.error(f"Error handling command: {e}")
                    self.connected = False
            else:
                await asyncio.sleep(1.0)

    async def process_command(self, command: Dict):
        """Process command from relay"""
        cmd_type = command.get("type")

        if cmd_type == "calibrate":
            await self.calibrate_sensors()
        elif cmd_type == "config_update":
            self.update_config(command.get("config", {}))
        elif cmd_type == "reset_filters":
            for joint in self.filtered_angles:
                self.filtered_angles[joint] = 0.0
        else:
            logger.warning(f"Unknown command type: {cmd_type}")

    def update_config(self, config: Dict):
        """Update configuration dynamically"""
        for section, values in config.items():
            if section in CONFIG:
                CONFIG[section].update(values)
        logger.info("Configuration updated")

    async def cleanup(self):
        """Clean up resources"""
        if self.websocket:
            await self.websocket.close()
        if self.session:
            await self.session.close()


async def main():
    """Main entry point"""
    sensor_system = ControlArmSensor()

    # Calibrate on startup if enabled
    if CONFIG["calibration"]["enabled"]:
        await sensor_system.calibrate_sensors()

    # Start sensor loop and command handler
    try:
        await asyncio.gather(
            sensor_system.sensor_loop(),
            sensor_system.handle_commands()
        )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await sensor_system.cleanup()


if __name__ == "__main__":
    asyncio.run(main())