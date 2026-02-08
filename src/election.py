"""
This module implements the Bully Algorithm for leader election. It handles UDP broadcasting and coordination to determine the new leader when the primary server fails.
"""

import json
import socket
import threading
import time
from typing import Optional, Tuple

ELECTION_UDP_PORT = 10001
BROADCAST_ADDR = ("255.255.255.255", ELECTION_UDP_PORT)


def get_local_ip() -> str:
    """
    Determines the local IP address of the machine.
    
    Uses a dummy connection to a public DNS (8.8.8.8) to find the primary network interface IP.
    Does not actually send data. Returns "127.0.0.1" on failure.
    """
    # Best-effort local IP discovery (no internet needed, just uses routing table)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class BullyElection:
    """
    Implements a simplified Bully Election algorithm over UDP.
    
    Attributes:
        server_id (int): Unique ID of this server.
        ws_port (int): WebSocket port to advertise if this server becomes leader.
        ip (str): Local IP address.
    """

    def __init__(self, server_id: int, ws_port: int):
        self.server_id = server_id
        self.ws_port = ws_port
        self.ip = get_local_ip()

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(("", ELECTION_UDP_PORT))
        self.sock.settimeout(0.2)

        self._stop = False
        self._listener = threading.Thread(target=self._listen_loop, daemon=True)

        self.current_leader: Optional[Tuple[int, str, int]] = None  # (id, ip, ws_port)

        # Internal election flags
        self._got_ok = False
        self._election_in_progress = False

    def start(self):
        """
        Starts the election listener thread.
        """
        self._listener.start()
        print(f"[ELECT] Listening UDP {ELECTION_UDP_PORT} as id={self.server_id} ip={self.ip}")

    def stop(self):
        self._stop = True
        try:
            self.sock.close()
        except Exception:
            pass

    def _send_broadcast(self, payload: dict):
        """
        Sends a UDP broadcast message to all nodes on the subnet.
        """
        data = json.dumps(payload).encode("utf-8")
        self.sock.sendto(data, BROADCAST_ADDR)

    def _send_unicast(self, addr, payload: dict):
        """
        Sends a UDP unicast message to a specific address.
        """
        data = json.dumps(payload).encode("utf-8")
        self.sock.sendto(data, addr)

    def _listen_loop(self):
        """
        Background thread loop for handling incoming UDP election messages.
        
        Handles:
        - ELECTION: Responds with OK if my ID is higher, then starts own election.
        - OK: Notes that a higher ID node is alive.
        - COORDINATOR: Updates the current leader information.
        """
        while not self._stop:
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except Exception:
                break

            try:
                msg = json.loads(data.decode("utf-8"))
            except Exception:
                continue

            mtype = msg.get("type")
            sid = msg.get("id")
            ip = msg.get("ip")
            ws_port = msg.get("ws_port")

            if mtype == "ELECTION":
                # If someone with lower id starts election, and we are higher => respond OK and start our own election
                if isinstance(sid, int) and sid < self.server_id:
                    self._send_unicast(addr, {"type": "OK", "id": self.server_id})
                    # Start election (bully) if not already
                    if not self._election_in_progress:
                        threading.Thread(target=self.run_election_blocking, daemon=True).start()


            elif mtype == "OK":
                if isinstance(sid, int) and sid > self.server_id:
                    self._got_ok = True




            elif mtype == "COORDINATOR":

                # A server says: "I am the new leader"
                if isinstance(sid, int) and isinstance(ip, str) and isinstance(ws_port, int):

                    # If the announced leader ID is smaller than mine,
                    # it is not allowed to be leader (Bully rule)
                    if sid < self.server_id:

                        # If I am not already running an election,
                        # start a new one to become leader
                        if not self._election_in_progress:
                            threading.Thread(
                                target=self.run_election_blocking,
                                daemon=True
                            ).start()
                        # Ignore this wrong coordinator message
                        continue

                    # If the leader ID is bigger than mine,
                    # accept it as the real leader
                    self.current_leader = (sid, ip, ws_port)
                    # Election is finished
                    self._election_in_progress = False
                    # Print who is the leader
                    print(f"[ELECT] Leader is id={sid} at {ip}:{ws_port}")

    def run_election_blocking(self, timeout_sec: float = 1.2) -> Tuple[int, str, int]:
        """
        Initiates and manages a blocking Bully election process.
        
        Steps:
        1. Broadcasts ELECTION(id).
        2. Waits for OK responses.
        3. If no OK received: assumes leadership, broadcasts COORDINATOR, returns self.
        4. If OK received: waits for COORDINATOR from a higher node
        5. Returns the (id, ip, port) of the new leader.
        """
        if self._election_in_progress:
            # if already running, just wait until current_leader is set
            while self.current_leader is None and not self._stop:
                time.sleep(0.05)
            return self.current_leader

        self._election_in_progress = True
        self._got_ok = False
        self.current_leader = None

        print(f"[ELECT] Starting election (id={self.server_id})")
        self._send_broadcast({
            "type": "ELECTION",
            "id": self.server_id,
            "ip": self.ip,
            "ws_port": self.ws_port
        })

        t0 = time.time()
        while time.time() - t0 < timeout_sec:
            if self._got_ok:
                break
            time.sleep(0.05)

        if not self._got_ok:
            # We are the leader
            self.current_leader = (self.server_id, self.ip, self.ws_port)
            print(f"[ELECT] I am the leader (id={self.server_id}) -> broadcasting COORDINATOR")
            self._send_broadcast({
                "type": "COORDINATOR",
                "id": self.server_id,
                "ip": self.ip,
                "ws_port": self.ws_port
            })
            self._election_in_progress = False
            return self.current_leader

        # Higher id exists, wait a bit for COORDINATOR
        wait_limit = time.time() + 2.5
        while self.current_leader is None and time.time() < wait_limit:
            time.sleep(0.05)

        # Fallback: if nobody announced, retry election
        if self.current_leader is None:
            self._election_in_progress = False
            return self.run_election_blocking(timeout_sec=timeout_sec)

        return self.current_leader
