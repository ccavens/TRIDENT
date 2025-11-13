#!/usr/bin/env python3
"""
TRIDENT BlueOS Subsea Extension - Minimal Servo Controller
Optimized for low resource usage on Raspberry Pi / BlueOS
All heavy processing happens on the Mac relay
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, asdict
from typing import Dict, Optional

import aiohttp
from aiohttp import web
import numpy as np

# Hardware control imports
try:
    import pigpio
    HARDWARE_AVAILABLE = True
except ImportError:
    HARDWARE_AVAILABLE = False
    logging.warning("pigpio not available - running in simulation mode")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Minimal configuration - optimized for performance
CONFIG = {
    "servo_pins": {
        "joint1_yaw": 12,      # GPIO12 - Joint 1 (Yaw)
        "joint2_pitch": 13,    # GPIO13 - Joint 2 (Pitch)
        "joint3_pitch": 14,    # GPIO14 - Joint 3 (Pitch)
        "joint4_roll": 15      # GPIO15 - Joint 4 (Roll)
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
        "max_speed": 180,  # degrees/second
        "update_rate": 50  # Hz
    }
}


@dataclass
class JointState:
    """Minimal joint state"""
    angle: float
    pwm: int
    timestamp: float


class MinimalServoController:
    """Lightweight servo controller - minimal CPU overhead"""

    def __init__(self):
        self.simulation_mode = not HARDWARE_AVAILABLE
        self.pi = None
        self.current_pwm = {}
        self.current_angles = {}

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

        # Initialize servos to center position
        for joint, pin in CONFIG["servo_pins"].items():
            center_pwm = CONFIG["servo_limits"][joint]["center"]
            self.current_pwm[joint] = center_pwm
            self.current_angles[joint] = 0.0
            if not self.simulation_mode:
                self.pi.set_servo_pulsewidth(pin, center_pwm)

        logger.info(f"Servo controller initialized (simulation_mode={self.simulation_mode})")

    def set_angle(self, joint: str, angle: float) -> int:
        """Set servo angle with minimal processing"""
        limits = CONFIG["servo_limits"][joint]

        # Clamp angle
        angle = np.clip(angle, -limits["range"] / 2, limits["range"] / 2)

        # Calculate PWM (optimized)
        pwm_range = limits["max"] - limits["min"]
        angle_ratio = (angle + limits["range"] / 2) / limits["range"]
        pwm = int(limits["min"] + angle_ratio * pwm_range)

        # Apply PWM
        if not self.simulation_mode and self.pi:
            pin = CONFIG["servo_pins"][joint]
            self.pi.set_servo_pulsewidth(pin, pwm)

        self.current_pwm[joint] = pwm
        self.current_angles[joint] = angle
        return pwm

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
    """Minimal subsea controller for BlueOS"""

    def __init__(self):
        self.servo = MinimalServoController()
        self.emergency_stopped = False
        self.connected_clients = set()
        self.last_command_time = time.time()
        self.start_time = time.time()

        # Joint states for status reporting
        self.joints = {}
        for joint in CONFIG["servo_pins"].keys():
            self.joints[joint] = JointState(
                angle=0.0,
                pwm=CONFIG["servo_limits"][joint]["center"],
                timestamp=time.time()
            )

    async def handle_command(self, command: Dict):
        """Process commands from relay - optimized for speed"""
        cmd_type = command.get("type")

        if self.emergency_stopped and cmd_type != "reset":
            return

        if cmd_type == "joint_angles":
            angles = command.get("data", {})
            for joint, angle in angles.items():
                if joint in self.joints:
                    # Apply rate limiting for safety
                    current = self.joints[joint].angle
                    max_delta = CONFIG["safety"]["max_speed"] / CONFIG["safety"]["update_rate"]
                    angle_clamped = current + np.clip(angle - current, -max_delta, max_delta)

                    # Set servo
                    pwm = self.servo.set_angle(joint, angle_clamped)

                    # Update state
                    self.joints[joint].angle = angle_clamped
                    self.joints[joint].pwm = pwm
                    self.joints[joint].timestamp = time.time()

            self.last_command_time = time.time()

        elif cmd_type == "emergency_stop":
            self.servo.emergency_stop()
            self.emergency_stopped = True

        elif cmd_type == "reset":
            self.servo.reset()
            self.emergency_stopped = False
            for joint in self.joints.values():
                joint.angle = 0.0
                joint.timestamp = time.time()

        elif cmd_type == "gripper":
            # TODO: Implement gripper control
            logger.info(f"Gripper command: {command.get('open', False)}")

    def get_status(self) -> Dict:
        """Get current status"""
        return {
            "joints": {name: asdict(joint) for name, joint in self.joints.items()},
            "emergency_stopped": self.emergency_stopped,
            "uptime": time.time() - self.start_time,
            "last_command": time.time() - self.last_command_time,
            "hardware_mode": "real" if not self.servo.simulation_mode else "simulation"
        }


# Web handlers
async def websocket_handler(request):
    """WebSocket endpoint for relay connection"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    controller = request.app['controller']
    controller.connected_clients.add(ws)
    logger.info(f"Client connected (total: {len(controller.connected_clients)})")

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    command = json.loads(msg.data)
                    await controller.handle_command(command)
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON: {e}")
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error(f'WebSocket error: {ws.exception()}')
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
    """Status endpoint"""
    controller = request.app['controller']
    return web.json_response(controller.get_status())


async def health_handler(request):
    """Health check for BlueOS"""
    controller = request.app['controller']
    return web.json_response({
        "status": "healthy",
        "uptime": time.time() - controller.start_time,
        "mode": "real" if not controller.servo.simulation_mode else "simulation"
    })


async def info_handler(request):
    """BlueOS extension info"""
    return web.json_response({
        "name": "TRIDENT Subsea Controller",
        "description": "Underwater robotic arm servo control",
        "version": "1.0.0",
        "author": "TRIDENT Project",
        "website": "https://github.com/ccavens/TRIDENT"
    })


def create_app():
    """Create minimal web application"""
    app = web.Application()
    app['controller'] = SubseaController()

    # Add routes
    app.router.add_get('/ws', websocket_handler)
    app.router.add_post('/command', command_handler)
    app.router.add_get('/status', status_handler)
    app.router.add_get('/health', health_handler)
    app.router.add_get('/info', info_handler)

    logger.info("Web application created")
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
    await site.start()

    try:
        # Run forever
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        app['controller'].servo.cleanup()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
