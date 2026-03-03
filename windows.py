#!/usr/bin/env python3
"""
Windows Click Server - GUI Version with Sun Valley Theme
Receives button events from Pi and simulates mouse clicks

IMPROVEMENTS:
- Better socket cleanup and handling
- Faster detection of dead connections
- TCP keepalive for detecting network changes
- Non-blocking client handling
"""

import select
import socket
import threading
import sys
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from pynput.mouse import Button, Controller
from pynput import keyboard as pynkeyboard
import sv_ttk
import time

# Optional: py-window-styles for dark title bar on Windows
try:
    import pywinstyles
    _HAS_PYWINSTYLES = True
except Exception:
    _HAS_PYWINSTYLES = False

LISTEN_IP = '0.0.0.0'
PORT = 5000


def apply_theme_to_titlebar(root):
    try:
        if not _HAS_PYWINSTYLES:
            return
        version = sys.getwindowsversion()
        dark_mode = sv_ttk.get_theme() == "dark"
        if version.major == 10 and version.build >= 22000:
            pywinstyles.change_header_color(root, "#1c1c1c" if dark_mode else "#fafafa")
        elif version.major == 10:
            pywinstyles.apply_style(root, "dark" if dark_mode else "normal")
            try:
                root.wm_attributes("-alpha", 0.99)
                root.wm_attributes("-alpha", 1.0)
            except tk.TclError:
                pass
    except Exception:
        pass


class ClickServerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Windows Click Server")
        self.root.geometry("700x600")
        self.root.resizable(False, False)

        sv_ttk.set_theme("dark")
        self.root.bind("<Map>", lambda e: apply_theme_to_titlebar(self.root))

        self.server = None
        self.is_running = False
        self.mouse = Controller()
        self.client_connected = False
        
        # Track current client connection
        self.current_client_conn = None
        self.client_lock = threading.Lock()

        # --- Click logging toggle ---
        self.log_clicks_var = tk.BooleanVar(value=False)

        # --- F6 → F2 hold state ---
        self.enable_f6_hold_var = tk.BooleanVar(value=False)
        self._kb_controller = pynkeyboard.Controller()
        self._hold_thread = None
        self._hold_stop = threading.Event()
        self._holding_active = False
        self._f6_is_down = False
        self._kb_listener = None

        self.setup_ui()
        self.root.after(100, self.start_server)
        self._start_keyboard_listener()

    # ---------- keyboard listener & hold loop ----------
    def _start_keyboard_listener(self):
        def on_press(key):
            try:
                if key == pynkeyboard.Key.f6:
                    if not self._f6_is_down:
                        self._f6_is_down = True
                        self._handle_f6_toggle()
            except Exception as e:
                self.root.after(0, lambda: self.log(f"Hotkey error: {e}", "error"))

        def on_release(key):
            if key == pynkeyboard.Key.f6:
                self._f6_is_down = False

        self._kb_listener = pynkeyboard.Listener(on_press=on_press, on_release=on_release)
        self._kb_listener.daemon = True
        self._kb_listener.start()
        self.root.after(0, lambda: self.log("Global hotkey ready: F6 (when enabled) toggles F2 hold", "info"))

    def _handle_f6_toggle(self):
        if not self.enable_f6_hold_var.get():
            self.log("F6 pressed, but 'Enable F6 → Hold F2' is off", "warning")
            return
        if not self._holding_active:
            self._start_f2_hold()
        else:
            self._stop_f2_hold()

    def _start_f2_hold(self):
        if self._holding_active:
            return
        self._holding_active = True
        self._hold_stop.clear()

        def loop():
            try:
                while not self._hold_stop.is_set():
                    self._kb_controller.press(pynkeyboard.Key.f2)
                    time.sleep(0.03)
            finally:
                try:
                    self._kb_controller.release(pynkeyboard.Key.f2)
                except Exception:
                    pass

        self._hold_thread = threading.Thread(target=loop, daemon=True)
        self._hold_thread.start()
        self.log("F2 HOLD: started (simulated)", "success")

    def _stop_f2_hold(self):
        if not self._holding_active:
            return
        self._hold_stop.set()
        self._hold_thread = None
        self._holding_active = False
        self.log("F2 HOLD: stopped", "warning")

    def _disable_feature_and_release(self):
        if self._holding_active:
            self._stop_f2_hold()

    # --------------------------------------------------------
    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        title_label = ttk.Label(main_frame, text="Click Server", font=("Segoe UI", 18, "bold"))
        title_label.grid(row=0, column=0, columnspan=2, pady=(0, 15))

        # Status frame
        status_frame = ttk.LabelFrame(main_frame, text="Status", padding="15")
        status_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 15))

        server_frame = ttk.Frame(status_frame)
        server_frame.grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Label(server_frame, text="Server:", font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 10))
        self.server_status = tk.Canvas(server_frame, width=12, height=12, highlightthickness=0, bg="#1c1c1c")
        self.server_status.pack(side=tk.LEFT, padx=(0, 8))
        self.server_indicator = self.server_status.create_oval(2, 2, 10, 10, fill="#6c6c6c", outline="")
        self.server_label = ttk.Label(server_frame, text="Stopped", font=("Segoe UI", 10))
        self.server_label.pack(side=tk.LEFT)

        client_frame = ttk.Frame(status_frame)
        client_frame.grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Label(client_frame, text="Client:", font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 10))
        self.client_status = tk.Canvas(client_frame, width=12, height=12, highlightthickness=0, bg="#1c1c1c")
        self.client_status.pack(side=tk.LEFT, padx=(0, 8))
        self.client_indicator = self.client_status.create_oval(2, 2, 10, 10, fill="#6c6c6c", outline="")
        self.client_label = ttk.Label(client_frame, text="Not Connected", font=("Segoe UI", 10))
        self.client_label.pack(side=tk.LEFT)

        address_frame = ttk.Frame(status_frame)
        address_frame.grid(row=2, column=0, sticky=tk.W, pady=5)
        ttk.Label(address_frame, text="Address:", font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 10))
        self.address_label = ttk.Label(address_frame, text=f"{LISTEN_IP}:{PORT}", font=("Segoe UI", 10, "bold"), foreground="#0078d4")
        self.address_label.pack(side=tk.LEFT)

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=2, column=0, columnspan=2, pady=(0, 10))
        self.start_btn = ttk.Button(button_frame, text="Start Server", command=self.start_server, width=18)
        self.start_btn.grid(row=0, column=0, padx=5)
        self.stop_btn = ttk.Button(button_frame, text="Stop Server", command=self.stop_server, width=18, state=tk.DISABLED)
        self.stop_btn.grid(row=0, column=1, padx=5)
        self.clear_btn = ttk.Button(button_frame, text="Clear Log", command=self.clear_log, width=18)
        self.clear_btn.grid(row=0, column=2, padx=5)

        # --- Hotkey feature frame ---
        feature_frame = ttk.LabelFrame(main_frame, text="Hotkey Feature", padding="15")
        feature_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 15))

        self.enable_chk = ttk.Checkbutton(
            feature_frame,
            text="Enable F6 → Hold F2 (simulated)",
            variable=self.enable_f6_hold_var,
            command=self._on_feature_toggle
        )
        self.enable_chk.grid(row=0, column=0, sticky=tk.W)

        self.log_clicks_chk = ttk.Checkbutton(
            feature_frame,
            text="Log click events (DOWN/UP)",
            variable=self.log_clicks_var
        )
        self.log_clicks_chk.grid(row=2, column=0, sticky=tk.W, pady=(4, 0))

        tip = ttk.Label(
            feature_frame,
            text="Tip: Tap F6 to toggle the F2 hold. Uncheck to stop & release F2.",
            foreground="#858585"
        )
        tip.grid(row=1, column=0, sticky=tk.W, pady=(4, 0))

        # Log frame
        log_frame = ttk.LabelFrame(main_frame, text="Activity Log", padding="15")
        log_frame.grid(row=4, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame, height=16, width=80, wrap=tk.WORD, font=("Consolas", 9),
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="white",
            borderwidth=0, highlightthickness=0
        )
        self.log_scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=self.log_scrollbar.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_scrollbar.grid(row=0, column=1, sticky="ns")

        self.log_text.tag_config("info", foreground="#d4d4d4")
        self.log_text.tag_config("success", foreground="#4ec9b0")
        self.log_text.tag_config("warning", foreground="#ce9178")
        self.log_text.tag_config("error", foreground="#f48771")
        self.log_text.tag_config("timestamp", foreground="#858585")

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(4, weight=1)

        if not _HAS_PYWINSTYLES:
            self.root.after(300, lambda: self.log(
                "Tip: Install 'py-window-styles' for dark title bar on Windows: pip install py-window-styles",
                "warning"
            ))

    def _on_feature_toggle(self):
        if self.enable_f6_hold_var.get():
            self.log("Feature enabled: F6 will toggle F2 hold", "success")
        else:
            self.log("Feature disabled", "warning")
            self._disable_feature_and_release()

    def log(self, message, tag="info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{timestamp}] ", "timestamp")
        self.log_text.insert(tk.END, f"{message}\n", tag)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

    def update_server_status(self, running):
        if running:
            self.server_status.itemconfig(self.server_indicator, fill="#4ec9b0")
            self.server_label.config(text="Running")
            self.start_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.NORMAL)
        else:
            self.server_status.itemconfig(self.server_indicator, fill="#6c6c6c")
            self.server_label.config(text="Stopped")
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)

    def update_client_status(self, connected, addr=None):
        self.client_connected = connected
        if connected:
            self.client_status.itemconfig(self.client_indicator, fill="#4ec9b0")
            self.client_label.config(text=f"Connected: {addr}")
        else:
            self.client_status.itemconfig(self.client_indicator, fill="#6c6c6c")
            self.client_label.config(text="Not Connected")

    def disconnect_current_client(self):
        """Safely disconnect the current client"""
        with self.client_lock:
            if self.current_client_conn:
                try:
                    self.current_client_conn.shutdown(socket.SHUT_RDWR)
                except:
                    pass
                try:
                    self.current_client_conn.close()
                except:
                    pass
                self.current_client_conn = None

    def handle_client(self, conn, addr):
        # Store the current connection
        with self.client_lock:
            # Disconnect any existing client first
            if self.current_client_conn:
                try:
                    self.current_client_conn.shutdown(socket.SHUT_RDWR)
                    self.current_client_conn.close()
                except:
                    pass
            self.current_client_conn = conn
        
        self.root.after(0, lambda: self.update_client_status(True, addr))
        self.root.after(0, lambda: self.log(f"Client connected from {addr}", "success"))
        
        buffer = ""

        try:
            while self.is_running:
                ready, _, _ = select.select([conn], [], [], 0.5)
                if not ready:
                    continue
                try:
                    data = conn.recv(1024)
                    if not data:
                        self.root.after(0, lambda: self.log("Client disconnected (clean)", "warning"))
                        break

                    buffer += data.decode()
                    while '\n' in buffer:
                        message, buffer = buffer.split('\n', 1)
                        message = message.strip()
                        if message == "DOWN":
                            self.mouse.press(Button.left)
                            if self.log_clicks_var.get():
                                self.root.after(0, lambda: self.log("Mouse DOWN", "info"))
                        elif message == "UP":
                            self.mouse.release(Button.left)
                            if self.log_clicks_var.get():
                                self.root.after(0, lambda: self.log("Mouse UP", "info"))
                except Exception as e:
                    self.root.after(0, lambda e=e: self.log(f"Client error: {e}", "error"))
                    break
        except Exception as e:
            self.root.after(0, lambda e=e: self.log(f"Error: {e}", "error"))
        finally:
            with self.client_lock:
                if self.current_client_conn == conn:
                    self.current_client_conn = None
            
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except:
                pass
            try:
                conn.close()
            except:
                pass
            
            self.root.after(0, lambda: self.update_client_status(False))
            self.root.after(0, lambda: self.log("Connection closed", "info"))

    def server_thread(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        
        # Enable TCP keepalive to detect dead connections faster
        if sys.platform == 'win32':
            # Windows: (on/off, time, interval)
            self.server.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10000, 3000))
        
        try:
            self.server.bind((LISTEN_IP, PORT))
            self.server.listen(1)
            self.server.settimeout(1.0)
            self.root.after(0, lambda: self.log(f"Server listening on {LISTEN_IP}:{PORT}", "success"))
            self.root.after(0, lambda: self.log("Waiting for Pi to connect...", "info"))
            
            while self.is_running:
                try:
                    conn, addr = self.server.accept()
                    
                    # Configure the client socket with keepalive
                    conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                    if sys.platform == 'win32':
                        conn.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10000, 3000))
                    
                    # Handle client in a separate thread so we can continue accepting
                    client_thread = threading.Thread(
                        target=self.handle_client, 
                        args=(conn, addr), 
                        daemon=True
                    )
                    client_thread.start()
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.is_running:
                        self.root.after(0, lambda e=e: self.log(f"Accept error: {e}", "error"))
        except Exception as e:
            self.root.after(0, lambda e=e: self.log(f"Server error: {e}", "error"))
        finally:
            self.server.close()
            self.root.after(0, lambda: self.update_server_status(False))

    def start_server(self):
        if not self.is_running:
            self.is_running = True
            self.update_server_status(True)
            self.log("Starting server...", "info")
            thread = threading.Thread(target=self.server_thread, daemon=True)
            thread.start()

    def stop_server(self):
        if self.is_running:
            self.log("Stopping server...", "warning")
            self.is_running = False
            self.disconnect_current_client()
            self.update_client_status(False)

    def on_closing(self):
        self._disable_feature_and_release()
        self.is_running = False
        self.disconnect_current_client()
        if self.server:
            try:
                self.server.close()
            except Exception:
                pass
        try:
            if self._kb_listener:
                self._kb_listener.stop()
        except Exception:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ClickServerGUI(root)
    root.update_idletasks()
    apply_theme_to_titlebar(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()