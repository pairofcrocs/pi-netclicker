#!/usr/bin/env python3
import os
import sys
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs
import threading
import json
import time
import shlex

# ====== Config ======
HOTSPOT_SSID = "Pi-Setup"
HOTSPOT_PASS = "setup1234"   # >= 8 chars
HOTSPOT_CON_NAME = "PiSetupHotspot"
WIFI_CON_NAME_PREFIX = "WiFi-"
WIFI_IFACE = "wlan0"

# Priority hints (higher wins). Keep hotspot very low just in case.
NEW_WIFI_PRIORITY = 10
HOTSPOT_PRIORITY = -999

# ====== Helpers ======
def run(cmd, check=True, capture=True):
    if isinstance(cmd, str):
        cmd_list = shlex.split(cmd)
    else:
        cmd_list = cmd
    result = subprocess.run(
        cmd_list,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\nOutput:\n{result.stdout}")
    return result.stdout.strip() if capture else ""

def nm_available():
    try:
        run("nmcli -v")
        return True
    except Exception:
        return False

def ensure_root():
    if os.geteuid() != 0:
        print("Please run as root: sudo python3 pi_wifi_setup.py")
        sys.exit(1)

def get_ip_for_iface(iface):
    try:
        out = run(f"ip -4 addr show {iface}")
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                return line.split()[1].split("/")[0]
    except Exception:
        pass
    return None

def delete_connection_if_exists(name):
    try:
        conns = run("nmcli -t -f NAME connection show")
        for c in conns.splitlines():
            if c.strip() == name:
                run(f"nmcli connection delete {shlex.quote(name)}")
                break
    except Exception:
        pass

# ====== Hotspot control (never autoconnect) ======
def start_hotspot():
    """Create/bring up a deterministic NM hotspot on WIFI_IFACE; autoconnect is disabled."""
    # Clean any auto "Hotspot" profile & our named one
    delete_connection_if_exists("Hotspot")
    delete_connection_if_exists(HOTSPOT_CON_NAME)

    # Create AP profile with AUTOCONNECT DISABLED
    run(
        f"nmcli connection add type wifi ifname {WIFI_IFACE} con-name {HOTSPOT_CON_NAME} "
        f"ssid {HOTSPOT_SSID}"
    )
    run(f"nmcli connection modify {HOTSPOT_CON_NAME} connection.autoconnect no")
    run(f"nmcli connection modify {HOTSPOT_CON_NAME} connection.autoconnect-priority {HOTSPOT_PRIORITY}")

    # AP settings
    run(f"nmcli connection modify {HOTSPOT_CON_NAME} 802-11-wireless.mode ap")
    run(f"nmcli connection modify {HOTSPOT_CON_NAME} 802-11-wireless.band bg")
    run(f"nmcli connection modify {HOTSPOT_CON_NAME} 802-11-wireless.channel 1")
    run(f"nmcli connection modify {HOTSPOT_CON_NAME} 802-11-wireless.hidden no")
    run(f"nmcli connection modify {HOTSPOT_CON_NAME} 802-11-wireless.powersave 2")

    # WPA2-PSK
    run(f"nmcli connection modify {HOTSPOT_CON_NAME} wifi-sec.key-mgmt wpa-psk")
    run(f"nmcli connection modify {HOTSPOT_CON_NAME} wifi-sec.psk {HOTSPOT_PASS}")

    # IP sharing for captive-style access
    run(f"nmcli connection modify {HOTSPOT_CON_NAME} ipv4.method shared")
    run(f"nmcli connection modify {HOTSPOT_CON_NAME} ipv6.method ignore")

    # Explicitly bring it up (since autoconnect is OFF)
    run(f"nmcli connection up {HOTSPOT_CON_NAME}")

    # Wait for IP (commonly 10.42.0.1)
    for _ in range(30):
        ip = get_ip_for_iface(WIFI_IFACE)
        if ip:
            return ip
        time.sleep(0.3)
    return get_ip_for_iface(WIFI_IFACE)

def stop_hotspot():
    try:
        run(f"nmcli connection down {HOTSPOT_CON_NAME}", check=False)
        # Keep the profile, but with autoconnect disabled it won't start at boot.
        # If you prefer, delete it instead:
        # delete_connection_if_exists(HOTSPOT_CON_NAME)
    except Exception:
        pass

def scan_ssids():
    try:
        run(f"nmcli dev wifi rescan ifname {WIFI_IFACE}", check=False)
        out = run(f"nmcli -t -f SSID dev wifi list ifname {WIFI_IFACE}")
        ssids = []
        for line in out.splitlines():
            s = line.strip()
            if s and s not in ssids:
                ssids.append(s)
        return [s for s in ssids if s]
    except Exception:
        return []

def get_saved_wifi_connections():
    """Get list of saved Wi-Fi connections with their SSIDs and passwords."""
    try:
        # Get all connection names that match our prefix
        out = run("nmcli -t -f NAME connection show")
        saved = []
        for line in out.splitlines():
            name = line.strip()
            if name.startswith(WIFI_CON_NAME_PREFIX):
                ssid = name[len(WIFI_CON_NAME_PREFIX):]
                # Try to get the password
                try:
                    psk_out = run(f"nmcli -s -g 802-11-wireless-security.psk connection show {shlex.quote(name)}")
                    psk = psk_out.strip() if psk_out else None
                except:
                    psk = None
                saved.append({"name": name, "ssid": ssid, "psk": psk})
        return saved
    except Exception:
        return []

def delete_saved_connection(con_name):
    """Delete a saved Wi-Fi connection."""
    try:
        run(f"nmcli connection delete {shlex.quote(con_name)}")
        return True
    except Exception:
        return False

# ====== Wi-Fi provisioning (no immediate connect) ======
def add_wifi_profile_only(ssid, psk=None):
    con_name = f"{WIFI_CON_NAME_PREFIX}{ssid}"
    delete_connection_if_exists(con_name)

    run(
        f"nmcli connection add type wifi ifname {WIFI_IFACE} "
        f"con-name {shlex.quote(con_name)} ssid {shlex.quote(ssid)}"
    )
    run(f"nmcli connection modify {shlex.quote(con_name)} connection.autoconnect yes")
    run(f"nmcli connection modify {shlex.quote(con_name)} connection.autoconnect-priority  {NEW_WIFI_PRIORITY}")

    if psk:
        run(f"nmcli connection modify {shlex.quote(con_name)} wifi-sec.key-mgmt wpa-psk")
        run(f"nmcli connection modify {shlex.quote(con_name)} wifi-sec.psk {shlex.quote(psk)}")
    else:
        run(f"nmcli connection modify {shlex.quote(con_name)} wifi-sec.key-mgmt none")

    # Do NOT bring it up now

# ====== Web portal ======
class Portal(BaseHTTPRequestHandler):
    def _write(self, code=200, content_type="text/html", body=b""):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stdout.write(("WEB: " + fmt + "\n") % args)

    def do_GET(self):
        if self.path == "/scan":
            ssids = scan_ssids()
            self._write(200, "application/json", json.dumps(ssids).encode())
            return
        
        if self.path == "/saved":
            saved = get_saved_wifi_connections()
            self._write(200, "application/json", json.dumps(saved).encode())
            return
        
        if self.path == "/cancel":
            self._write(200, "text/html; charset=utf-8", """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rebooting</title>
<style>
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: linear-gradient(135deg, #1a1a2e 0%, #0f0f1e 100%);
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  color: #e0e0e0;
}
.card {
  background: rgba(30, 30, 46, 0.95);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 20px;
  padding: 40px;
  text-align: center;
  max-width: 400px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
}
.icon {
  font-size: 64px;
  margin-bottom: 20px;
}
h3 {
  font-size: 24px;
  color: #ffffff;
  margin-bottom: 16px;
}
p {
  color: #a0a0b0;
  line-height: 1.6;
}
</style>
</head>
<body>
<div class="card">
  <div class="icon">🔄</div>
  <h3>Rebooting</h3>
  <p>Your Raspberry Pi is rebooting now...</p>
</div>
</body>
</html>""".encode())
            threading.Thread(target=reboot_only, daemon=True).start()
            return

        ip = get_ip_for_iface(WIFI_IFACE) or "10.42.0.1"
        html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Raspberry Pi Wi-Fi Setup</title>
<style>
* {{
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}}

body {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
  background: linear-gradient(135deg, #1a1a2e 0%, #0f0f1e 100%);
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  color: #e0e0e0;
}}

.container {{
  width: 100%;
  max-width: 480px;
}}

.card {{
  background: rgba(30, 30, 46, 0.95);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 20px;
  padding: 32px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(10px);
}}

.header {{
  text-align: center;
  margin-bottom: 32px;
}}

.icon {{
  width: 64px;
  height: 64px;
  margin: 0 auto 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 32px;
}}

h2 {{
  font-size: 24px;
  font-weight: 600;
  color: #ffffff;
  margin-bottom: 8px;
}}

.subtitle {{
  color: #a0a0b0;
  font-size: 14px;
  line-height: 1.5;
}}

.info-box {{
  background: rgba(102, 126, 234, 0.1);
  border: 1px solid rgba(102, 126, 234, 0.3);
  border-radius: 12px;
  padding: 12px 16px;
  margin-bottom: 24px;
  font-size: 13px;
  color: #c0c0d0;
}}

.info-box code {{
  background: rgba(255, 255, 255, 0.1);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: 'Courier New', monospace;
  color: #a0d0ff;
}}

.saved-networks {{
  margin-bottom: 24px;
}}

.saved-networks h3 {{
  font-size: 16px;
  font-weight: 600;
  color: #d0d0d0;
  margin-bottom: 12px;
}}

.network-list {{
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  overflow: hidden;
}}

.network-item {{
  padding: 12px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  display: flex;
  align-items: center;
  justify-content: space-between;
}}

.network-item:last-child {{
  border-bottom: none;
}}

.network-info {{
  flex: 1;
  min-width: 0;
}}

.network-ssid {{
  font-weight: 500;
  color: #ffffff;
  margin-bottom: 4px;
}}

.network-password {{
  font-size: 12px;
  color: #808090;
  font-family: 'Courier New', monospace;
  word-break: break-all;
}}

.network-password.hidden {{
  color: #606070;
}}

.btn-delete {{
  background: rgba(220, 38, 38, 0.2);
  border: 1px solid rgba(220, 38, 38, 0.3);
  color: #ff6b6b;
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s ease;
  white-space: nowrap;
  margin-left: 12px;
}}

.btn-delete:hover {{
  background: rgba(220, 38, 38, 0.3);
  transform: none;
  box-shadow: none;
}}

.empty-state {{
  padding: 20px;
  text-align: center;
  color: #808090;
  font-size: 14px;
}}

.form-group {{
  margin-bottom: 20px;
}}

label {{
  display: block;
  margin-bottom: 8px;
  font-size: 14px;
  font-weight: 500;
  color: #d0d0d0;
}}

select, input {{
  width: 100%;
  padding: 12px 16px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 10px;
  color: #ffffff;
  font-size: 15px;
  transition: all 0.3s ease;
}}

select:focus, input:focus {{
  outline: none;
  border-color: #667eea;
  background: rgba(255, 255, 255, 0.08);
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}}

select option {{
  background: #1a1a2e;
  color: #ffffff;
}}

input::placeholder {{
  color: #808090;
}}

.button-group {{
  display: flex;
  gap: 12px;
  margin-top: 8px;
}}

button {{
  flex: 1;
  padding: 14px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
  border-radius: 10px;
  color: #ffffff;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s ease;
  box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
}}

button:hover {{
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(102, 126, 234, 0.6);
}}

button:active {{
  transform: translateY(0);
}}

.btn-secondary {{
  background: rgba(255, 255, 255, 0.1);
  box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
}}

.btn-secondary:hover {{
  background: rgba(255, 255, 255, 0.15);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.3);
}}

.footer {{
  margin-top: 20px;
  text-align: center;
  font-size: 12px;
  color: #707080;
}}

.footer a {{
  color: #8090ff;
  text-decoration: none;
}}

.loading {{
  display: none;
  text-align: center;
  margin-top: 16px;
  color: #a0a0b0;
  font-size: 14px;
}}

.loading.active {{
  display: block;
}}

@keyframes spin {{
  to {{ transform: rotate(360deg); }}
}}

.spinner {{
  display: inline-block;
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.2);
  border-top-color: #667eea;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-right: 8px;
  vertical-align: middle;
}}
</style>
</head>
<body>
  <div class="container">
    <div class="card">
      <div class="header">
        <div class="icon">📡</div>
        <h2>Wi-Fi Setup</h2>
        <p class="subtitle">Connect your Raspberry Pi to a wireless network</p>
      </div>

      <div class="saved-networks">
        <h3>Saved Networks</h3>
        <div class="network-list" id="savedList">
          <div class="empty-state">Loading saved networks...</div>
        </div>
      </div>

      <form method="POST" action="/provision" id="wifiForm">
        <div class="form-group">
          <label for="ssid">Select Network</label>
          <select id="ssid" name="ssid">
            <option value="">Loading networks...</option>
          </select>
        </div>

        <div class="form-group">
          <label for="ssid_manual">Or Enter SSID Manually</label>
          <input id="ssid_manual" name="ssid_manual" placeholder="Network name (SSID)">
        </div>

        <div class="form-group">
          <label for="psk">Password</label>
          <input id="psk" name="psk" type="password" autocomplete="off" placeholder="Leave blank for open networks">
        </div>

        <div class="button-group">
          <button type="button" class="btn-secondary" onclick="cancelSetup()">Cancel</button>
          <button type="submit">Add Connection</button>
        </div>
        
        <div class="loading" id="loading">
          <span class="spinner"></span>
          <span>Saving configuration...</span>
        </div>
      </form>
    </div>
  </div>

<script>
// Load available networks
(async function() {{
  const sel = document.getElementById('ssid');
  try {{
    const r = await fetch('/scan');
    const data = await r.json();
    sel.innerHTML = '<option value="">-- Select a network --</option>';
    data.forEach(s => {{
      const o = document.createElement('option');
      o.value = s;
      o.textContent = s;
      sel.appendChild(o);
    }});
  }} catch(e) {{
    sel.innerHTML = '<option value="">Scan failed - enter manually below</option>';
  }}
}})();

// Load saved networks
(async function() {{
  const list = document.getElementById('savedList');
  try {{
    const r = await fetch('/saved');
    const data = await r.json();
    if (data.length === 0) {{
      list.innerHTML = '<div class="empty-state">No saved networks</div>';
    }} else {{
      list.innerHTML = '';
      data.forEach(net => {{
        const item = document.createElement('div');
        item.className = 'network-item';
        const pskDisplay = net.psk ? net.psk : '(open network)';
        item.innerHTML = `
          <div class="network-info">
            <div class="network-ssid">${{escapeHtml(net.ssid)}}</div>
            <div class="network-password">${{escapeHtml(pskDisplay)}}</div>
          </div>
          <button class="btn-delete" onclick="deleteNetwork('${{escapeHtml(net.name)}}')">Delete</button>
        `;
        list.appendChild(item);
      }});
    }}
  }} catch(e) {{
    list.innerHTML = '<div class="empty-state">Failed to load saved networks</div>';
  }}
}})();

function escapeHtml(text) {{
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}}

async function deleteNetwork(conName) {{
  if (!confirm('Delete this saved network?')) return;
  try {{
    const r = await fetch('/delete', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
      body: 'con_name=' + encodeURIComponent(conName)
    }});
    const result = await r.json();
    if (result.success) {{
      location.reload();
    }} else {{
      alert('Failed to delete network');
    }}
  }} catch(e) {{
    alert('Error deleting network');
  }}
}}

document.getElementById('wifiForm').addEventListener('submit', function() {{
  document.getElementById('loading').classList.add('active');
}});

function cancelSetup() {{
  if (confirm('Reboot without saving Wi-Fi configuration?')) {{
    window.location.href = '/cancel';
  }}
}}
</script>
</body>
</html>"""

        self._write(200, "text/html; charset=utf-8", html.encode())

    def do_POST(self):
        if self.path == "/delete":
            length = int(self.headers.get("Content-Length", "0"))
            data = self.rfile.read(length).decode()
            form = parse_qs(data)
            con_name = form.get("con_name", [""])[0].strip()
            if con_name:
                success = delete_saved_connection(con_name)
                self._write(200, "application/json", json.dumps({"success": success}).encode())
            else:
                self._write(400, "application/json", json.dumps({"success": False}).encode())
            return
        
        if self.path != "/provision":
            self._write(404, "text/plain", b"Not Found")
            return
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode()
        form = parse_qs(data)
        ssid = (form.get("ssid_manual", [""])[0] or form.get("ssid", [""])[0]).strip()
        psk = form.get("psk", [""])[0].strip()
        if not ssid:
            self._write(400, "text/plain", b"SSID is required.")
            return
        self._write(200, "text/html; charset=utf-8", f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Setup Complete</title>
<style>
body {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: linear-gradient(135deg, #1a1a2e 0%, #0f0f1e 100%);
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  color: #e0e0e0;
}}
.card {{
  background: rgba(30, 30, 46, 0.95);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 20px;
  padding: 40px;
  text-align: center;
  max-width: 400px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
}}
.icon {{
  font-size: 64px;
  margin-bottom: 20px;
}}
h3 {{
  font-size: 24px;
  color: #ffffff;
  margin-bottom: 16px;
}}
p {{
  color: #a0a0b0;
  line-height: 1.6;
}}
code {{
  background: rgba(102, 126, 234, 0.2);
  padding: 2px 8px;
  border-radius: 4px;
  color: #a0d0ff;
}}
</style>
</head>
<body>
<div class="card">
  <div class="icon">✅</div>
  <h3>Configuration Saved</h3>
  <p>Network <code>{ssid}</code> has been added. Your Pi is rebooting and will connect to the configured network.</p>
</div>
</body>
</html>""".encode())
        threading.Thread(target=provision_and_reboot, args=(ssid, psk), daemon=True).start()

# ====== Provision + reboot ======
def reboot_only():
    try:
        print("Cancel requested - rebooting without saving Wi-Fi...")
        stop_hotspot()
        time.sleep(1)
    except Exception as e:
        print("Error during cleanup:", e)
    finally:
        print("Rebooting…")
        os.sync()
        time.sleep(1)
        os.system("reboot")

def provision_and_reboot(ssid, psk):
    try:
        print(f"Saving SSID='{ssid}' (psk {'set' if psk else 'none'})")
        add_wifi_profile_only(ssid, psk if psk else None)
        # Explicitly bring the setup AP down before rebooting
        stop_hotspot()
        time.sleep(1)
    except Exception as e:
        print("Error during provisioning:", e)
    finally:
        print("Rebooting…")
        os.sync()
        time.sleep(1)
        os.system("reboot")

# ====== HTTP server ======
def run_server(bind_ip="0.0.0.0", port=8080):
    httpd = HTTPServer((bind_ip, port), Portal)
    print(f"Web portal listening on http://{bind_ip}:{port}/")
    httpd.serve_forever()

# ====== Main ======
def main():
    ensure_root()
    if not nm_available():
        print("NetworkManager (nmcli) not found. Install and ensure it's managing wlan0.")
        print("  sudo apt update && sudo apt install -y network-manager")
        sys.exit(1)
    try:
        run("nmcli radio wifi on", check=False)
    except Exception:
        pass
    print(f"Starting hotspot SSID='{HOTSPOT_SSID}' password='{HOTSPOT_PASS}' on {WIFI_IFACE}…")
    ip = start_hotspot()
    if ip:
        print(f"Hotspot up. Interface {WIFI_IFACE} IP: {ip} (NM shared networks often use 10.42.0.1)")
    else:
        print("Warning: could not determine hotspot IP. Proceeding anyway.")
    try:
        run_server("0.0.0.0", 8080)
    except KeyboardInterrupt:
        print("Interrupted, cleaning up…")
        stop_hotspot()

if __name__ == "__main__":
    main()
