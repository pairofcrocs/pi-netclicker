#!/usr/bin/env python3
"""
Raspberry Pi Button Client
Sends button events to Windows server over Tailscale

IMPROVEMENTS:
- Better reconnection logic
- TCP keepalive for network change detection
- Graceful handling of network transitions
"""
import queue
import socket
import time
import RPi.GPIO as GPIO

# Configuration
WINDOWS_IP = '100.90.49.28'
PORT = 5000
GPIO_PIN = 17
DEBOUNCE_MS = 84
RECONNECT_DELAY = 2  # seconds
CONNECT_TIMEOUT = 5  # seconds

event_queue = queue.Queue()

# Global variables
sock = None
connected = False

def setup_gpio():
    """Initialize GPIO with cleanup"""
    # Clean up any previous GPIO usage
    GPIO.setwarnings(False)
    try:
        GPIO.cleanup()
    except:
        pass
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(GPIO_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

def close_socket():
    """Safely close the socket"""
    global sock
    if sock:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except:
            pass
        try:
            sock.close()
        except:
            pass
        sock = None

def connect_to_server():
    """Connect to Windows server with retry logic"""
    global sock, connected
    
    while True:
        # Clean up any existing socket first
        close_socket()
        connected = False
        
        try:
            print(f"Connecting to {WINDOWS_IP}:{PORT}...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            
            # Enable TCP keepalive to detect dead connections
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            
            # Linux-specific keepalive settings (detect failure in ~30 seconds)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            except AttributeError:
                pass  # Not on Linux
            
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect((WINDOWS_IP, PORT))
            sock.settimeout(None)  # Remove timeout after connect
            
            connected = True
            print("✓ Connected to Windows server!")
            return True
            
        except socket.timeout:
            print(f"Connection timeout. Retrying in {RECONNECT_DELAY}s...")
            close_socket()
            time.sleep(RECONNECT_DELAY)
            
        except Exception as e:
            print(f"Connection failed: {e}. Retrying in {RECONNECT_DELAY}s...")
            close_socket()
            time.sleep(RECONNECT_DELAY)

def send_event(event_type):
    """Send button event to server with automatic reconnection"""
    global sock, connected
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if not connected:
                connect_to_server()
            
            # Send event (DOWN or UP)
            sock.sendall(f"{event_type}\n".encode())
            print(f"Button {event_type}")
            return True
            
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            print(f"Connection broken. Reconnecting... (attempt {attempt + 1}/{max_retries})")
            connected = False
            close_socket()
            
            if attempt < max_retries - 1:
                time.sleep(0.5)  # Brief pause before retry
                connect_to_server()
            
        except Exception as e:
            print(f"Send failed: {e}. Reconnecting... (attempt {attempt + 1}/{max_retries})")
            connected = False
            close_socket()
            
            if attempt < max_retries - 1:
                time.sleep(0.5)
                connect_to_server()
    
    print("Failed to send event after retries")
    return False

def button_callback(channel):
    state = GPIO.input(GPIO_PIN)
    event_queue.put("DOWN" if state == GPIO.LOW else "UP")


def main():
    """Main program loop with interrupt-driven GPIO"""
    print("=" * 50)
    print("Raspberry Pi Button Client Starting...")
    print(f"GPIO Pin: {GPIO_PIN}")
    print(f"Target: {WINDOWS_IP}:{PORT}")
    print("=" * 50)

    # Setup
    setup_gpio()

    # Connect initially
    connect_to_server()

    # Register interrupt — bouncetime handles debounce natively
    GPIO.add_event_detect(GPIO_PIN, GPIO.BOTH, callback=button_callback, bouncetime=DEBOUNCE_MS)

    print("\nMonitoring button... Press Ctrl+C to exit\n")

    try:
        while True:
            try:
                event = event_queue.get(timeout=1.0)
                send_event(event)
            except queue.Empty:
                continue

    except KeyboardInterrupt:
        print("\n" + "=" * 50)
        print("Shutting down gracefully...")
        print("=" * 50)
    finally:
        GPIO.cleanup()
        close_socket()
        print("Cleanup complete. Goodbye!")

if __name__ == "__main__":
    main()
