#!/usr/bin/env python3
"""
Mac Relay Program
Bridges communication between Control Pi and Subsea Pi
Provides web interface for monitoring and control
"""

import asyncio
import json
import logging
import time
import os
from typing import Dict, Set, Optional
from dataclasses import dataclass, asdict
import numpy as np
from pathlib import Path

# Web framework
from aiohttp import web, ClientSession, WSMsgType
import aiohttp_cors

# System monitoring
import psutil
import platform

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
CONFIG = {
    "network": {
        "relay_port": 9090,
        "web_port": 8090,
        "subsea_url": "ws://127.0.0.1:8080/ws",  # Subsea Pi BlueOS extension
        "control_timeout": 5.0,
        "subsea_timeout": 10.0,
        "reconnect_delay": 5.0,
        "heartbeat_interval": 1.0
    },
    "data": {
        "buffer_size": 1000,
        "log_enabled": True,
        "log_dir": "logs",
        "record_telemetry": True
    },
    "safety": {
        "max_latency": 100,  # ms
        "auto_reconnect": True,
        "rate_limit": 100  # commands per second
    },
    "simulation": {
        "enabled": False,
        "add_noise": True,
        "latency_ms": 20
    }
}


@dataclass
class ConnectionStatus:
    """Status of a connection"""
    connected: bool
    last_message: float
    message_count: int
    error_count: int
    latency: float


@dataclass
class SystemStatus:
    """Overall system status"""
    control_connection: ConnectionStatus
    subsea_connection: ConnectionStatus
    relay_uptime: float
    cpu_percent: float
    memory_percent: float
    bandwidth_up: float
    bandwidth_down: float


class DataLogger:
    """Logs telemetry data to files"""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        if self.enabled:
            self.log_dir = Path(CONFIG["data"]["log_dir"])
            self.log_dir.mkdir(exist_ok=True)
            self.current_log = None
            self._start_new_log()

    def _start_new_log(self):
        """Start a new log file"""
        if not self.enabled:
            return

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        log_path = self.log_dir / f"telemetry_{timestamp}.jsonl"
        self.current_log = open(log_path, 'w')
        logger.info(f"Started new log file: {log_path}")

    def log_data(self, data_type: str, data: Dict):
        """Log data entry"""
        if not self.enabled or not self.current_log:
            return

        entry = {
            "timestamp": time.time(),
            "type": data_type,
            "data": data
        }

        self.current_log.write(json.dumps(entry) + '\n')
        self.current_log.flush()

    def close(self):
        """Close current log file"""
        if self.current_log:
            self.current_log.close()


class MacRelay:
    """Main relay system coordinating communication"""

    def __init__(self):
        self.control_clients: Set[web.WebSocketResponse] = set()
        self.web_clients: Set[web.WebSocketResponse] = set()
        self.subsea_ws = None
        self.subsea_session = None

        self.control_status = ConnectionStatus(
            connected=False,
            last_message=0,
            message_count=0,
            error_count=0,
            latency=0
        )

        self.subsea_status = ConnectionStatus(
            connected=False,
            last_message=0,
            message_count=0,
            error_count=0,
            latency=0
        )

        self.data_logger = DataLogger(CONFIG["data"]["log_enabled"])
        self.telemetry_buffer = []
        self.command_buffer = []
        self.start_time = time.time()

        # Rate limiting
        self.command_timestamps = []

        # Simulation mode
        self.simulation_mode = CONFIG["simulation"]["enabled"]
        self.sim_joint_angles = {
            "joint1_yaw": 0,
            "joint2_pitch": 0,
            "joint3_pitch": 0,
            "joint4_roll": 0
        }

    async def connect_to_subsea(self):
        """Establish connection to subsea Pi"""
        if self.simulation_mode:
            logger.info("Running in simulation mode - skipping subsea connection")
            self.subsea_status.connected = True
            return

        while not self.subsea_status.connected:
            try:
                if not self.subsea_session:
                    self.subsea_session = ClientSession()

                logger.info(f"Connecting to subsea at {CONFIG['network']['subsea_url']}")
                self.subsea_ws = await self.subsea_session.ws_connect(
                    CONFIG["network"]["subsea_url"],
                    heartbeat=30
                )

                self.subsea_status.connected = True
                self.subsea_status.last_message = time.time()
                logger.info("Connected to subsea system")

                # Start listening for subsea messages
                asyncio.create_task(self.handle_subsea_messages())

            except Exception as e:
                logger.error(f"Failed to connect to subsea: {e}")
                self.subsea_status.error_count += 1

                if CONFIG["safety"]["auto_reconnect"]:
                    await asyncio.sleep(CONFIG["network"]["reconnect_delay"])
                else:
                    break

    async def handle_subsea_messages(self):
        """Handle incoming messages from subsea"""
        while self.subsea_ws and not self.subsea_ws.closed:
            try:
                msg = await self.subsea_ws.receive()

                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    self.subsea_status.message_count += 1
                    self.subsea_status.last_message = time.time()

                    # Log telemetry
                    self.data_logger.log_data("subsea_telemetry", data)

                    # Broadcast to web clients
                    await self.broadcast_to_web({
                        "type": "subsea_status",
                        "data": data
                    })

                elif msg.type == WSMsgType.ERROR:
                    logger.error(f'Subsea WebSocket error: {self.subsea_ws.exception()}')
                    self.subsea_status.connected = False
                    self.subsea_status.error_count += 1

                elif msg.type == WSMsgType.CLOSED:
                    logger.info("Subsea WebSocket closed")
                    self.subsea_status.connected = False
                    break

            except Exception as e:
                logger.error(f"Error handling subsea message: {e}")
                self.subsea_status.error_count += 1

        # Reconnect if configured
        if CONFIG["safety"]["auto_reconnect"]:
            await self.connect_to_subsea()

    async def relay_to_subsea(self, message: Dict):
        """Relay message to subsea system"""
        # Rate limiting
        current_time = time.time()
        self.command_timestamps = [
            t for t in self.command_timestamps
            if current_time - t < 1.0
        ]

        if len(self.command_timestamps) >= CONFIG["safety"]["rate_limit"]:
            logger.warning("Rate limit exceeded - dropping command")
            return

        self.command_timestamps.append(current_time)

        if self.simulation_mode:
            # Simulate processing
            await self.simulate_subsea_response(message)
            return

        if self.subsea_ws and not self.subsea_ws.closed:
            try:
                # Add timestamp for latency measurement
                message["relay_timestamp"] = time.time()

                await self.subsea_ws.send_str(json.dumps(message))

                # Log command
                self.data_logger.log_data("command_sent", message)

            except Exception as e:
                logger.error(f"Failed to relay to subsea: {e}")
                self.subsea_status.error_count += 1
        else:
            logger.warning("Subsea not connected - buffering command")
            self.command_buffer.append(message)

    async def simulate_subsea_response(self, command: Dict):
        """Simulate subsea system response"""
        if CONFIG["simulation"]["latency_ms"] > 0:
            await asyncio.sleep(CONFIG["simulation"]["latency_ms"] / 1000.0)

        if command.get("type") == "joint_angles":
            # Update simulated joint angles
            angles = command.get("data", {})
            for joint, angle in angles.items():
                if joint in self.sim_joint_angles:
                    # Add some noise if configured
                    if CONFIG["simulation"]["add_noise"]:
                        angle += np.random.randn() * 0.5
                    self.sim_joint_angles[joint] = angle

            # Send simulated response
            response = {
                "type": "state_update",
                "data": {
                    "joints": {
                        joint: {
                            "angle": angle,
                            "target": angle,
                            "velocity": 0,
                            "pwm": 1500,
                            "timestamp": time.time()
                        }
                        for joint, angle in self.sim_joint_angles.items()
                    },
                    "emergency_stop": False,
                    "connection_status": "connected"
                },
                "timestamp": time.time()
            }

            await self.broadcast_to_web(response)

    async def broadcast_to_web(self, message: Dict):
        """Broadcast message to all web clients"""
        message_str = json.dumps(message)

        for ws in list(self.web_clients):
            try:
                await ws.send_str(message_str)
            except ConnectionResetError:
                self.web_clients.discard(ws)

    async def process_control_data(self, data: Dict):
        """Process data from control arm"""
        if data.get("type") == "sensor_data":
            sensor_data = data.get("data", {})

            # Convert to joint angles command
            command = {
                "type": "joint_angles",
                "data": sensor_data.get("angles", {}),
                "source": "control_arm",
                "timestamp": sensor_data.get("timestamp", time.time())
            }

            # Relay to subsea
            await self.relay_to_subsea(command)

            # Log telemetry
            self.data_logger.log_data("control_telemetry", sensor_data)

            # Update telemetry buffer
            self.telemetry_buffer.append(sensor_data)
            if len(self.telemetry_buffer) > CONFIG["data"]["buffer_size"]:
                self.telemetry_buffer.pop(0)

            # Broadcast to web clients
            await self.broadcast_to_web({
                "type": "control_data",
                "data": sensor_data
            })

    async def flush_command_buffer(self):
        """Send buffered commands when connection restored"""
        if not self.command_buffer:
            return

        logger.info(f"Flushing {len(self.command_buffer)} buffered commands")

        for command in self.command_buffer:
            await self.relay_to_subsea(command)
            await asyncio.sleep(0.01)  # Small delay between commands

        self.command_buffer.clear()

    def get_system_status(self) -> Dict:
        """Get current system status"""
        return {
            "relay": {
                "uptime": time.time() - self.start_time,
                "simulation_mode": self.simulation_mode,
                "telemetry_buffer_size": len(self.telemetry_buffer),
                "command_buffer_size": len(self.command_buffer)
            },
            "control": asdict(self.control_status),
            "subsea": asdict(self.subsea_status),
            "system": {
                "platform": platform.system(),
                "cpu_percent": psutil.cpu_percent(),
                "memory_percent": psutil.virtual_memory().percent,
                "network": {
                    "bytes_sent": psutil.net_io_counters().bytes_sent,
                    "bytes_recv": psutil.net_io_counters().bytes_recv
                }
            }
        }


# Web handlers
async def control_ws_handler(request):
    """Handle WebSocket connections from control Pi"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    relay = request.app['relay']
    relay.control_clients.add(ws)
    relay.control_status.connected = True
    relay.control_status.last_message = time.time()

    logger.info("Control arm connected")

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    relay.control_status.message_count += 1
                    relay.control_status.last_message = time.time()

                    await relay.process_control_data(data)

                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON from control: {msg.data}")
                    relay.control_status.error_count += 1

            elif msg.type == WSMsgType.ERROR:
                logger.error(f'Control WebSocket error: {ws.exception()}')
                relay.control_status.error_count += 1

    finally:
        relay.control_clients.discard(ws)
        if not relay.control_clients:
            relay.control_status.connected = False
        logger.info("Control arm disconnected")

    return ws


async def web_ws_handler(request):
    """Handle WebSocket connections from web interface"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    relay = request.app['relay']
    relay.web_clients.add(ws)

    # Send initial status
    await ws.send_str(json.dumps({
        "type": "system_status",
        "data": relay.get_system_status()
    }))

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)

                    # Handle web interface commands
                    if data.get("type") == "command":
                        await relay.relay_to_subsea(data.get("data", {}))
                    elif data.get("type") == "get_status":
                        await ws.send_str(json.dumps({
                            "type": "system_status",
                            "data": relay.get_system_status()
                        }))

                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON from web client: {msg.data}")

    finally:
        relay.web_clients.discard(ws)

    return ws


async def status_handler(request):
    """REST API for system status"""
    relay = request.app['relay']
    return web.json_response(relay.get_system_status())


async def command_handler(request):
    """REST API for sending commands"""
    try:
        data = await request.json()
        relay = request.app['relay']
        await relay.relay_to_subsea(data)
        return web.json_response({"status": "accepted"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)


async def telemetry_handler(request):
    """Get recent telemetry data"""
    relay = request.app['relay']

    # Get last N entries from buffer
    count = int(request.query.get('count', 100))

    return web.json_response({
        "telemetry": relay.telemetry_buffer[-count:],
        "total_entries": len(relay.telemetry_buffer)
    })


async def config_handler(request):
    """Get or update configuration"""
    if request.method == 'GET':
        return web.json_response(CONFIG)

    elif request.method == 'POST':
        try:
            updates = await request.json()

            # Update configuration
            for section, values in updates.items():
                if section in CONFIG:
                    CONFIG[section].update(values)

            # Apply changes
            relay = request.app['relay']
            relay.simulation_mode = CONFIG["simulation"]["enabled"]

            return web.json_response({"status": "updated", "config": CONFIG})

        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)


async def heartbeat_loop(app):
    """Send periodic heartbeats and status updates"""
    relay = app['relay']

    while True:
        try:
            # Check connection timeouts
            current_time = time.time()

            if relay.control_status.connected:
                if current_time - relay.control_status.last_message > CONFIG["network"]["control_timeout"]:
                    relay.control_status.connected = False
                    logger.warning("Control connection timeout")

            if relay.subsea_status.connected and not relay.simulation_mode:
                if current_time - relay.subsea_status.last_message > CONFIG["network"]["subsea_timeout"]:
                    relay.subsea_status.connected = False
                    logger.warning("Subsea connection timeout")

            # Send status update to web clients
            await relay.broadcast_to_web({
                "type": "heartbeat",
                "data": relay.get_system_status(),
                "timestamp": current_time
            })

            await asyncio.sleep(CONFIG["network"]["heartbeat_interval"])

        except Exception as e:
            logger.error(f"Error in heartbeat loop: {e}")
            await asyncio.sleep(1.0)


def create_app():
    """Create web application"""
    app = web.Application()
    relay = MacRelay()
    app['relay'] = relay

    # Configure CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*"
        )
    })

    # Add routes
    app.router.add_get('/ws', control_ws_handler)
    app.router.add_get('/web/ws', web_ws_handler)
    app.router.add_get('/status', status_handler)
    app.router.add_post('/command', command_handler)
    app.router.add_get('/telemetry', telemetry_handler)
    # app.router.add_route('*', '/config', config_handler) -- caused crash due to cors preflight claiming same resource
    app.router.add_get('/config', config_handler)
    app.router.add_post('/config', config_handler)

    # Add static files for web interface
    static_path = Path(__file__).parent / 'static'
    if static_path.exists():
        app.router.add_static('/', path=str(static_path), name='static', show_index=True)

    # Configure CORS for routes
    for route in list(app.router.routes()):
        cors.add(route)

    return app, relay


async def on_startup(app):
    """Application startup handler"""
    relay = app['relay']

    # Start heartbeat loop
    asyncio.create_task(heartbeat_loop(app))

    # Connect to subsea if not in simulation mode
    if not relay.simulation_mode:
        asyncio.create_task(relay.connect_to_subsea())

    logger.info("Mac Relay started")


async def on_cleanup(app):
    """Application cleanup handler"""
    relay = app['relay']

    # Close connections
    if relay.subsea_ws:
        await relay.subsea_ws.close()
    if relay.subsea_session:
        await relay.subsea_session.close()

    # Close data logger
    relay.data_logger.close()

    logger.info("Mac Relay stopped")


async def main():
    """Main entry point"""
    app, relay = create_app()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    runner = web.AppRunner(app)
    await runner.setup()

    # Start relay server
    relay_site = web.TCPSite(runner, '0.0.0.0', CONFIG["network"]["relay_port"])
    await relay_site.start()
    logger.info(f"Relay server running on port {CONFIG['network']['relay_port']}")

    # Start web server
    web_site = web.TCPSite(runner, '0.0.0.0', CONFIG["network"]["web_port"])
    await web_site.start()
    logger.info(f"Web interface running on http://localhost:{CONFIG['network']['web_port']}")

    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())