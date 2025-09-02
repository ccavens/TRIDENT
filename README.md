
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

```javascript
Topside RPi (sensor reader)  <-->  Mac Relay/Web Server  <-->  Subsea RPi (servo controller)
```

## Repository Structure
```javascript TRIDENT/
├── raspberrypi/
│   ├── subsea/
│   │   └── subseamain.py   # Servo driver + BlueOS extension
│   └── topside/
│       └── topsidemain.py  # Sensor reader & relay client
├── relay/
│   ├── relay.py            # Mac relay & web server
│   └── static/
│       └── index.html      # Browser dashboard
├── README.md              
├── LICENSE                 # GPL v3
└── .gitignore
```
## Installation

TODO
    
## Running the System
TODO
## API & Communication
TODO
## Simulation Mode
Both topside and subsea scripts gracefully degrade when required hardware modules are missing:

- Subsea
    - Logs intended PWM signals instead of actuating servos.

- Topside
    - Generates synthetic sensor values to emulate joint motion.

The relay also supports a simulation.enabled flag that injects artificial latency and noise for testing.
## Notes
None.
## License

[GNU General Public License v3.0](https://choosealicense.com/licenses/gpl-3.0/)

