"""
client_terminal.py
-----------------
Terminal-based game client using TCP sockets instead of WebSocket.
This demonstrates how to connect to the game server without a browser.

Features:
- TCP socket connection (native socket, not WebSocket)
- Interactive CLI with colored output
- Real-time state updates
- Failover support (automatic reconnection to backup servers)
- Retry mechanism for omission faults

"""

import asyncio
import json
import sys
import argparse
from typing import Optional, Dict, Any, List


# ANSI colors for terminal output
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    GRAY = '\033[90m'


class TerminalClient:
    """
    Terminal-based game client that connects via TCP socket.
    """

    def __init__(self, ip: str, port: int):
        self.primary_ip = ip
        self.primary_port = port
        self.current_ip = ip
        self.current_port = port

        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None

        self.player_id: Optional[str] = None
        self.last_state: Optional[Dict[str, Any]] = None
        self.known_backups: List[str] = []

        self._last_render_key = None

        self.running = True
        self.in_failover = False  # Prevent concurrent failover attempts

    async def connect(self) -> bool:
        """Establish TCP connection to the server."""
        try:
            print(f"{Colors.CYAN}[CONNECTION] Connecting to {self.current_ip}:{self.current_port}...{Colors.RESET}")
            self.reader, self.writer = await asyncio.open_connection(
                self.current_ip,
                self.current_port
            )
            print(f"{Colors.GREEN}[CONNECTION] Connected ✅{Colors.RESET}\n")

            # Send JOIN for new connection, HELLO for reconnection
            if self.player_id:
                # Reconnection - reclaim identity
                await self.send_message({"type": "HELLO", "player_id": self.player_id})
            else:
                # New connection - get new ID
                await self.send_message({"type": "JOIN"})

            return True
        except Exception as e:
            print(f"{Colors.RED}[ERROR] Connection failed: {e}{Colors.RESET}")
            return False

    async def send_message(self, msg: Dict[str, Any]):
        """Send a JSON message to the server."""
        if not self.writer:
            return

        try:
            data = json.dumps(msg).encode('utf-8') + b'\n'
            self.writer.write(data)
            await self.writer.drain()
        except Exception as e:
            print(f"{Colors.RED}[ERROR] Send failed: {e}{Colors.RESET}")

    async def receive_updates(self):
        """Receive and process server messages."""
        #print(f"{Colors.GRAY}[DEBUG] receive_updates started, reader={self.reader is not None}, running={self.running}{Colors.RESET}")

        while self.running:
            if not self.reader:
                print(f"{Colors.GRAY}[DEBUG] No reader, waiting before retry...{Colors.RESET}")
                await asyncio.sleep(1)
                continue

            try:
                while self.running:
                    line = await self.reader.readline()

                    if not line:
                        # Connection closed
                        print(f"{Colors.RED}[CONNECTION] Server disconnected{Colors.RESET}")
                        break

                    # Debug: Show raw message
                    # print(f"{Colors.GRAY}[DEBUG] Received raw: {line[:100]}...{Colors.RESET}")

                    try:
                        msg = json.loads(line.decode('utf-8').strip())
                        # print(f"{Colors.GRAY}[DEBUG] Parsed message type: {msg.get('type')}, has backups: {'backups' in msg}{Colors.RESET}")
                        self.process_message(msg)
                    except json.JSONDecodeError as e:
                        print(f"{Colors.RED}[ERROR] JSON decode failed: {e}{Colors.RESET}")
                        print(f"{Colors.RED}[ERROR] Raw line: {line}{Colors.RESET}")
                        continue

            except asyncio.CancelledError:
                # Task was cancelled so we exit gracefully
                # print(f"{Colors.GRAY}[DEBUG] receive_updates cancelled{Colors.RESET}")
                return
            except Exception as e:
                if self.running:  # Only log if we're still running
                    print(f"{Colors.RED}[ERROR] Connection error: {e}{Colors.RESET}")

            # Connection lost - attempt failover if we're still running
            print(
                f"{Colors.GRAY}[DEBUG] Connection lost: running={self.running}, in_failover={self.in_failover}{Colors.RESET}")

            if not self.running:
                break

            if self.in_failover:
                # Already in failover, wait a bit
                await asyncio.sleep(1)
                continue

            # Attempt failover
            await self.handle_disconnect()

            # If failover succeeded, reader will be set and loop continues
            # If failed, running will be False and loop exits

        # print(f"{Colors.GRAY}[DEBUG] receive_updates exiting{Colors.RESET}")

    def process_message(self, msg: Dict[str, Any]):
        """Process incoming messages from server."""

        # 1) Backups list (for failover)
        if 'backups' in msg and isinstance(msg['backups'], list):
            print(f"{Colors.GRAY}[DEBUG] Received backups from server: {msg['backups']}{Colors.RESET}")
            for backup_ip in msg['backups']:
                if backup_ip not in self.known_backups:
                    self.known_backups.append(backup_ip)
                    print(f"{Colors.GREEN}[BACKUP] Added backup server: {backup_ip}{Colors.RESET}")
            print(
                f"{Colors.GRAY}[DEBUG] Total known backups: {len(self.known_backups)} → {self.known_backups}{Colors.RESET}")

        # 2) Welcome (player id)
        if 'welcome' in msg and 'player_id' in msg['welcome']:
            self.player_id = msg['welcome']['player_id']
            print(f"{Colors.GREEN}[GAME] Welcome! You are {Colors.BOLD}{self.player_id}{Colors.RESET}\n")

        # 3)
        event = msg.get("event") or {}

        if event.get("kind") == "GAME_OVER":
            winner = event.get("winner")
            result = event.get("result")

            if "state" in msg:
                self.last_state = msg["state"]
            self._last_render_key = None
            self.render_state()

            if result == "draw":
                print("\n🏁 GAME OVER: DRAW!\n")
            else:
                if winner == self.player_id:
                    print("\n🏁 GAME OVER: YOU WON! 🎉\n")
                else:
                    print(f"\n🏁 GAME OVER: YOU LOST 😢 (Winner: {winner})\n")

            self.running = False
            try:
                if self.writer:
                    self.writer.close()
            except:
                pass

            sys.exit(0)

        # 4) State update (if it's not GAME_OVER)
        if 'state' in msg:
            self.last_state = msg['state']

            deck = self.last_state.get("deck", [])
            if not deck:
                return

            render_key = (
                tuple(self.last_state.get("face_up", [])),
                tuple(self.last_state.get("matched", [])),
                tuple(sorted((self.last_state.get("scores", {}) or {}).items())),
                self.last_state.get("turn"),
                self.player_id,
            )
            if render_key != self._last_render_key:
                self._last_render_key = render_key
                self.render_state()

        # 5) Normal events
        if event:
            self.handle_event(event)

    def handle_event(self, event: Dict[str, Any]):
        """Handle game events."""
        kind = event.get('kind')

        if kind == 'MATCH':
            player = event.get('player')
            print(f"{Colors.GREEN}🎉 {player} found a match!{Colors.RESET}")

        elif kind == 'MISMATCH':
            player = event.get('player')
            print(f"{Colors.YELLOW}❌ {player} mismatched{Colors.RESET}")

        elif kind == 'TURN_ADVANCE':
            turn = event.get('turn')
            if turn == self.player_id:
                print(f"{Colors.CYAN}🎯 Your turn!{Colors.RESET}")


    def render_state(self):
        """Render the current game state to terminal."""
        if not self.last_state:
            return

        state = self.last_state

        # Clear screen (ANSI escape code)
        print('\033[2J\033[H', end='')

        # Header
        print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 60}")
        print(f"  🧠 DISTRIBUTED MEMORY GAME (Terminal Client)")
        print(f"{'=' * 60}{Colors.RESET}\n")

        # Player info
        if self.player_id:
            print(f"{Colors.BOLD}You: {Colors.GREEN}{self.player_id}{Colors.RESET}")

        # Current turn
        current_turn = state.get('turn', '—')
        if current_turn == self.player_id:
            turn_msg = f"{Colors.GREEN}{Colors.BOLD}YOUR TURN ✅{Colors.RESET}"
        else:
            turn_msg = f"{Colors.GRAY}Wait... ({current_turn}'s turn){Colors.RESET}"
        print(f"{Colors.BOLD}Turn: {turn_msg}\n")

        # Scoreboard
        print(f"{Colors.BOLD}📊 Scores:{Colors.RESET}")
        players = state.get('players', [])
        scores = state.get('scores', {})
        for player in players:
            score = scores.get(player, 0)
            you = " (you)" if player == self.player_id else ""
            playing = " 🎮" if player == current_turn else ""
            print(f"  {player}{you}: {score}{playing}")
        print()

        # Board
        deck = state.get('deck', [])
        matched = state.get('matched', [])
        face_up = state.get('face_up', [])

        print(f"{Colors.BOLD}🎴 Board:{Colors.RESET}")
        print("  ", end="")

        for i in range(len(deck)):
            if i > 0 and i % 6 == 0:
                print("\n  ", end="")

            # Determine card appearance
            if i in matched:
                # Matched cards (green)
                card = f"{Colors.GREEN}{deck[i]:3s}{Colors.RESET}"
            elif i in face_up:
                # Face up cards (yellow)
                card = f"{Colors.YELLOW}{deck[i]:3s}{Colors.RESET}"
            else:
                # Face down cards
                card = f"{Colors.BLUE}[{i:2d}]{Colors.RESET}"

            print(card, end=" ")

        print("\n")

        # Instructions
        if state.get('turn') == self.player_id and len(face_up) < 2:
            print(f"{Colors.CYAN}💡 Type card index to flip (0-{len(deck) - 1}){Colors.RESET}")

        print()

    async def handle_disconnect(self):
        """Handle server disconnection and attempt failover."""
        # Prevent multiple concurrent failover attempts
        if self.in_failover:
            return

        self.in_failover = True

        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except:
                pass  # Ignore cleanup errors

        self.reader = None
        self.writer = None

        # Try backup servers
        if self.known_backups:
            backup_ip = self.known_backups.pop(0)
            self.known_backups.append(self.current_ip)  # Add current to end
            self.current_ip = backup_ip

            print(f"{Colors.YELLOW}[FAILOVER] Switching to backup {self.current_ip}...{Colors.RESET}")

            # Wait a bit for backup to become primary (election + server startup)
            await asyncio.sleep(2)

            if await self.connect():
                # connect() already sent HELLO if player_id exists
                # Reset failover flag - receive_updates loop will continue
                self.in_failover = False
            else:
                # Connection failed, try next backup
                print(f"{Colors.RED}[FAILOVER] Failed to connect to {self.current_ip}, trying next...{Colors.RESET}")
                self.in_failover = False  # Reset before retry
                await self.handle_disconnect()  # Recursive retry
        else:
            print(f"{Colors.RED}[ERROR] No backup servers available. Exiting...{Colors.RESET}")
            self.running = False
            self.in_failover = False

    async def handle_input(self):
        """Handle user input from terminal."""
        # print(f"{Colors.GRAY}[DEBUG] handle_input started{Colors.RESET}")
        loop = asyncio.get_event_loop()

        try:
            while self.running:
                try:
                    # Read input asynchronously
                    line = await loop.run_in_executor(None, sys.stdin.readline)

                    # print(f"{Colors.GRAY}[DEBUG] Read line: '{line.strip()}'{Colors.RESET}")

                    if not line:
                        # print(f"{Colors.GRAY}[DEBUG] EOF received, exiting handle_input{Colors.RESET}")
                        break

                    line = line.strip()

                    if not line:
                        continue

                    # Check if it's a number (card index)
                    if line.isdigit():
                        index = int(line)

                        # Validate
                        if not self.last_state:
                            print(f"{Colors.RED}Waiting for game state...{Colors.RESET}")
                            continue

                        if self.last_state.get('turn') != self.player_id:
                            print(f"{Colors.RED}Not your turn!{Colors.RESET}")
                            continue

                        deck_size = len(self.last_state.get('deck', []))
                        if index < 0 or index >= deck_size:
                            print(f"{Colors.RED}Invalid index! Choose 0-{deck_size - 1}{Colors.RESET}")
                            continue

                        # Send FLIP
                        await self.send_message({"type": "FLIP", "index": index})

                    elif line.lower() in ['quit', 'exit', 'q']:
                        print(f"{Colors.YELLOW}Exiting...{Colors.RESET}")
                        self.running = False
                        break

                    else:
                        print(f"{Colors.GRAY}Unknown command. Type a number to flip a card.{Colors.RESET}")

                except Exception as e:
                    print(f"{Colors.RED}[ERROR] Input error: {e}{Colors.RESET}")
                    break
        except Exception as e:
            print(f"{Colors.YELLOW}[INFO] Input handler error: {e}. Client will stay connected.{Colors.RESET}")

        # print(f"{Colors.GRAY}[DEBUG] handle_input exiting{Colors.RESET}")

    async def run(self):
        """Main client loop."""
        if not await self.connect():
            return

        # Start background tasks
        receive_task = asyncio.create_task(self.receive_updates())
        input_task = asyncio.create_task(self.handle_input())

        # Wait for tasks - only exit if receive_updates fails or user quits
        done, pending = await asyncio.wait(
            [receive_task, input_task],
            return_when=asyncio.FIRST_COMPLETED
        )

        # Check which task completed
        for task in done:
            if task == input_task:
                # Input task finished (stdin closed or user quit)
                # But keep receive_updates running for failover
                if self.running:
                    print(
                        f"{Colors.YELLOW}[INFO] Input closed, but staying connected. Press Ctrl+C to exit.{Colors.RESET}")
                    # Wait for receive_updates to finish
                    await receive_task
                else:
                    # User explicitly quit
                    receive_task.cancel()
                    break
            elif task == receive_task:
                # Connection lost - input task should also stop
                input_task.cancel()
                break

        # Cleanup
        self.running = False
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except:
                pass

        print(f"\n{Colors.CYAN}Goodbye!{Colors.RESET}")


async def main():
    """Entry point for terminal client."""
    parser = argparse.ArgumentParser(description="Terminal-based Memory Game Client (TCP)")
    parser.add_argument("--ip", default=None, help="Server IP address (auto-discovered if not specified)")
    parser.add_argument("--port", type=int, default=8767, help="Server TCP port (default: 8767)")
    args = parser.parse_args()

    print(f"{Colors.BOLD}{Colors.MAGENTA}")
    print("╔═══════════════════════════════════════════════════════╗")
    print("║  🧠 DISTRIBUTED MEMORY GAME - Terminal Client 🧠     ║")
    print("║  Using TCP Socket                                     ║")
    print("╚═══════════════════════════════════════════════════════╝")
    print(f"{Colors.RESET}\n")

    server_ip = args.ip
    server_port = args.port

    # Auto-discover server if IP not provided
    if not server_ip:
        print(f"{Colors.CYAN}[DISCOVERY] Searching for server via UDP broadcast...{Colors.RESET}")

        # Import discovery module
        import sys
        import os
        sys.path.insert(0, os.path.dirname(__file__))
        from discovery import discover_server, scan_for_server

        try:
            # 1. Try UDP Broadcast (Fastest)
            result = discover_server(timeout=3.0)
            if result and result[0]:
                server_ip = result[0]
                print(f"{Colors.GREEN}[DISCOVERY] Found server via UDP at {server_ip}:{server_port} ✅{Colors.RESET}\n")
            else:
                # 2. Try TCP Scan (Fallback for Firewall/Router issues)
                print(f"{Colors.YELLOW}[DISCOVERY] UDP broadcast failed. Scanning local network (this may take a moment)...{Colors.RESET}")
                
                # We scan for the WebSocket port (8765), but we connect to TCP port (8767)
                scan_ip, _ = scan_for_server(ws_port=8765, timeout_s=0.2)
                
                if scan_ip:
                    server_ip = scan_ip
                    print(f"{Colors.GREEN}[DISCOVERY] Found server via TCP Scan at {server_ip}:{server_port} ✅{Colors.RESET}\n")
                else:
                    print(f"{Colors.RED}[DISCOVERY] No server found. Using default 127.0.0.1{Colors.RESET}\n")
                    server_ip = "127.0.0.1"
        except Exception as e:
            print(f"{Colors.YELLOW}[DISCOVERY] Discovery failed: {e}. Using default 127.0.0.1{Colors.RESET}\n")
            server_ip = "127.0.0.1"

    client = TerminalClient(server_ip, server_port)
    await client.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Interrupted by user{Colors.RESET}")
    except Exception as e:
        print(f"{Colors.RED}Fatal error: {e}{Colors.RESET}")
