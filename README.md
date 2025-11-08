# Record Player Setup Guide

This explains how to set up the **Record Player** web interface — a lightweight control layer designed to integrate a record player into a multi-room audio setup using AirPlay speakers. In this setup, **Owntone** acts as the central audio server, receiving its input from a pipe that captures audio from an interface connected to the record player.

## Step 1 – Install Required Packages

Before you begin, install the required packages using **apt**:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip owntone alsa-utils
```

These packages provide:

- **python3 / venv / pip** — to run the FastAPI web interface
- **owntone** — the AirPlay/DAAP server
- **alsa-utils** — provides `arecord` for capturing audio from your USB interface

## Step 2 – Set up the Python environment

Create and activate a virtual environment, then install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
```

## Step 3 – Configure Environment Variables (`web.env`)

The web interface and its systemd service rely on a simple environment file located at:

```
/etc/record-player/web.env
```

This file defines runtime paths and settings for the **Record Player Web** service.

Create the file with the following contents (adjust paths to your installation):

```ini
APP_ROOT=/path-to-app

# The Python interpreter to use (venv)
VENV_PY=/path/.venv/bin/python3

# Uvicorn bind
HOST=0.0.0.0
PORT=8080

# Owntone endpoint
OWNTONE_ENDPOINT=http://127.0.0.1:3689/api

# Directory to store persistent default output configuration
RECORD_PLAYER_DATA_DIR=/path-to-data-dir
```

## Step 4 – Select the Correct Audio Interface (ALSA)

The **Owntone pipe service** uses the ALSA capture device `hw:CARD=CODEC,DEV=0` by default.
However, your audio interface (for example, a USB-connected record player preamp) may have a different name or index depending on your system configuration.

If the wrong device is selected, the service will start but record **silence** or fail with an ALSA error.

To find the correct device run:

```bash
arecord -l
```
This lists all *capture-capable* (recording) devices.

For example

```
**** List of CAPTURE Hardware Devices ****
card 2: Scarlett2i2 [Focusrite Scarlett 2i2], device 0: USB Audio
  Subdevices: 1/1
  Subdevice #0: subdevice #0
```

This means your device string is `hw:CARD=Scarlett2i2,DEV=0`

Edit the file:

```
systemd/owntone-record_player-input.service
```

Find this line:

```
ExecStart=/bin/bash -lc '/usr/bin/arecord -D hw:CARD=CODEC,DEV=0 -f cd | /usr/bin/tee /srv/music/record_player.pcm > /dev/null'
```

and replace it with:

```
ExecStart=/bin/bash -lc '/usr/bin/arecord -D hw:CARD=Scarlett2i2,DEV=0 -f cd | /usr/bin/tee /srv/music/record_player.pcm > /dev/null'
```

## Step 5 – Deploy the systemd services

Deploy all services permanently using the provided script:

```bash
chmod +x tools/deploy-systemd.sh
./tools/deploy-systemd.sh
```

The script will:

- Copy the unit files from `systemd/` into `/etc/systemd/system/`
- Reload systemd
- Enable and restart **record-player-web.service**
- Disable Owntone’s auto-start on boot (you control it from the web UI)

## Using the Web Interface

Open:
```
http://<your-ip>:8080
```
Once you’ve opened the web interface in your browser, you’ll see a dashboard with a **toggle switch** and (once active) a list of available outputs.

### Start and Stop
- Use the **main toggle switch** at the top to start or stop Owntone.
- Starting Owntone will also start the **pipe service**, which captures the analog audio from your record player through the audio interface and sends it to Owntone.
- Stopping Owntone automatically stops the pipe service as well.

### Outputs
- When both Owntone and the pipe service are running, the **Outputs** section becomes visible.
- Here you’ll see all available playback devices that Owntone has detected (e.g., AirPlay speakers, Chromecast devices, etc.).
- Each listed output has its own on/off **switch**.
- Each output also includes an **individual volume slider**.

### Persistent Default Outputs
The web interface allows you to mark certain outputs as **default**.
When a default output is enabled, its **current volume** (as shown by the slider) is stored.
These settings are saved to disk in a small JSON file at:

```
data/default_outputs.json
```

and are **persistent across restarts** of both the web interface and the system itself.

### When No Outputs Are Shown
The web app automatically hides outputs while the system is starting up or stopped.
If **Owntone** and the **pipe service** are fully running (both indicators on the page are green), run these commands to check for errors:
```
systemctl status owntone-record_player-input.service
systemctl status owntone.service
```
### Uncompressed Audio
By default **Owntone** uses compressed ALAC for AirPlay. To switch to uncompressed ALAC edit `/etc/owntone.conf` and change the line
```
#	uncompressed_alac = false
```
to
```
uncompressed_alac = true
```

## Homekit

The Homekit systemd service relies on an environment file located at:

```
/etc/record-player/homekit.env
```
Create the file with the following contents:

```ini
# Path to your Record Player app repo
APP_ROOT=/path-to-app

# Python interpreter from your virtual environment
VENV_PY=/path/.venv/bin/python3

# Where the backend (web service) API lives
BACKEND_ENDPOINT=http://127.0.0.1:8080/api

# IP address for HomeKit accessory to bind on your LAN
HAP_ADDRESS=host-ip-address

# Directory for HomeKit pairing data
HOMEKIT_DATA_DIR=/path-to-data-folder
```

Deploy all services including the Homekit service permanently:

```bash
chmod +x tools/deploy-systemd.sh
./tools/deploy-systemd.sh --with-homekit
```
