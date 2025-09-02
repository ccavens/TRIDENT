#!/usr/bin/env python3
"""
BlueOS Extension for Subsea Arm Control
Receives joint angle commands and controls HSR-M9382TH servos via PWM
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
import numpy as np

# Network and API imports
import aiohttp
from aiohttp import web
import uvloop

# Hardware control imports
try:
    import pigpio

    HARDWARE_AVAILABLE = True
except ImportError:
    HARDWARE_AVAILABLE = False
    logging.warning("pigpio not available - running in simulation mode")

# MAVLink imports for BlueOS integration
try:
    from pymavlink import mavutil

    MAVLINK_AVAILABLE = True
except ImportError:
    MAVLINK_AVAILABLE = False
    logging.warning("pymavlink not available - MAVLink features disabled")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
CONFIG = {
    "servo_pins": {
        "joint1_yaw": 12,  # GPIO12 - Joint 1 (Yaw)
        "joint2_pitch": 13,  # GPIO13 - Joint 2 (Pitch)
        "joint3_pitch": 14,  # GPIO14 - Joint 3 (Pitch)
        "joint4_roll": 15  # GPIO15 - Joint 4 (Roll)
    },
    "servo_limits": {
        "joint1_yaw": {"min": 500, "max": 2500, "center": 1500, "range": 180},
        "joint2_pitch": {"min": 500, "max": 2500, "center": 1500, "range": 180},
        "joint3_pitch": {"min": 500, "max": 2500, "center": 1500, "range": 180},
        "joint4_roll": {"min": 500, "max": 2500, "center": 1500, "range": 360}
    },
    "network": {
        "port": 8080,
        "host": "0.0.0.0",
        "heartbeat_interval": 1.0,
        "command_timeout": 5.0
    },
    "safety": {
        "max_speed": 180,  # degrees/second
        "emergency_stop": False,
        "soft_limits_enabled": True
    },
    "mavlink": {
        "connection": "udpout:0.0.0.0:14550",
        "system_id": 255,
        "component_id": 1
    }
}


@dataclass
class JointState:
    """Represents the state of a single joint"""
    angle: float  # Current angle in degrees
    target: float  # Target angle in degrees
    velocity: float  # Current velocity in degrees/second
    pwm: int  # Current PWM value
    timestamp: float  # Last update timestamp


@dataclass
class ArmState:
    """Complete arm state"""
    joints: Dict[str, JointState]
    gripper_open: bool
    emergency_stop: bool
    connection_status: str
    last_command_time: float
    telemetry: Dict


class ServoController:
    """Handles PWM control for the servos"""

    def __init__(self, simulation_mode=False):
        self.simulation_mode = simulation_mode or not HARDWARE_AVAILABLE
        self.pi = None
        self.current_pwm = {}

        if not self.simulation_mode:
            self.pi = pigpio.pi()
            if not self.pi.connected:
                logger.error("Failed to connect to pigpio daemon")
                self.simulation_mode = True

        # Initialize all servos to center position
        for joint, pin in CONFIG["servo_pins"].items():
            center_pwm = CONFIG["servo_limits"][joint]["center"]
            self.current_pwm[joint] = center_pwm
            if not self.simulation_mode:
                self.pi.set_servo_pulsewidth(pin, center_pwm)

    def set_angle(self, joint: str, angle: float) -> int:
        """Set servo angle and return PWM value"""
        limits = CONFIG["servo_limits"][joint]

        # Clamp angle to valid range
        angle = np.clip(angle, -limits["range"] / 2, limits["range"] / 2)

        # Convert angle to PWM
        pwm_range = limits["max"] - limits["min"]
        angle_ratio = (angle + limits["range"] / 2) / limits["range"]
        pwm = int(limits["min"] + angle_ratio * pwm_range)

        # Apply PWM
        if not self.simulation_mode:
            pin = CONFIG["servo_pins"][joint]
            self.pi.set_servo_pulsewidth(pin, pwm)

        self.current_pwm[joint] = pwm
        return pwm

    def emergency_stop(self):
        """Stop all servos immediately"""
        if not self.simulation_mode:
            for pin in CONFIG["servo_pins"].values():
                self.pi.set_servo_pulsewidth(pin, 0)

    def cleanup(self):
        """Clean up GPIO resources"""
        if self.pi and not self.simulation_mode:
            self.emergency_stop()
            self.pi.stop()


class SubseaArmController:
    """Main controller for the subsea arm"""

    def __init__(self):
        self._boot_ms0 = int(time.monotonic() * 1000) & 0xFFFFFFFF
        self.servo_controller = ServoController()
        self.arm_state = self._initialize_arm_state()
        self.mavlink_conn = None
        self.last_heartbeat = time.time()
        self.command_queue = asyncio.Queue()
        self.web_clients = set()

        # Initialize MAVLink connection if available
        if MAVLINK_AVAILABLE:
            try:
                self.mavlink_conn = mavutil.mavlink_connection(
                    CONFIG["mavlink"]["connection"],
                    source_system=CONFIG["mavlink"]["system_id"],
                    source_component=CONFIG["mavlink"]["component_id"]
                )
                logger.info("MAVLink connection established")
            except Exception as e:
                logger.error(f"Failed to establish MAVLink connection: {e}")

    def _initialize_arm_state(self) -> ArmState:
        """Initialize arm state with default values"""
        joints = {}
        for joint in CONFIG["servo_pins"].keys():
            joints[joint] = JointState(
                angle=0.0,
                target=0.0,
                velocity=0.0,
                pwm=CONFIG["servo_limits"][joint]["center"],
                timestamp=time.time()
            )

        return ArmState(
            joints=joints,
            gripper_open=False,
            emergency_stop=False,
            connection_status="disconnected",
            last_command_time=time.time(),
            telemetry={}
        )

    def _time_boot_ms(self) -> int:
        """MAVLink time_boot_ms: ms since boot, uint32"""
        return (int(time.monotonic() * 1000) - self._boot_ms0) & 0xFFFFFFFF

    async def process_command(self, command: Dict):
        """Process incoming command"""
        cmd_type = command.get("type")

        if cmd_type == "joint_angles":
            await self._handle_joint_angles(command.get("data", {}))
        elif cmd_type == "emergency_stop":
            await self._handle_emergency_stop()
        elif cmd_type == "reset":
            await self._handle_reset()
        elif cmd_type == "gripper":
            await self._handle_gripper(command.get("open", False))
        elif cmd_type == "config_update":
            await self._handle_config_update(command.get("config", {}))
        else:
            logger.warning(f"Unknown command type: {cmd_type}")

    async def _handle_joint_angles(self, angles: Dict):
        """Handle joint angle commands"""
        if self.arm_state.emergency_stop:
            logger.warning("Cannot move - emergency stop active")
            return

        for joint, angle in angles.items():
            if joint in self.arm_state.joints:
                # Apply rate limiting
                current = self.arm_state.joints[joint].angle
                max_delta = CONFIG["safety"]["max_speed"] / 50  # Assuming 50Hz update
                angle_delta = np.clip(angle - current, -max_delta, max_delta)
                new_angle = current + angle_delta

                # Set servo position
                pwm = self.servo_controller.set_angle(joint, new_angle)

                # Update state
                self.arm_state.joints[joint].angle = new_angle
                self.arm_state.joints[joint].target = angle
                self.arm_state.joints[joint].pwm = pwm
                self.arm_state.joints[joint].velocity = angle_delta * 50
                self.arm_state.joints[joint].timestamp = time.time()

        self.arm_state.last_command_time = time.time()
        await self._broadcast_state()

    async def _handle_emergency_stop(self):
        """Handle emergency stop command"""
        logger.warning("EMERGENCY STOP ACTIVATED")
        self.servo_controller.emergency_stop()
        self.arm_state.emergency_stop = True
        await self._broadcast_state()

    async def _handle_reset(self):
        """Reset arm to home position"""
        logger.info("Resetting arm to home position")
        self.arm_state.emergency_stop = False

        for joint in self.arm_state.joints:
            self.servo_controller.set_angle(joint, 0)
            self.arm_state.joints[joint].angle = 0
            self.arm_state.joints[joint].target = 0
            self.arm_state.joints[joint].velocity = 0

        await self._broadcast_state()

    async def _handle_gripper(self, open_gripper: bool):
        """Handle gripper control"""
        self.arm_state.gripper_open = open_gripper
        # TODO: Implement pneumatic gripper control
        logger.info(f"Gripper {'opened' if open_gripper else 'closed'}")
        await self._broadcast_state()

    async def _handle_config_update(self, config: Dict):
        """Update configuration dynamically"""
        for section, values in config.items():
            if section in CONFIG:
                CONFIG[section].update(values)
        logger.info("Configuration updated")

    async def _broadcast_state(self):
        """Broadcast current state to all connected clients"""
        state_dict = {
            "joints": {
                name: asdict(joint)
                for name, joint in self.arm_state.joints.items()
            },
            "gripper_open": self.arm_state.gripper_open,
            "emergency_stop": self.arm_state.emergency_stop,
            "connection_status": self.arm_state.connection_status,
            "last_command_time": self.arm_state.last_command_time,
            "telemetry": self.arm_state.telemetry
        }

        message = json.dumps({
            "type": "state_update",
            "data": state_dict,
            "timestamp": time.time()
        })

        # Send to WebSocket clients
        for ws in list(self.web_clients):
            try:
                await ws.send_str(message)
            except ConnectionResetError:
                self.web_clients.discard(ws)

    async def send_mavlink_telemetry(self):
        """Send telemetry via MAVLink"""
        if not self.mavlink_conn:
            return

        # Send custom telemetry message
        # This would typically use NAMED_VALUE_FLOAT messages
        for joint_name, joint_state in self.arm_state.joints.items():
            try:
                self.mavlink_conn.mav.named_value_float_send(
                    self._time_boot_ms(),
                    joint_name.encode('utf-8')[:10],  # name (max 10 chars)
                    joint_state.angle  # value
                )
            except Exception as e:
                logger.error(f"Failed to send MAVLink telemetry: {e}")

    async def control_loop(self):
        """Main control loop"""
        while True:
            try:
                # Process command queue
                while not self.command_queue.empty():
                    command = await self.command_queue.get()
                    await self.process_command(command)

                # Check for timeout
                if time.time() - self.arm_state.last_command_time > CONFIG["network"]["command_timeout"]:
                    if self.arm_state.connection_status != "timeout":
                        self.arm_state.connection_status = "timeout"
                        logger.warning("Command timeout - entering safe mode")

                # Send MAVLink telemetry
                if time.time() - self.last_heartbeat > CONFIG["network"]["heartbeat_interval"]:
                    await self.send_mavlink_telemetry()
                    self.last_heartbeat = time.time()

                await asyncio.sleep(0.02)  # 50Hz update rate

            except Exception as e:
                logger.error(f"Error in control loop: {e}")
                await asyncio.sleep(0.1)


# Web API handlers
async def websocket_handler(request):
    """Handle WebSocket connections"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    controller = request.app['controller']
    controller.web_clients.add(ws)
    controller.arm_state.connection_status = "connected"

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    command = json.loads(msg.data)
                    await controller.command_queue.put(command)
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON: {msg.data}")
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error(f'WebSocket error: {ws.exception()}')
    finally:
        controller.web_clients.discard(ws)
        if not controller.web_clients:
            controller.arm_state.connection_status = "disconnected"

    return ws


async def command_handler(request):
    """Handle REST API commands"""
    try:
        data = await request.json()
        controller = request.app['controller']
        await controller.command_queue.put(data)
        return web.json_response({"status": "accepted"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)


async def status_handler(request):
    """Return current arm status"""
    controller = request.app['controller']
    state_dict = {
        "joints": {
            name: asdict(joint)
            for name, joint in controller.arm_state.joints.items()
        },
        "gripper_open": controller.arm_state.gripper_open,
        "emergency_stop": controller.arm_state.emergency_stop,
        "connection_status": controller.arm_state.connection_status,
        "telemetry": controller.arm_state.telemetry
    }
    return web.json_response(state_dict)


async def register_service_handler(request):
    """BlueOS service registration endpoint"""
    return web.json_response({
        "name": "Subsea Arm Controller",
        "description": "Joint replication controller for underwater robotic arm",
        "icon": "mdi-robot-industrial",
        "company": "Custom",
        "version": "1.0.0",
        "webpage": "http://blueos.local/extension/subsea-arm/",
        "api": "http://blueos.local:8080/docs"
    })


async def health_handler(request):
    """Health check endpoint"""
    controller = request.app['controller']
    return web.json_response({
        "status": "healthy",
        "uptime": time.time() - controller.last_heartbeat,
        "hardware_mode": "real" if HARDWARE_AVAILABLE else "simulation",
        "mavlink_connected": controller.mavlink_conn is not None
    })


def create_app():
    """Create the web application"""
    app = web.Application()
    controller = SubseaArmController()
    app['controller'] = controller

    # Add routes
    app.router.add_get('/ws', websocket_handler)
    app.router.add_post('/command', command_handler)
    app.router.add_get('/status', status_handler)
    app.router.add_get('/register_service', register_service_handler)
    app.router.add_get('/health', health_handler)

    # Add static file serving for web interface
    # app.router.add_static('/', path='static', name='static', show_index=True) -- throws error in simulation mode
    static_dir = (Path(__file__).parent / "static").resolve()
    if static_dir.exists():
        app.router.add_static('/web/', path=str(static_dir), name='static', show_index=True)
    else:
        logger.warning(f"Static dir not found at {static_dir}; skipping static route")

    return app, controller


async def main():
    """Main entry point"""
    # Set up event loop with uvloop for better performance
    if sys.platform != 'win32':
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

    app, controller = create_app()

    # Start control loop
    asyncio.create_task(controller.control_loop())

    # Start web server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(
        runner,
        CONFIG["network"]["host"],
        CONFIG["network"]["port"]
    )

    logger.info(f"Starting Subsea Arm Controller on {CONFIG['network']['host']}:{CONFIG['network']['port']}")
    await site.start()

    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        controller.servo_controller.cleanup()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())