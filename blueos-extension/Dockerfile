# BlueOS TRIDENT Subsea Controller Extension
# Minimal servo control for underwater robotic arm

FROM python:3.11-slim-bookworm

# Install system dependencies
RUN apt-get update && apt-get install -y \
    pigpio \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app/ ./app/
COPY start.sh .
RUN chmod +x start.sh

# Expose web interface port
EXPOSE 9091

# Health check endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:9091/health', timeout=5)"

# BlueOS Extension Metadata Labels
LABEL version="1.0.0"
LABEL permissions='\
{\
    "ExposedPorts": {\
        "9091/tcp": {}\
    },\
    "HostConfig": {\
        "Privileged": true,\
        "NetworkMode": "host",\
        "Binds": [\
            "/dev:/dev:rw"\
        ]\
    }\
}'
LABEL authors='[\
    {\
        "name": "TRIDENT Project",\
        "email": "trident@example.com"\
    }\
]'
LABEL company='{\
    "about": "Open-source underwater robotic arm control system",\
    "name": "TRIDENT",\
    "email": "trident@example.com"\
}'
LABEL readme='https://raw.githubusercontent.com/{tag}/README.md'
LABEL links='{\
    "website": "https://github.com/ccavens/TRIDENT",\
    "support": "https://github.com/ccavens/TRIDENT/issues"\
}'
LABEL type="device-integration"
LABEL tags='[\
    "robotics",\
    "manipulator",\
    "rov",\
    "servo-control",\
    "underwater"\
]'
LABEL requirements="core >= 1.1"

# Start pigpio daemon and application
CMD ["./start.sh"]
