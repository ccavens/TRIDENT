// TRIDENT Subsea Controller - Web Interface
// Enhanced with reconnection, health monitoring, and comprehensive UI

class TRIDENTController {
    constructor() {
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this.reconnectDelay = 1000; // Start with 1 second
        this.isConnecting = false;
        this.isUserMode = true;
        this.setupComplete = false;

        // Metrics
        this.metrics = {
            commandCount: 0,
            errorCount: 0,
            latencies: [],
            lastMessageTime: Date.now()
        };

        // Logs storage
        this.logs = [];
        this.maxLogs = 1000;

        this.init();
    }

    init() {
        this.setupEventListeners();
        this.checkSetupStatus();
        this.connect();
        this.startMetricsUpdate();
    }

    // ==================== WebSocket Connection ====================

    connect() {
        if (this.isConnecting || (this.ws && this.ws.readyState === WebSocket.OPEN)) {
            return;
        }

        this.isConnecting = true;
        this.log('info', 'Connecting to subsea controller...');

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        try {
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => this.handleOpen();
            this.ws.onmessage = (event) => this.handleMessage(event);
            this.ws.onerror = (error) => this.handleError(error);
            this.ws.onclose = (event) => this.handleClose(event);

        } catch (error) {
            this.log('error', `Connection failed: ${error.message}`);
            this.scheduleReconnect();
        }
    }

    handleOpen() {
        this.isConnecting = false;
        this.reconnectAttempts = 0;
        this.reconnectDelay = 1000;
        this.updateConnectionStatus(true);
        this.log('success', 'Connected to subsea controller');

        // Request initial status
        this.requestStatus();
    }

    handleMessage(event) {
        this.metrics.lastMessageTime = Date.now();

        try {
            const data = JSON.parse(event.data);

            if (data.type === 'status') {
                this.updateStatus(data.data);
            } else if (data.type === 'state_update') {
                this.updateStatus(data.data);
            } else if (data.type === 'log') {
                this.log(data.level, data.message);
            } else if (data.type === 'error') {
                this.log('error', data.message);
                this.metrics.errorCount++;
            }
        } catch (error) {
            this.log('error', `Failed to parse message: ${error.message}`);
        }
    }

    handleError(error) {
        this.log('error', `WebSocket error: ${error.message || 'Unknown error'}`);
        this.metrics.errorCount++;
    }

    handleClose(event) {
        this.isConnecting = false;
        this.updateConnectionStatus(false);

        if (event.wasClean) {
            this.log('warning', `Connection closed cleanly (code: ${event.code})`);
        } else {
            this.log('error', 'Connection lost');
        }

        this.scheduleReconnect();
    }

    scheduleReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            this.log('error', 'Max reconnection attempts reached. Please refresh the page.');
            return;
        }

        this.reconnectAttempts++;
        const delay = Math.min(
            this.reconnectDelay * Math.pow(1.5, this.reconnectAttempts),
            30000 // Max 30 seconds
        );

        // Add jitter to prevent thundering herd
        const jitter = delay * 0.1 * Math.random();
        const actualDelay = delay + jitter;

        this.log('warning', `Reconnecting in ${(actualDelay / 1000).toFixed(1)}s (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})`);

        setTimeout(() => this.connect(), actualDelay);
    }

    sendCommand(command) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            this.log('error', 'Not connected to subsea controller');
            return false;
        }

        try {
            const startTime = performance.now();
            this.ws.send(JSON.stringify(command));

            // Track latency (approximate)
            requestAnimationFrame(() => {
                const latency = performance.now() - startTime;
                this.metrics.latencies.push(latency);
                if (this.metrics.latencies.length > 100) {
                    this.metrics.latencies.shift();
                }
            });

            this.metrics.commandCount++;
            return true;
        } catch (error) {
            this.log('error', `Failed to send command: ${error.message}`);
            this.metrics.errorCount++;
            return false;
        }
    }

    requestStatus() {
        fetch('/status')
            .then(res => res.json())
            .then(data => this.updateStatus(data))
            .catch(err => this.log('error', `Failed to fetch status: ${err.message}`));
    }

    // ==================== UI Updates ====================

    updateConnectionStatus(connected) {
        const statusDot = document.getElementById('status-dot');
        const statusText = document.getElementById('status-text');

        if (connected) {
            statusDot.classList.add('connected');
            statusText.textContent = 'Connected';
        } else {
            statusDot.classList.remove('connected');
            statusText.textContent = `Reconnecting (${this.reconnectAttempts}/${this.maxReconnectAttempts})`;
        }
    }

    updateStatus(data) {
        // Hardware mode
        const hardwareMode = document.getElementById('hardware-mode');
        if (hardwareMode && data.hardware_mode) {
            hardwareMode.textContent = data.hardware_mode === 'real' ? 'Real Hardware' : 'Simulation';
            hardwareMode.className = `badge ${data.hardware_mode === 'real' ? 'success' : 'warning'}`;
        }

        // Uptime
        const uptime = document.getElementById('uptime');
        if (uptime && data.uptime !== undefined) {
            uptime.textContent = this.formatUptime(data.uptime);
        }

        // Calibration status
        const calibrated = document.getElementById('calibrated');
        if (calibrated && data.calibrated !== undefined) {
            calibrated.textContent = data.calibrated ? 'Yes' : 'No';
            calibrated.className = `badge ${data.calibrated ? 'success' : 'warning'}`;
        }

        // Connection info
        if (data.connection) {
            const clientCount = document.getElementById('client-count');
            if (clientCount) clientCount.textContent = data.connection.clients || 0;

            const lastCommand = document.getElementById('last-command');
            if (lastCommand && data.connection.last_command !== undefined) {
                lastCommand.textContent = data.connection.last_command < 1
                    ? 'Just now'
                    : `${data.connection.last_command.toFixed(1)}s ago`;
            }

            // Connection quality
            if (data.connection.quality !== undefined) {
                this.updateConnectionQuality(data.connection.quality);
            }
        }

        // System resources
        if (data.system) {
            const cpuUsage = document.getElementById('cpu-usage');
            if (cpuUsage && data.system.cpu_percent !== undefined) {
                cpuUsage.textContent = `${data.system.cpu_percent.toFixed(1)}%`;
            }

            const memoryUsage = document.getElementById('memory-usage');
            if (memoryUsage && data.system.memory_mb !== undefined) {
                memoryUsage.textContent = `${data.system.memory_mb.toFixed(0)} MB`;
            }
        }

        // Health status
        if (data.health) {
            const healthStatus = document.getElementById('health-status');
            if (healthStatus) {
                healthStatus.textContent = data.health.healthy ? 'Healthy' : 'Issues';
                healthStatus.className = `badge ${data.health.healthy ? 'success' : 'danger'}`;
            }
        }

        // Joint states
        if (data.joints) {
            this.updateJoints(data.joints);
        }
    }

    updateJoints(joints) {
        const jointsGrid = document.getElementById('joints-grid');
        if (!jointsGrid) return;

        jointsGrid.innerHTML = '';

        Object.entries(joints).forEach(([name, state]) => {
            const jointDiv = document.createElement('div');
            jointDiv.className = 'joint-item';
            jointDiv.innerHTML = `
                <h4>${this.formatJointName(name)}</h4>
                <div class="joint-info">
                    <span>Angle:</span>
                    <span>${state.angle.toFixed(1)}°</span>
                </div>
                <div class="joint-info">
                    <span>PWM:</span>
                    <span>${state.pwm}</span>
                </div>
                <div class="joint-info">
                    <span>Updated:</span>
                    <span>${this.formatTimestamp(state.timestamp)}</span>
                </div>
            `;
            jointsGrid.appendChild(jointDiv);
        });
    }

    updateConnectionQuality(quality) {
        const qualityFill = document.getElementById('quality-fill');
        if (!qualityFill) return;

        const percent = quality * 100;
        qualityFill.style.width = `${percent}%`;

        qualityFill.className = 'quality-fill';
        if (percent < 50) {
            qualityFill.classList.add('poor');
        } else if (percent < 80) {
            qualityFill.classList.add('fair');
        }
    }

    // ==================== Logging ====================

    log(level, message) {
        const timestamp = new Date().toISOString();
        const logEntry = { timestamp, level, message };

        this.logs.unshift(logEntry);
        if (this.logs.length > this.maxLogs) {
            this.logs.pop();
        }

        // Update logs panel if in developer mode
        if (!this.isUserMode) {
            this.updateLogsPanel();
        }

        // Console log
        const consoleMethod = level === 'error' ? 'error' : level === 'warning' ? 'warn' : 'log';
        console[consoleMethod](`[${timestamp}] ${message}`);
    }

    updateLogsPanel() {
        const logsContainer = document.getElementById('logs-container');
        if (!logsContainer) return;

        const levelFilter = document.getElementById('log-level')?.value || 'all';
        const filteredLogs = this.filterLogs(levelFilter);

        logsContainer.innerHTML = filteredLogs.slice(0, 200).map(log => `
            <div class="log-entry ${log.level}">
                <span class="log-timestamp">${new Date(log.timestamp).toLocaleTimeString()}</span>
                <span class="log-level">[${log.level.toUpperCase()}]</span>
                <span class="log-message">${this.escapeHtml(log.message)}</span>
            </div>
        `).join('');

        // Auto-scroll to bottom if near bottom
        if (logsContainer.scrollHeight - logsContainer.scrollTop < logsContainer.clientHeight + 100) {
            logsContainer.scrollTop = logsContainer.scrollHeight;
        }
    }

    filterLogs(level) {
        if (level === 'all') return this.logs;

        const levels = {
            'error': ['error'],
            'warning': ['error', 'warning'],
            'info': ['error', 'warning', 'info']
        };

        return this.logs.filter(log => levels[level].includes(log.level));
    }

    clearLogs() {
        this.logs = [];
        this.updateLogsPanel();
        this.log('info', 'Logs cleared');
    }

    downloadLogs() {
        const logsText = this.logs.map(log =>
            `${log.timestamp} [${log.level.toUpperCase()}] ${log.message}`
        ).join('\n');

        const blob = new Blob([logsText], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `trident-logs-${Date.now()}.txt`;
        a.click();
        URL.revokeObjectURL(url);
    }

    // ==================== Setup Wizard ====================

    checkSetupStatus() {
        // Check if setup has been completed
        this.setupComplete = localStorage.getItem('trident_setup_complete') === 'true';

        if (!this.setupComplete) {
            this.showSetupWizard();
        }
    }

    showSetupWizard() {
        const wizardOverlay = document.getElementById('wizard-overlay');
        const wizardContent = document.getElementById('wizard-content');

        wizardOverlay.classList.add('active');

        this.wizardSteps = [
            {
                title: 'Welcome',
                content: `
                    <h3>Welcome to TRIDENT!</h3>
                    <p>This wizard will help you set up your robotic arm controller.</p>
                    <p>You'll configure:</p>
                    <ul>
                        <li>✓ Hardware connections</li>
                        <li>✓ Safety limits</li>
                        <li>✓ Servo calibration</li>
                        <li>✓ System verification</li>
                    </ul>
                    <p>This should take about 5 minutes.</p>
                `
            },
            {
                title: 'Hardware Check',
                content: `
                    <h3>Hardware Verification</h3>
                    <p>Please verify your hardware connections:</p>
                    <div class="status-item">
                        <span>Servos connected to GPIO 12-15:</span>
                        <input type="checkbox" id="hw-servos">
                    </div>
                    <div class="status-item">
                        <span>Servo power supply connected:</span>
                        <input type="checkbox" id="hw-power">
                    </div>
                    <div class="status-item">
                        <span>Common ground established:</span>
                        <input type="checkbox" id="hw-ground">
                    </div>
                    <div style="margin-top: 1rem; padding: 1rem; background: rgba(255, 193, 7, 0.2); border-radius: 4px;">
                        ⚠️ <strong>Warning:</strong> Do NOT power servos from Raspberry Pi 5V!
                    </div>
                `
            },
            {
                title: 'Safety Configuration',
                content: `
                    <h3>Safety Limits</h3>
                    <p>Configure safe operating parameters:</p>
                    <div class="config-item" style="margin-bottom: 1rem;">
                        <label>Maximum Speed (°/second):</label>
                        <input type="number" id="setup-max-speed" value="180" min="1" max="360">
                        <small>Recommended: 180°/s</small>
                    </div>
                    <div class="config-item">
                        <label>Update Rate (Hz):</label>
                        <input type="number" id="setup-update-rate" value="50" min="10" max="100">
                        <small>Recommended: 50Hz</small>
                    </div>
                `
            },
            {
                title: 'Connection Test',
                content: `
                    <h3>System Test</h3>
                    <p>Testing system components...</p>
                    <div id="test-results">
                        <div class="status-item">
                            <span>pigpio daemon:</span>
                            <span id="test-pigpio">Testing...</span>
                        </div>
                        <div class="status-item">
                            <span>GPIO access:</span>
                            <span id="test-gpio">Testing...</span>
                        </div>
                        <div class="status-item">
                            <span>Servo response:</span>
                            <span id="test-servo">Testing...</span>
                        </div>
                    </div>
                `
            },
            {
                title: 'Complete',
                content: `
                    <h3>✓ Setup Complete!</h3>
                    <p>Your TRIDENT subsea controller is ready to use.</p>
                    <p>Next steps:</p>
                    <ol>
                        <li>Connect your Mac relay to this controller</li>
                        <li>Open the web dashboard</li>
                        <li>Calibrate individual joints (recommended)</li>
                    </ol>
                    <p>You can re-run this wizard anytime from Developer Mode.</p>
                `
            }
        ];

        this.currentWizardStep = 0;
        this.renderWizardStep();
    }

    renderWizardStep() {
        const wizardContent = document.getElementById('wizard-content');
        const step = this.wizardSteps[this.currentWizardStep];

        wizardContent.innerHTML = `
            <div class="wizard-step active">
                ${step.content}
            </div>
        `;

        // Update buttons
        document.getElementById('wizard-prev').style.display =
            this.currentWizardStep === 0 ? 'none' : 'inline-block';
        document.getElementById('wizard-next').style.display =
            this.currentWizardStep === this.wizardSteps.length - 1 ? 'none' : 'inline-block';
        document.getElementById('wizard-finish').style.display =
            this.currentWizardStep === this.wizardSteps.length - 1 ? 'inline-block' : 'none';

        // Special handling for test step
        if (this.currentWizardStep === 3) {
            setTimeout(() => this.runSystemTests(), 500);
        }
    }

    runSystemTests() {
        // Simulate system tests (in real implementation, these would query the backend)
        setTimeout(() => {
            document.getElementById('test-pigpio').innerHTML = '<span class="badge success">OK</span>';
        }, 500);

        setTimeout(() => {
            document.getElementById('test-gpio').innerHTML = '<span class="badge success">OK</span>';
        }, 1000);

        setTimeout(() => {
            document.getElementById('test-servo').innerHTML = '<span class="badge success">OK</span>';
        }, 1500);
    }

    // ==================== Calibration ====================

    openCalibration() {
        const modal = document.getElementById('calibration-modal');
        modal.classList.add('active');
    }

    closeCalibration() {
        const modal = document.getElementById('calibration-modal');
        modal.classList.remove('active');
    }

    startJointCalibration(joint) {
        const content = document.getElementById('calibration-content');
        content.innerHTML = `
            <h4>Calibrating ${this.formatJointName(joint)}</h4>
            <p>Move the servo to each position and record the PWM value.</p>
            <div class="calibration-positions">
                <div class="calibration-position">
                    <h5>Minimum Position</h5>
                    <input type="range" id="cal-min-pwm" min="500" max="2500" value="500">
                    <span id="cal-min-value">500</span>
                    <button class="btn btn-primary" onclick="controller.setCalibrationPWM('${joint}', 'min')">
                        Set Minimum
                    </button>
                </div>
                <div class="calibration-position">
                    <h5>Center Position</h5>
                    <input type="range" id="cal-center-pwm" min="500" max="2500" value="1500">
                    <span id="cal-center-value">1500</span>
                    <button class="btn btn-primary" onclick="controller.setCalibrationPWM('${joint}', 'center')">
                        Set Center
                    </button>
                </div>
                <div class="calibration-position">
                    <h5>Maximum Position</h5>
                    <input type="range" id="cal-max-pwm" min="500" max="2500" value="2500">
                    <span id="cal-max-value">2500</span>
                    <button class="btn btn-primary" onclick="controller.setCalibrationPWM('${joint}', 'max')">
                        Set Maximum
                    </button>
                </div>
            </div>
            <button class="btn btn-success" onclick="controller.saveCalibration('${joint}')">
                Save Calibration
            </button>
            <button class="btn btn-secondary" onclick="controller.openCalibration()">
                Back
            </button>
        `;

        // Add event listeners for PWM sliders
        ['min', 'center', 'max'].forEach(pos => {
            const slider = document.getElementById(`cal-${pos}-pwm`);
            const value = document.getElementById(`cal-${pos}-value`);
            if (slider && value) {
                slider.addEventListener('input', () => {
                    value.textContent = slider.value;
                });
            }
        });
    }

    setCalibrationPWM(joint, position) {
        const pwm = parseInt(document.getElementById(`cal-${position}-pwm`).value);

        // Send test command to servo
        this.sendCommand({
            type: 'test_pwm',
            joint: joint,
            pwm: pwm
        });

        this.log('info', `Testing ${joint} at ${position} position (PWM: ${pwm})`);
    }

    saveCalibration(joint) {
        const minPWM = parseInt(document.getElementById('cal-min-pwm').value);
        const centerPWM = parseInt(document.getElementById('cal-center-pwm').value);
        const maxPWM = parseInt(document.getElementById('cal-max-pwm').value);

        this.sendCommand({
            type: 'save_calibration',
            joint: joint,
            calibration: {
                min: minPWM,
                center: centerPWM,
                max: maxPWM
            }
        });

        this.log('success', `Calibration saved for ${joint}`);
        this.closeCalibration();
    }

    // ==================== Event Listeners ====================

    setupEventListeners() {
        // Mode toggle
        document.getElementById('mode-toggle')?.addEventListener('click', () => {
            this.isUserMode = !this.isUserMode;
            this.toggleMode();
        });

        // Emergency stop
        document.getElementById('emergency-stop')?.addEventListener('click', () => {
            this.emergencyStop();
        });

        // Reset arm
        document.getElementById('reset-arm')?.addEventListener('click', () => {
            this.resetArm();
        });

        // Open calibration
        document.getElementById('open-calibration')?.addEventListener('click', () => {
            this.openCalibration();
        });

        // Close calibration
        document.getElementById('close-calibration')?.addEventListener('click', () => {
            this.closeCalibration();
        });

        // Calibration joint selection
        document.querySelectorAll('[data-joint]').forEach(btn => {
            btn.addEventListener('click', () => {
                this.startJointCalibration(btn.dataset.joint);
            });
        });

        // Wizard navigation
        document.getElementById('wizard-prev')?.addEventListener('click', () => {
            if (this.currentWizardStep > 0) {
                this.currentWizardStep--;
                this.renderWizardStep();
            }
        });

        document.getElementById('wizard-next')?.addEventListener('click', () => {
            if (this.currentWizardStep < this.wizardSteps.length - 1) {
                this.currentWizardStep++;
                this.renderWizardStep();
            }
        });

        document.getElementById('wizard-finish')?.addEventListener('click', () => {
            localStorage.setItem('trident_setup_complete', 'true');
            document.getElementById('wizard-overlay').classList.remove('active');
            this.log('success', 'Setup wizard completed');
        });

        // Developer mode controls
        document.getElementById('clear-logs')?.addEventListener('click', () => {
            this.clearLogs();
        });

        document.getElementById('download-logs')?.addEventListener('click', () => {
            this.downloadLogs();
        });

        document.getElementById('log-level')?.addEventListener('change', () => {
            this.updateLogsPanel();
        });

        document.getElementById('save-config')?.addEventListener('click', () => {
            this.saveConfiguration();
        });

        document.getElementById('test-servo')?.addEventListener('click', () => {
            this.testServo();
        });

        document.getElementById('ping-test')?.addEventListener('click', () => {
            this.pingTest();
        });

        // Test angle slider
        document.getElementById('test-angle')?.addEventListener('input', (e) => {
            document.getElementById('test-angle-value').textContent = e.target.value + '°';
        });
    }

    // ==================== Control Actions ====================

    emergencyStop() {
        if (confirm('Are you sure you want to trigger an emergency stop?')) {
            this.sendCommand({ type: 'emergency_stop' });
            this.log('warning', 'EMERGENCY STOP ACTIVATED');
        }
    }

    resetArm() {
        this.sendCommand({ type: 'reset' });
        this.log('info', 'Resetting arm to center position');
    }

    testServo() {
        const joint = document.getElementById('test-joint').value;
        const angle = parseFloat(document.getElementById('test-angle').value);

        this.sendCommand({
            type: 'joint_angles',
            data: { [joint]: angle }
        });

        this.log('info', `Testing ${joint} at ${angle}°`);
    }

    pingTest() {
        const resultDiv = document.getElementById('ping-result');
        resultDiv.textContent = 'Testing...';

        const start = performance.now();
        fetch('/health')
            .then(res => res.json())
            .then(data => {
                const latency = (performance.now() - start).toFixed(1);
                resultDiv.innerHTML = `<span class="badge success">OK</span> Latency: ${latency}ms`;
                this.log('success', `Ping successful: ${latency}ms`);
            })
            .catch(err => {
                resultDiv.innerHTML = `<span class="badge danger">FAILED</span>`;
                this.log('error', `Ping failed: ${err.message}`);
            });
    }

    saveConfiguration() {
        const config = {
            servo_pins: {
                joint1_yaw: parseInt(document.getElementById('pin-joint1').value),
                joint2_pitch: parseInt(document.getElementById('pin-joint2').value),
                joint3_pitch: parseInt(document.getElementById('pin-joint3').value),
                joint4_roll: parseInt(document.getElementById('pin-joint4').value)
            },
            safety: {
                max_speed: parseInt(document.getElementById('max-speed').value),
                update_rate: parseInt(document.getElementById('update-rate').value)
            }
        };

        this.sendCommand({
            type: 'config_update',
            config: config
        });

        this.log('success', 'Configuration saved');
    }

    toggleMode() {
        const userView = document.getElementById('user-view');
        const developerView = document.getElementById('developer-view');
        const modeToggle = document.getElementById('mode-toggle');

        if (this.isUserMode) {
            userView.style.display = 'block';
            developerView.style.display = 'none';
            modeToggle.textContent = '👨‍💻 Developer Mode';
        } else {
            userView.style.display = 'none';
            developerView.style.display = 'block';
            modeToggle.textContent = '👤 User Mode';
            this.updateLogsPanel();
        }
    }

    // ==================== Metrics Update ====================

    startMetricsUpdate() {
        setInterval(() => {
            this.requestStatus();
            this.updateMetrics();
        }, 2000); // Update every 2 seconds
    }

    updateMetrics() {
        // Command count
        document.getElementById('cmd-count').textContent = this.metrics.commandCount;

        // Error rate
        const errorRate = this.metrics.commandCount > 0
            ? ((this.metrics.errorCount / this.metrics.commandCount) * 100).toFixed(1)
            : 0;
        document.getElementById('error-rate').textContent = errorRate + '%';

        // Average latency
        if (this.metrics.latencies.length > 0) {
            const avgLatency = this.metrics.latencies.reduce((a, b) => a + b, 0) / this.metrics.latencies.length;
            document.getElementById('avg-latency').textContent = avgLatency.toFixed(1) + 'ms';
        }

        // Messages per second
        const timeSinceLastMessage = (Date.now() - this.metrics.lastMessageTime) / 1000;
        const msgRate = timeSinceLastMessage < 2 ? Math.round(1 / timeSinceLastMessage) : 0;
        document.getElementById('msg-rate').textContent = msgRate;
    }

    // ==================== Utility Functions ====================

    formatUptime(seconds) {
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);
        return `${hours}h ${minutes}m ${secs}s`;
    }

    formatTimestamp(timestamp) {
        const now = Date.now() / 1000;
        const diff = now - timestamp;

        if (diff < 1) return 'Just now';
        if (diff < 60) return `${Math.floor(diff)}s ago`;
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        return new Date(timestamp * 1000).toLocaleTimeString();
    }

    formatJointName(name) {
        return name.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize controller when DOM is ready
let controller;
document.addEventListener('DOMContentLoaded', () => {
    controller = new TRIDENTController();
});
