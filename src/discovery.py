import socket
import json

DISCOVERY_PORT = 9999
DISCOVERY_MESSAGE = "DISCOVER_DISTMEMORY"
RESPONSE_MESSAGE = "DISTMEMORY_HERE"


def _get_lan_ip() -> str:
    """Find local network IP address (skip 127.0.0.1)"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Dummy connection to find outgoing interface IP
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        return ip
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def start_discovery_server(ws_port: int):
    """Listen for UDP broadcast and reply with server info"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", DISCOVERY_PORT))

    server_ip = _get_lan_ip()
    print(f"[DISCOVERY] Listening on UDP {DISCOVERY_PORT} (server_ip={server_ip})")

    while True:
        data, addr = sock.recvfrom(1024)
        if data.decode(errors="ignore") == DISCOVERY_MESSAGE:
            response = json.dumps({
                "type": RESPONSE_MESSAGE,
                "ws_port": ws_port,
                "server_ip": server_ip,
            })
            sock.sendto(response.encode(), addr)


def discover_server(timeout: float = 3.0):
    """Broadcast UDP message to find server IP and port"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout)

    try:
        sock.sendto(DISCOVERY_MESSAGE.encode(), ("<broadcast>", DISCOVERY_PORT))

        data, addr = sock.recvfrom(1024)
        info = json.loads(data.decode())

        ip = info.get("server_ip") or addr[0]
        port = int(info["ws_port"])
        return ip, port

    except Exception:
        # Discovery failed
        return None, None

    finally:
        sock.close()



def scan_for_server(ws_port: int = 8765, timeout_s: float = 0.15):
    """Fallback: scan local subnet TCP ports if UDP broadcast fails"""
    my_ip = _get_lan_ip()
    if not my_ip or my_ip.startswith("127."):
        return None, None

    parts = my_ip.split(".")
    if len(parts) != 4:
        return None, None

    prefix = ".".join(parts[:3]) + "."

    for host in range(1, 255):
        ip = f"{prefix}{host}"
        try:
            # TCP 3-way handshake test
            s = socket.create_connection((ip, ws_port), timeout=timeout_s)
            s.close()
            return ip, ws_port
        except Exception:
            continue

    return None, None