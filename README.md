# Pi NetClicker

A wireless, battery-powered button device that sends real mouse click events to a Windows PC over a network. Press a physical button on the handheld device — the PC receives a left mouse down/up event instantly.

Communication runs over [Tailscale](https://tailscale.com), so it works across any network without port forwarding.

---

## How It Works

```
[Physical Button] → [Pi Zero 2 W] → Tailscale VPN → [Windows PC] → Mouse Click
```

- `button_client.py` runs on the Pi. It monitors the button via GPIO interrupt and sends `DOWN`/`UP` events over TCP to the Windows machine.
- `windows.py` runs on Windows. It receives those events and simulates real left mouse button presses using `pynput`.
- `ap_server.py` runs on the Pi and creates a Wi-Fi hotspot that lets you add or change network credentials from any phone or laptop. It's triggered on demand by a double-press of the PiSugar2's built-in button — no SSH or keyboard required.

---

## Parts List

| Part | Link |
|------|------|
| Raspberry Pi Zero 2 W | [Amazon](https://www.amazon.com/dp/B09LH5SBPS) |
| PiSugar2 (battery board) | [Amazon](https://www.amazon.com/dp/B08D678XPR) |
| 2200mAh extended battery | [Amazon](https://www.amazon.com/dp/B0CTQ6VBB6) |
| Momentary push button | [Amazon](https://www.amazon.com/dp/B09V2L86DR) |
| 3D printed case | `case.stl` + `lid.stl` (in this repo) |

---

## 3D Printed Case

Four STL files are included:

| File | Qty | Description |
|------|-----|-------------|
| `case.stl` | 1 | Main enclosure body |
| `lid.stl` | 2 | Top and bottom lids — print twice and glue to seal |
| `button.stl` | 1 | Button cap that sits over the momentary push button |
| `switch.stl` | 1 | Switch actuator/cover |

After assembling the electronics inside, glue both lids in place to seal the enclosure.

> **Note:** The 3D printed case is the weakest part of this build. It can crack under hard drops. The electronics inside are fine — reprint and reassemble if needed.

Any FDM printer works. No specific settings required.

---

## Hardware Assembly

### Wiring the Button

The button connects directly to the Pi's GPIO header — no resistors needed (internal pull-up is enabled in firmware).

| Button leg | Pi Zero 2 W pin |
|------------|-----------------|
| Leg 1 | GPIO 17 (physical pin 11) |
| Leg 2 | GND (physical pin 9) |

### Attaching the PiSugar2

The PiSugar2 attaches to the back of the Pi Zero 2 W via spring-loaded pogo pins — no soldering required. Align the board, press it on, and secure it with the included screws.

### Installing the 2200mAh Battery

The PiSugar2 ships with a smaller default battery. To use the 2200mAh extended battery:

1. Carefully disconnect the default battery's JST connector from the PiSugar2 board.
2. Connect the 2200mAh battery's JST connector in its place.
3. After booting (see software setup), open the PiSugar2 web interface at `http://<pi-ip>:8421`.
4. Navigate to **Settings** and set the battery capacity to **2200 mAh** so the percentage display is accurate.

### Configuring the PiSugar2 Button (Wi-Fi Provisioning)

The PiSugar2 has its own physical button. Configure a double-press to trigger `ap_server.py` so you can change Wi-Fi credentials at any time without SSH:

1. Open the PiSugar2 web interface at `http://<pi-ip>:8421`
2. Go to **Settings → Button**
3. Set the **Double Tap** action to **Custom** and enter:
   ```
   sudo python3 /home/<your-username>/ap_server.py
   ```
4. Save the setting

From that point on, a double-press of the PiSugar2 button will bring up the `NetClicker` hotspot so you can reconfigure Wi-Fi without touching a terminal.

---

## Software Setup

### 1. Flash the Pi

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to flash **Raspberry Pi OS Lite (64-bit)** to a microSD card.

In the Imager's advanced settings (gear icon), configure:
- Hostname (e.g. `netclicker`)
- Enable SSH
- Set a username/password
- Optionally pre-configure your Wi-Fi here (skips the `ap_server.py` step)

### 2. Wi-Fi Setup via ap_server.py

`ap_server.py` creates a temporary Wi-Fi hotspot called **`NetClicker`** so you can add or change network credentials from any phone or laptop. It's used for both initial setup and changing networks later.

First, install NetworkManager on the Pi:

```bash
sudo apt update && sudo apt install -y network-manager python3
sudo systemctl stop wpa_supplicant
sudo systemctl disable wpa_supplicant
```

Copy `ap_server.py` to the Pi, then run it:

```bash
sudo python3 ap_server.py
```

Or, after completing the PiSugar2 button setup below, simply **double-press the PiSugar2 button** to start it automatically.

**To connect and configure:**

1. On your phone or laptop, connect to Wi-Fi network **`NetClicker`** (password: `netclicker`)
2. Open a browser and go to `http://10.42.0.1:8080`
3. Select your network and enter the password
4. Click **Add Connection** — the Pi saves the credentials and reboots

### 3. Install Tailscale on the Pi

SSH into the Pi after it connects to your network:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Follow the authentication link printed in the terminal. Once authenticated, note the Pi's Tailscale IP:

```bash
tailscale ip -4
```

### 4. Install Tailscale on Windows

Download and install Tailscale from [tailscale.com/download](https://tailscale.com/download). Sign in with the same account used on the Pi. Both devices should appear in your Tailscale admin panel.

### 5. Configure the Client

On the Pi, edit `button_client.py` and update `WINDOWS_IP` with your Windows machine's Tailscale IP address:

```python
WINDOWS_IP = '100.x.x.x'  # your Windows Tailscale IP
```

Find your Windows Tailscale IP in the Tailscale system tray icon or at [login.tailscale.com](https://login.tailscale.com).

### 6. Install Pi Dependencies

```bash
pip3 install RPi.GPIO
```

> `RPi.GPIO` may already be installed on Raspberry Pi OS. If the script runs without error, you're good.

### 7. Auto-Start on Boot (systemd service)

Create a service file so `button_client.py` starts automatically when the Pi powers on:

```bash
sudo nano /etc/systemd/system/netclicker.service
```

Paste the following (adjust the path if you placed the script elsewhere):

```ini
[Unit]
Description=NetClicker Button Client
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 /home/<your-username>/button_client.py
Restart=always
RestartSec=5
User=<your-username>

[Install]
WantedBy=multi-user.target
```

Enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable netclicker
sudo systemctl start netclicker
```

Check it's running:

```bash
sudo systemctl status netclicker
```

From this point on, powering on the device will automatically connect to the Windows server.

---

## Windows Setup

### Install Dependencies

```bash
pip install pynput sv-ttk
pip install py-window-styles  # optional: dark title bar on Windows 11
```

### Run the Server

```bash
python windows.py
```

The GUI will appear and the server starts automatically on port `5000`. When the Pi connects, the status indicator turns green. Press the physical button — you'll see the click events in the log (enable "Log click events" checkbox to see each DOWN/UP).

### Features

- **F6 → Hold F2:** When the checkbox is enabled, pressing F6 on the keyboard toggles a simulated continuous F2 keypress. Tap F6 again to stop.
- **Log click events:** Toggle logging of individual DOWN/UP events. Off by default to keep the log clean at high click rates.

---

## File Reference

| File | Runs on | Purpose |
|------|---------|---------|
| `button_client.py` | Raspberry Pi | Reads button, sends events over TCP |
| `windows.py` | Windows PC | Receives events, simulates mouse clicks |
| `ap_server.py` | Raspberry Pi | One-time Wi-Fi provisioning hotspot |
| `case.stl` | — | 3D printable enclosure body |
| `lid.stl` | — | 3D printable lid (print twice) |
| `button.stl` | — | 3D printable button cap |
| `switch.stl` | — | 3D printable switch actuator |
