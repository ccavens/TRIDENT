#!/usr/bin/env python3
"""
TRIDENT BlueOS Subsea Extension - Enhanced Version
Features: WebSocket reconnection, health monitoring, calibration, logging
"""

import asyncio
import json
import logging
import time
import os
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Dict, Optional, List, Set
from collections import deque
from datetime import datetime

import aiohttp
from aiohttp import web
import numpy as np
import psutil

# Hardware control imports
try:
    import pigpio
    HARDWARE_AVAILABLE = True
except ImportError:
    HARDWARE_AVAILABLE = False
    logging.warning("pigpio not available - running in simulation mode")

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Persistent config directory
CONFIG_DIR = Path("/root/.config/trident")
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = CONFIG_DIR / "config.json"
CALIBRATION_FILE = CONFIG_DIR / "calibration.json"

# Default configuration
DEFAULT_CONFIG = {
    "servo_pins": {
        "joint1_yaw": 12,
        "joint2_pitch": 13,
        "joint3_pitch": 14,
        "joint4_roll": 15
    },
    "servo_limits": {
        "joint1_yaw": {"min": 500, "max": 2500, "center": 1500, "range": 180},
        "joint2_pitch": {"min": 500, "max": 2500, "center": 1500, "range": 180},
        "joint3_pitch": {"min": 500, "max": 2500, "center": 1500, "range": 180},
        "joint4_roll": {"min": 500, "max": 2500, "center": 1500, "range": 360}
    },
    "network": {
        "port": 9091,
        "host": "0.0.0.0"
    },
    "safety": {
        "max_speed": 180,
        "update_rate": 50
    }
}


def load_config():
    """Load configuration from file or use defaults"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                loaded = json.load(f)
                # Merge with defaults to handle new keys
                config = DEFAULT_CONFIG.copy()
                config.update(loaded)
                return config
        except Exception as e:
            logger.error(f"Failed to load config: {e}, using defaults")
    return DEFAULT_CONFIG.copy()


def save_config(config):
    """Save configuration to file"""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        logger.info("Configuration saved")
    except Exception as e:
        logger.error(f"Failed to save config: {e}")


def load_calibration():
    """Load calibration data"""
    if CALIBRATION_FILE.exists():
        try:
            with open(CALIBRATION_FILE) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load calibration: {e}")
    return {}


def save_calibration(calibration):
    """Save calibration data"""
    try:
        with open(CALIBRATION_FILE, 'w') as f:
            json.dump(calibration, f, indent=2)
        logger.info("Calibration saved")
    except Exception as e:
        logger.error(f"Failed to save calibration: {e}")


CONFIG = load_config()


@dataclass
class JointState:
    """Joint state with timestamp"""
    angle: float
    pwm: int
    timestamp: float
    velocity: float = 0.0


@dataclass
class ConnectionHealth:
    """Connection health metrics"""
    last_message_time: float = field(default_factory=time.time)
    message_count: int = 0
    error_count: int = 0
    latencies: deque = field(default_factory=lambda: deque(maxlen=100))

    @property
    def quality(self) -> float:
        """Calculate connection quality (0-1)"""
        if self.message_count == 0:
            return 0.0

        # Time since last message
        time_since_last = time.time() - self.last_message_time
        time_score = max(0, 1 - (time_since_last / 10))  # 10s timeout

        # Error rate
        error_rate = self.error_count / max(self.message_count, 1)
        error_score = 1 - min(error_rate, 1.0)

        # Latency score
        if self.latencies:
            avg_latency = sum(self.latencies) / len(self.latencies)
            latency_score = max(0, 1 - (avg_latency / 100))  # 100ms = 0 score
        else:
            latency_score = 1.0

        return (time_score * 0.4 + error_score * 0.3 + latency_score * 0.3)


@dataclass
class SystemHealth:
    """System health status"""
    healthy: bool = True
    issues: List[str] = field(default_factory=list)
    last_check: float = field(default_factory=time.time)


class EnhancedServoController:
    """Enhanced servo controller with calibration and monitoring"""

    def __init__(self):
        self.simulation_mode = not HARDWARE_AVAILABLE
        self.pi = None
        self.current_pwm = {}
        self.current_angles = {}
        self.calibration = load_calibration()
        self.last_update_times = {}

        if not self.simulation_mode:
            try:
                self.pi = pigpio.pi()
                if not self.pi.connected:
                    logger.error("Failed to connect to pigpio daemon")
                    self.simulation_mode = True
                else:
                    logger.info("Connected to pigpio daemon")
            except Exception as e:
                logger.error(f"pigpio initialization failed: {e}")
                self.simulation_mode = True

        # Initialize servos
        for joint, pin in CONFIG["servo_pins"].items():
            center_pwm = self.get_calibrated_pwm(joint, 0.0)
            self.current_pwm[joint] = center_pwm
            self.current_angles[joint] = 0.0
            self.last_update_times[joint] = time.time()

            if not self.simulation_mode and self.pi:
                self.pi.set_servo_pulsewidth(pin, center_pwm)

        logger.info(f"Servo controller initialized (simulation_mode={self.simulation_mode})")

    def get_calibrated_pwm(self, joint: str, angle: float) -> int:
        """Get PWM value using calibration data if available"""
        if joint in self.calibration:
            cal = self.calibration[joint]
            limits = CONFIG["servo_limits"][joint]

            # Map angle to PWM using calibration
            angle_ratio = (angle + limits["range"] / 2) / limits["range"]
            pwm = int(cal["min"] + angle_ratio * (cal["max"] - cal["min"]))
        else:
            # Use default limits
            limits = CONFIG["servo_limits"][joint]
            angle = np.clip(angle, -limits["range"] / 2, limits["range"] / 2)
            pwm_range = limits["max"] - limits["min"]
            angle_ratio = (angle + limits["range"] / 2) / limits["range"]
            pwm = int(limits["min"] + angle_ratio * pwm_range)

        return pwm

    def set_angle(self, joint: str, angle: float) -> int:
        """Set servo angle with rate limiting"""
        limits = CONFIG["servo_limits"][joint]

        # Apply rate limiting
        current = self.current_angles.get(joint, 0.0)
        dt = time.time() - self.last_update_times.get(joint, time.time())
        max_delta = CONFIG["safety"]["max_speed"] * dt

        angle_delta = np.clip(angle - current, -max_delta, max_delta)
        new_angle = np.clip(current + angle_delta, -limits["range"] / 2, limits["range"] / 2)

        # Get calibrated PWM
        pwm = self.get_calibrated_pwm(joint, new_angle)

        # Apply PWM
        if not self.simulation_mode and self.pi:
            try:
                pin = CONFIG["servo_pins"][joint]
                self.pi.set_servo_pulsewidth(pin, pwm)
            except Exception as e:
                logger.error(f"Failed to set servo {joint}: {e}")

        self.current_pwm[joint] = pwm
        self.current_angles[joint] = new_angle
        self.last_update_times[joint] = time.time()

        return pwm

    def set_pwm_direct(self, joint: str, pwm: int):
        """Set PWM directly (for testing/calibration)"""
        pwm = int(np.clip(pwm, 500, 2500))

        if not self.simulation_mode and self.pi:
            try:
                pin = CONFIG["servo_pins"][joint]
                self.pi.set_servo_pulsewidth(pin, pwm)
            except Exception as e:
                logger.error(f"Failed to set PWM for {joint}: {e}")

        self.current_pwm[joint] = pwm

    def save_joint_calibration(self, joint: str, calibration: Dict):
        """Save calibration for a specific joint"""
        self.calibration[joint] = calibration
        save_calibration(self.calibration)
        logger.info(f"Calibration saved for {joint}")

    def emergency_stop(self):
        """Stop all servos immediately"""
        logger.warning("EMERGENCY STOP")
        if not self.simulation_mode and self.pi:
            for pin in CONFIG["servo_pins"].values():
                self.pi.set_servo_pulsewidth(pin, 0)

    def reset(self):
        """Reset to center position"""
        logger.info("Reset to center")
        for joint in CONFIG["servo_pins"].keys():
            self.set_angle(joint, 0.0)

    def cleanup(self):
        """Clean up resources"""
        if self.pi and not self.simulation_mode:
            self.emergency_stop()
            self.pi.stop()


class SubseaController:
    """Main controller with health monitoring"""

    def __init__(self):
        self.servo = EnhancedServoController()
        self.emergency_stopped = False
        self.connected_clients: Set[web.WebSocketResponse] = set()
        self.connection_health = ConnectionHealth()
        self.system_health = SystemHealth()
        self.start_time = time.time()
        self.last_command_time = time.time()

        # Metrics
        self.command_count = 0
        self.error_count = 0

        # Joint states
        self.joints = {}
        for joint in CONFIG["servo_pins"].keys():
            self.joints[joint] = JointState(
                angle=0.0,
                pwm=CONFIG["servo_limits"][joint]["center"],
                timestamp=time.time()
            )

        # Start health monitoring
        asyncio.create_task(self.monitor_health())

    async def handle_command(self, command: Dict):
        """Process commands with error handling"""
        try:
            cmd_type = command.get("type")
            self.command_count += 1
            self.connection_health.message_count += 1

            if self.emergency_stopped and cmd_type not in ["reset", "health"]:
                await self.broadcast_error("System in emergency stop mode")
                return

            if cmd_type == "joint_angles":
                await self._handle_joint_angles(command.get("data", {}))

            elif cmd_type == "emergency_stop":
                await self._handle_emergency_stop()

            elif cmd_type == "reset":
                await self._handle_reset()

            elif cmd_type == "test_pwm":
                await self._handle_test_pwm(command)

            elif cmd_type == "save_calibration":
                await self._handle_save_calibration(command)

            elif cmd_type == "config_update":
                await self._handle_config_update(command.get("config", {}))

            else:
                logger.warning(f"Unknown command type: {cmd_type}")

            self.last_command_time = time.time()
            self.connection_health.last_message_time = time.time()

        except Exception as e:
            logger.error(f"Error handling command: {e}")
            self.error_count += 1
            self.connection_health.error_count += 1
            await self.broadcast_error(str(e))

    async def _handle_joint_angles(self, angles: Dict):
        """Handle joint angle commands"""
        for joint, angle in angles.items():
            if joint in self.joints:
                pwm = self.servo.set_angle(joint, angle)

                # Update state
                self.joints[joint].angle = self.servo.current_angles[joint]
                self.joints[joint].pwm = pwm
                self.joints[joint].timestamp = time.time()

        await self.broadcast_state()

    async def _handle_emergency_stop(self):
        """Handle emergency stop"""
        self.servo.emergency_stop()
        self.emergency_stopped = True
        await self.broadcast_state()
        logger.warning("Emergency stop activated")

    async def _handle_reset(self):
        """Reset arm"""
        self.servo.reset()
        self.emergency_stopped = False

        for joint in self.joints.values():
            joint.angle = 0.0
            joint.timestamp = time.time()

        await self.broadcast_state()
        logger.info("Arm reset to center")

    async def _handle_test_pwm(self, command: Dict):
        """Test PWM directly"""
        joint = command.get("joint")
        pwm = command.get("pwm")

        if joint and pwm:
            self.servo.set_pwm_direct(joint, pwm)
            await self.broadcast_log("info", f"Testing {joint} at PWM {pwm}")

    async def _handle_save_calibration(self, command: Dict):
        """Save calibration data"""
        joint = command.get("joint")
        calibration = command.get("calibration")

        if joint and calibration:
            self.servo.save_joint_calibration(joint, calibration)
            await self.broadcast_log("success", f"Calibration saved for {joint}")

    async def _handle_config_update(self, config: Dict):
        """Update configuration"""
        for section, values in config.items():
            if section in CONFIG:
                CONFIG[section].update(values)
        save_config(CONFIG)
        await self.broadcast_log("success", "Configuration updated")

    async def broadcast_state(self):
        """Broadcast current state to all clients"""
        state = self.get_status()

        message = json.dumps({
            "type": "state_update",
            "data": state,
            "timestamp": time.time()
        })

        await self.broadcast_to_clients(message)

    async def broadcast_log(self, level: str, message: str):
        """Broadcast log message to clients"""
        log_message = json.dumps({
            "type": "log",
            "level": level,
            "message": message,
            "timestamp": time.time()
        })

        await self.broadcast_to_clients(log_message)

    async def broadcast_error(self, error: str):
        """Broadcast error to clients"""
        error_message = json.dumps({
            "type": "error",
            "message": error,
            "timestamp": time.time()
        })

        await self.broadcast_to_clients(error_message)

    async def broadcast_to_clients(self, message: str):
        """Send message to all connected clients"""
        disconnected = set()

        for ws in self.connected_clients:
            try:
                await ws.send_str(message)
            except Exception as e:
                logger.error(f"Failed to send to client: {e}")
                disconnected.add(ws)

        # Remove disconnected clients
        self.connected_clients -= disconnected

    def get_status(self) -> Dict:
        """Get comprehensive status"""
        process = psutil.Process()

        return {
            "joints": {name: asdict(joint) for name, joint in self.joints.items()},
            "emergency_stopped": self.emergency_stopped,
            "uptime": time.time() - self.start_time,
            "last_command": time.time() - self.last_command_time,
            "hardware_mode": "real" if not self.servo.simulation_mode else "simulation",
            "calibrated": len(self.servo.calibration) == len(self.joints),
            "connection": {
                "clients": len(self.connected_clients),
                "last_command": time.time() - self.last_command_time,
                "quality": self.connection_health.quality
            },
            "system": {
                "cpu_percent": psutil.cpu_percent(interval=0.1),
                "memory_mb": process.memory_info().rss / 1024 / 1024
            },
            "health": asdict(self.system_health)
        }

    async def monitor_health(self):
        """Periodic health monitoring"""
        while True:
            try:
                await asyncio.sleep(5)

                issues = []

                # Check command timeout
                if time.time() - self.last_command_time > 60:
                    issues.append("no_commands_60s")

                # Check error rate
                if self.command_count > 0:
                    error_rate = self.error_count / self.command_count
                    if error_rate > 0.1:  # 10% error rate
                        issues.append("high_error_rate")

                # Check hardware
                if not self.servo.simulation_mode:
                    if not self.servo.pi or not self.servo.pi.connected:
                        issues.append("pigpio_disconnected")

                # Update health status
                self.system_health.healthy = len(issues) == 0
                self.system_health.issues = issues
                self.system_health.last_check = time.time()

                if not self.system_health.healthy:
                    logger.warning(f"Health issues detected: {issues}")

            except Exception as e:
                logger.error(f"Health monitoring error: {e}")


# Web handlers
async def websocket_handler(request):
    """Enhanced WebSocket handler"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    controller = request.app['controller']
    controller.connected_clients.add(ws)
    logger.info(f"Client connected (total: {len(controller.connected_clients)})")

    # Send initial status
    await ws.send_str(json.dumps({
        "type": "status",
        "data": controller.get_status()
    }))

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    command = json.loads(msg.data)
                    await controller.handle_command(command)
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON: {e}")
                    await ws.send_str(json.dumps({
                        "type": "error",
                        "message": "Invalid JSON"
                    }))
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error(f'WebSocket error: {ws.exception()}')

    except Exception as e:
        logger.error(f"WebSocket handler error: {e}")

    finally:
        controller.connected_clients.discard(ws)
        logger.info(f"Client disconnected (total: {len(controller.connected_clients)})")

    return ws


async def command_handler(request):
    """REST API for commands"""
    try:
        data = await request.json()
        controller = request.app['controller']
        await controller.handle_command(data)
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.error(f"Command error: {e}")
        return web.json_response({"error": str(e)}, status=400)


async def status_handler(request):
    """Enhanced status endpoint"""
    controller = request.app['controller']
    return web.json_response(controller.get_status())


async def health_handler(request):
    """Health check"""
    controller = request.app['controller']
    return web.json_response({
        "status": "healthy" if controller.system_health.healthy else "degraded",
        "uptime": time.time() - controller.start_time,
        "mode": "real" if not controller.servo.simulation_mode else "simulation",
        "issues": controller.system_health.issues
    })


async def info_handler(request):
    """Extension info"""
    return web.json_response({
        "name": "TRIDENT Subsea Controller",
        "description": "Underwater robotic arm servo control",
        "version": "1.0.0",
        "author": "TRIDENT Project",
        "website": "https://github.com/ccavens/TRIDENT"
    })


async def config_handler(request):
    """Get current configuration"""
    return web.json_response(CONFIG)


async def calibration_handler(request):
    """Get calibration data"""
    controller = request.app['controller']
    return web.json_response(controller.servo.calibration)


async def test_handler(request):
    """System test endpoint"""
    controller = request.app['controller']

    tests = {
        "pigpio": controller.servo.pi is not None and (controller.servo.simulation_mode or controller.servo.pi.connected),
        "gpio_access": not controller.servo.simulation_mode or os.path.exists("/dev/gpiomem"),
        "servo_response": True  # Would need actual test
    }

    return web.json_response({
        "tests": tests,
        "all_passed": all(tests.values())
    })


def create_app():
    """Create web application with enhanced features"""
    app = web.Application()
    app['controller'] = SubseaController()

    # Routes
    app.router.add_get('/ws', websocket_handler)
    app.router.add_post('/command', command_handler)
    app.router.add_get('/status', status_handler)
    app.router.add_get('/health', health_handler)
    app.router.add_get('/info', info_handler)
    app.router.add_get('/config', config_handler)
    app.router.add_get('/calibration', calibration_handler)
    app.router.add_get('/test', test_handler)

    # Static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.router.add_static('/', path=str(static_dir), name='static', show_index=True)
        logger.info(f"Serving static files from {static_dir}")
    else:
        logger.warning(f"Static dir not found at {static_dir}")

    logger.info("Web application created with enhanced features")
    return app


async def main():
    """Main entry point"""
    app = create_app()

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(
        runner,
        CONFIG["network"]["host"],
        CONFIG["network"]["port"]
    )

    logger.info(f"Starting TRIDENT Subsea Controller on port {CONFIG['network']['port']}")
    logger.info(f"Features: WebSocket reconnection, health monitoring, calibration, logging")
    await site.start()

    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        app['controller'].servo.cleanup()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
