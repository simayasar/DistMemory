"""Finds the server and opens the client in the browser"""

import webbrowser
from pathlib import Path
from urllib.parse import urlencode

from discovery import discover_server, scan_for_server



def _open_in_chrome(url: str) -> bool:
    """Helper to open URL in Chrome, falls back to default browser"""
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]

    for p in chrome_paths:
        if Path(p).exists():
            webbrowser.get(f'"{p}" %s').open(url)
            return True

    # Fallback: try if "chrome" is registered
    try:
        webbrowser.get("chrome").open(url)
        return True
    except Exception:
        return False


def main():
    """Find server and launch client UI"""
    ip, port = discover_server()

    if not ip:
        print("[LAUNCHER] UDP discovery failed, scanning local subnet...")
        ip, port = scan_for_server(ws_port=8765)

    if not ip:
        print("[LAUNCHER] Server not found (discovery + scan failed).")
        return

    client_file = (Path(__file__).parent / "client.html").resolve()

    # file:///C:/.../client.html
    base = client_file.as_uri()

    # file:///.../client.html?ip=...&port=...
    url = f"{base}?{urlencode({'ip': ip, 'port': port})}"

    print("[LAUNCHER] Opening client:", url)

    if not _open_in_chrome(url):
        # If Chrome isn't found, open with default browser
        webbrowser.open(url)


if __name__ == "__main__":
    main()