"""
This module implements the core game server logic.
"""

import json
import random
import asyncio
import websockets
import random
import websockets

# ---- Symbols (given) ----
SYMBOLS = [
    "🕵️‍♂️", "🐶", "🐱", "🐭", "🐹", "🐰", "🦊", "🐻", "🐼", "🐨",
    "🐯", "🦁", "🐮", "🐷", "🐸", "🐵", "🐔", "🐧", "🐦", "🐤",
    "🐺", "🦄", "🐝", "🐛", "🐌", "🐞", "🦋", "🐢", "🐍", "🦖",
    "🚗", "🚕", "🚌", "🚙", "🚓", "🚑", "🚒", "🚜", "🏎️", "✈️",
    "🎩", "🎲", "🎯", "🔔", "🍎", "🍋", "🍇", "🍉", "🍌", "🍒"
]

clients = set()
active_players = set()
replicas = set()  # Only WebSocket replicas (deprecated but kept for safety/legacy)
tcp_replicas = set()  # New TCP replicas (asyncio.StreamWriter)
tcp_clients = set()  # TCP terminal clients (asyncio.StreamWriter)

client_to_player = {}
seq = 0

state = {
    "deck": [],  # list[str], length 24
    "face_up": [],  # list[int], up to 2 indices
    "matched": [],  # list[int]
    "scores": {},  # player_id -> int
    "players": [],  # list[player_id]
    "turn": None,  # player_id
    "game_over": False,
}


def load_state(new_state: dict, new_seq: int = 0):
    """
    Loads a replicated state into the server.

    This function is used during failover when a backup server takes over as the primary.
    It updates the global game state and sequence number.
    """
    global state, seq
    if new_state:
        state = new_state
    if new_seq > seq:
        seq = new_seq
        print(f"[SERVER] State loaded with seq={seq}")


# prevents concurrent processing while we do the 1s mismatch delay
action_lock = asyncio.Lock()


def init_game():
    """
    Initializes a new game session.

    Selects 12 unique symbols to create 24 pairs, shuffles the deck,
    and resets the board state (face_up, matched cards).
    Existing players and scores are preserved.
    """
    # pick 12 unique symbols in total 24 cards (pairs)
    chosen = random.sample(SYMBOLS, 12)
    deck = chosen + chosen
    random.shuffle(deck)

    state["deck"] = deck
    state["face_up"] = []
    state["matched"] = []
    # keep existing players/scores/turn as it is


def _next_player(current):
    """
    Determines the next player in the turn rotation.

    Returns the player ID of the next player in the list.
    If the current player is not found, returns the first player.
    """
    players = state["players"]
    if not players:
        return None
    if current not in players:
        return players[0]
    i = players.index(current)
    return players[(i + 1) % len(players)]


async def broadcast(msg: dict):
    """
    Broadcasts a message to all connected clients and replicas.

    Increments the sequence number seq for ordering.
    Sends JSON updates to WebSocket clients (players).
    Sends newline-delimited JSON updates to TCP replicas (backup servers).
    """
    global seq
    seq += 1

    # Collect Backup IPs to send to clients for failover
    backup_ips = []
    for writer in tcp_replicas:
        try:
            addr = writer.get_extra_info('peername')
            if addr:
                # addr is (ip, port)
                backup_ips.append(addr[0])
        except:
            pass

    payload = {
        "type": "STATE_UPDATE",
        "seq": seq,
        "state": state,
        "backups": backup_ips,
        **msg,
    }

    # Show what backups we're broadcasting
    if backup_ips:
        print(f"[DEBUG] Broadcasting {len(backup_ips)} backups: {backup_ips}")

    data = json.dumps(payload)

    # Send to players (WebSocket)
    ws_targets = list(clients)
    ws_dead = []
    for c in ws_targets:
        try:
            await c.send(data)
        except Exception:
            ws_dead.append(c)

    for d in ws_dead:
        clients.discard(d)

    # Send to Replicas (TCP) - New "Normal Socket" Requirement
    tcp_payload = data.encode("utf-8") + b"\n"
    tcp_targets = list(tcp_replicas)
    tcp_dead = []

    for writer in tcp_targets:
        try:
            writer.write(tcp_payload)
            await writer.drain()
        except Exception:
            tcp_dead.append(writer)

    for d in tcp_dead:
        tcp_replicas.discard(d)
        try:
            d.close()
        except:
            pass

    # Send to Terminal Clients (TCP)
    tcp_client_targets = list(tcp_clients)
    tcp_client_dead = []

    for w in tcp_client_targets:
        try:
            w.write(tcp_payload)
            await w.drain()
        except Exception:
            tcp_client_dead.append(w)

    for d in tcp_client_dead:
        tcp_clients.discard(d)
        try:
            d.close()
        except:
            pass


async def send_to(ws, msg: dict):
    """
    Sends a direct message to a specific WebSocket client.

    Used for personal messages like "Welcome" or initial state synchronization.
    Also increments the sequence number to maintain consistency
    """
    global seq
    seq += 1
    payload = {
        "type": "STATE_UPDATE",
        "seq": seq,
        "state": state,
        **msg,
    }
    await ws.send(json.dumps(payload))


async def apply_flip(player_id: str, index: int):
    """
    Processes a card flip action requested by a player.

    Validates the move (player's turn, valid index, not already matched).
    Updates game state (face_up cards).
    Resolves matches or mismatches:
    - Match: Cards stay open, player gets points, gets another turn.
    - Mismatch: Cards close after delay, turn passes to next player.
    Checks for Game Over condition.

    """
    print(f"[DEBUG] FLIP request by {player_id} at index {index}")

    # Validate turn
    if state["turn"] != player_id:
        print(f"[DEBUG] Rejected: Not your turn (Current: {state['turn']})")
        return

    # Validate index
    if not isinstance(index, int) or index < 0 or index >= len(state["deck"]):
        print(f"[DEBUG] Rejected: Invalid index {index}")
        return

    if index in state["matched"]:
        print(f"[DEBUG] Rejected: Already matched")
        return

    if index in state["face_up"]:
        print(f"[DEBUG] Rejected: Already face up")
        return

    # If we already have 2 up, ignore
    if len(state["face_up"]) >= 2:
        print(f"[DEBUG] Rejected: 2 cards already up")
        return

    # Flip the card
    state["face_up"].append(index)
    await broadcast({"event": {"kind": "FLIP", "player": player_id, "index": index}})

    # If two cards are up, check for match/mismatch
    if len(state["face_up"]) == 2:
        a, b = state["face_up"][0], state["face_up"][1]
        sym_a, sym_b = state["deck"][a], state["deck"][b]

        if sym_a == sym_b:
            # If there is a match: keep them open forever
            state["matched"].append(a)
            state["matched"].append(b)
            state["face_up"] = []

            state["scores"][player_id] = state["scores"].get(player_id, 0) + 1
            # same player's turn continues
            await broadcast({"event": {"kind": "MATCH", "player": player_id, "a": a, "b": b}})

            # If the game is over with this card match, send GAME_OVER
            game_over = compute_game_over_event(state)
            if game_over:
                print("[SERVER] GAME_OVER:", game_over)
                await broadcast({"event": game_over, "state": state})
                return


        else:
            # If there is a mismatch: show them briefly, then flip back and pass turn
            await broadcast({"event": {"kind": "MISMATCH", "player": player_id, "a": a, "b": b}})
            await asyncio.sleep(1.0)
            state["face_up"] = []
            state["turn"] = _next_player(state["turn"])
            await broadcast({"event": {"kind": "TURN_ADVANCE", "turn": state["turn"]}})



async def handle_client(websocket):
    """
    Main handler for WebSocket client connections.

    Manages:
    - Replica subscriptions (REPL_SUBSCRIBE)
    - Player rejoining (HELLO)
    - New player assignment (JOIN/auto-assign)
    - Game actions (FLIP)
    - Disconnection and cleanup
    """
    # Always accept the socket first
    clients.add(websocket)
    player_id = None
    client_to_player[websocket] = None

    print(f"[SERVER] Socket connected ({len(clients)} ws)")

    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            # 1) Replica subscribe
            if msg.get("type") == "REPL_SUBSCRIBE":
                replicas.add(websocket)
                clients.discard(websocket)

                # This is to ensure this socket is not treated as a player
                old = client_to_player.get(websocket)
                if old:
                    if old in active_players:
                        active_players.remove(old)
                    while old in state["players"]:
                        state["players"].remove(old)
                    state["scores"].pop(old, None)

                client_to_player[websocket] = None
                player_id = None

                print("[SERVER] Replica subscribed")
                await send_to(websocket, {"event": {"kind": "REPL_INIT"}})
                continue

            # 2) HELLO (rejoin) — try reclaim identity before creating a new player
            if msg.get("type") == "HELLO":
                requested = msg.get("player_id")

                # can reclaim only if exists in replicated state and not currently taken
                if requested in state["scores"] and requested not in active_players:
                    player_id = requested
                    client_to_player[websocket] = requested
                    active_players.add(requested)

                    # ensure roster contains it
                    if requested not in state["players"]:
                        state["players"].append(requested)

                    # ensure game exists
                    if not state["deck"]:
                        init_game()
                    if state["turn"] is None and state["players"]:
                        state["turn"] = state["players"][0]

                    print(f"[SERVER] Rejoined as {requested}")

                    # welcome + roster update
                    await send_to(websocket, {"welcome": {"player_id": requested}})
                    await broadcast({"event": {"kind": "PLAYER_JOIN", "player": requested}})
                else:
                    # cannot reclaim — if we already have an id, re-welcome it; otherwise ignore
                    current = client_to_player.get(websocket)
                    if current:
                        await send_to(websocket, {"welcome": {"player_id": current}})

                continue

            if msg.get("type") == "JOIN":
                # do the id assignment below
                pass

            # 3) If still no player_id, assign a brand new one only when a real action arrives
            if player_id is None:
                new_id = f"Player{len(state['players']) + 1}"
                player_id = new_id
                client_to_player[websocket] = new_id
                active_players.add(new_id)

                state["players"].append(new_id)
                state["scores"].setdefault(new_id, 0)

                if not state["deck"]:
                    init_game()
                if state["turn"] is None and state["players"]:
                    state["turn"] = state["players"][0]

                print(f"[SERVER] {new_id} connected ({len(clients)} ws)")

                await send_to(websocket, {"welcome": {"player_id": new_id}})
                await broadcast({"event": {"kind": "PLAYER_JOIN", "player": new_id}})

            # 4) Normal actions
            if msg.get("type") == "FLIP":
                idx = msg.get("index")
                async with action_lock:
                    await apply_flip(player_id, idx)

    except Exception as e:
        print("[SERVER] Connection error:", e)

    finally:
        # remove from socket sets
        clients.discard(websocket)
        replicas.discard(websocket)

        pid = client_to_player.pop(websocket, None)

        if pid:
            if pid in active_players:
                active_players.remove(pid)

            if pid in state["players"]:
                state["players"].remove(pid)

            # if leaving player had the turn, move turn
            if state["turn"] == pid:
                state["turn"] = _next_player(pid)

            await broadcast({"event": {"kind": "PLAYER_LEAVE", "player": pid}})

        print(f"[SERVER] {pid} disconnected ({len(clients)} ws)")


async def handle_tcp_replica(reader, writer):
    """
    Handles a TCP connection from a backup server (Replica).

    Streams the game state updates to the connected replica using newline-delimited JSON.
    This fulfills the requirement of using "normal sockets" for inter-server communication.
    Sends initial state immediately upon connection.
    """
    addr = writer.get_extra_info('peername')
    print(f"[SERVER] New TCP Replica connected from {addr}")

    tcp_replicas.add(writer)

    try:
        current_seq = seq
        init_payload = {
            "type": "STATE_UPDATE",
            "seq": current_seq,
            "state": state,
            "event": {"kind": "REPL_INIT"}
        }
        writer.write(json.dumps(init_payload).encode("utf-8") + b"\n")
        await writer.drain()

        # Keep connection open until they disconnect
        while True:
            data = await reader.readline()
            if not data:
                break
            # Ignore incoming data for now, purely one-way replication
    except Exception as e:
        print(f"[SERVER] TCP Replica {addr} error: {e}")
    finally:
        print(f"[SERVER] TCP Replica {addr} disconnected")
        tcp_replicas.discard(writer)
        writer.close()
        await writer.wait_closed()


async def start_tcp_server(port: int):
    """
    Starts the TCP server for replication.

    Listens on the specified port for incoming backup server connections.
    """
    server = await asyncio.start_server(handle_tcp_replica, '0.0.0.0', port, reuse_address=True)
    addr = server.sockets[0].getsockname()
    print(f"[SERVER] TCP Socket (Replication) running on {addr}")

    async with server:
        await server.serve_forever()


async def handle_tcp_client(reader, writer):
    """
    Handles a TCP connection from a terminal client (not WebSocket).

    This allows terminal-based clients to connect without WebSocket protocol.
    Uses the same game logic as WebSocket clients but with raw TCP + NDJSON.
    """
    addr = writer.get_extra_info('peername')
    print(f"[SERVER] TCP Client connected from {addr}")

    # Add to TCP clients set for broadcasting
    tcp_clients.add(writer)

    player_id = None

    global seq

    try:
        while True:
            line = await reader.readline()
            if not line:
                break

            try:
                msg = json.loads(line.decode('utf-8').strip())
            except Exception:
                continue

            # Handle JOIN/HELLO
            if msg.get("type") == "JOIN":
                if player_id is None:
                    new_id = f"Player{len(state['players']) + 1}"
                    player_id = new_id
                    active_players.add(new_id)

                    state["players"].append(new_id)
                    state["scores"].setdefault(new_id, 0)

                    if not state["deck"]:
                        init_game()
                    if state["turn"] is None and state["players"]:
                        state["turn"] = state["players"][0]

                    print(f"[SERVER] TCP Client {new_id} joined")

                    # Send welcome before broadcast (so they know their ID)
                    seq += 1
                    welcome_msg = {
                        "type": "STATE_UPDATE",
                        "seq": seq,
                        "state": state,
                        "welcome": {"player_id": new_id}
                    }
                    writer.write(json.dumps(welcome_msg).encode('utf-8') + b'\n')
                    await writer.drain()

                    await broadcast({"event": {"kind": "PLAYER_JOIN", "player": new_id}})

            elif msg.get("type") == "HELLO":

                requested = msg.get("player_id")
                if requested in state["scores"]:

                    player_id = requested
                    active_players.add(requested)

                    if requested not in state["players"]:
                        state["players"].append(requested)

                    if not state["deck"]:
                        init_game()

                    if state["turn"] is None and state["players"]:
                        state["turn"] = state["players"][0]

                    print(f"[SERVER] TCP Client rejoined as {requested}")

                    # Send welcome + current state
                    seq += 1

                    welcome_msg = {
                        "type": "STATE_UPDATE",
                        "seq": seq,
                        "state": state,
                        "welcome": {"player_id": requested},
                        "event": {"kind": "TURN_ADVANCE", "turn": state.get("turn")}
                    }

                    writer.write(json.dumps(welcome_msg).encode('utf-8') + b'\n')
                    await writer.drain()
                    await broadcast({"event": {"kind": "PLAYER_JOIN", "player": requested}})


            # Handle FLIP
            elif msg.get("type") == "FLIP" and player_id:
                idx = msg.get("index")
                async with action_lock:
                    await apply_flip(player_id, idx)

    except Exception as e:
        # Suppress noisy connection errors during disconnection
        if not isinstance(e, (ConnectionResetError, ConnectionAbortedError, OSError)):
            print(f"[SERVER] TCP Client {addr} error: {e}")

    finally:
        print(f"[SERVER] TCP Client {player_id} disconnected")

        # Remove from TCP clients set
        tcp_clients.discard(writer)

        if player_id:
            if player_id in active_players:
                active_players.remove(player_id)

            if player_id in state["players"]:
                state["players"].remove(player_id)

            if state["turn"] == player_id:
                state["turn"] = _next_player(player_id)

            await broadcast({"event": {"kind": "PLAYER_LEAVE", "player": player_id}})

        try:
            writer.close()
        except:
            pass


async def send_tcp_message(writer, msg: dict):
    # Helper to send JSON message to TCP client
    global seq
    seq += 1
    payload = {
        "type": "STATE_UPDATE",
        "seq": seq,
        "state": state,
        **msg,
    }
    writer.write(json.dumps(payload).encode('utf-8') + b'\n')
    await writer.drain()


async def start_tcp_client_server(port: int):
    """
    Starts the TCP server for terminal clients.
    Listens on the specified port for incoming terminal client connections
    """
    server = await asyncio.start_server(handle_tcp_client, '0.0.0.0', port, reuse_address=True)
    addr = server.sockets[0].getsockname()
    print(f"[SERVER] TCP Socket (Terminal Clients) running on {addr}")

    async with server:
        await server.serve_forever()


async def start_server(port: int):
    """
    This is the entry point to start the Game Server.
    Runs three servers concurrently:
    1. WebSocket server for Browser Clients (on port = 8765).
    2. TCP server for Backup Replicas (on port + 1 = 8766).
    3. TCP server for Terminal Clients (on port + 2 = 8767).
    """
    print(f"[SERVER] WebSocket (Browser) running on port {port}")

    # Port allocation:
    # port = WebSocket (Browser)
    # port + 1 = TCP Replication (Backup)
    # port + 2 = TCP Client (Terminal)
    tcp_replication_port = port + 1
    tcp_client_port = port + 2

    # Run all three servers concurrently
    await asyncio.gather(
        websockets.serve(handle_client, "0.0.0.0", port, reuse_address=True),
        start_tcp_server(tcp_replication_port),
        start_tcp_client_server(tcp_client_port)
    )

def compute_game_over_event(state):
    deck = state.get("deck", [])
    matched = state.get("matched", [])
    if not deck or len(matched) < len(deck):
        return None

    scores = state.get("scores", {}) or {}
    max_score = max(scores.values()) if scores else 0
    winners = [p for p, s in scores.items() if s == max_score]

    if len(winners) == 1:
        return {"kind": "GAME_OVER", "result": "win", "winner": winners[0]}
    else:
        return {"kind": "GAME_OVER", "result": "draw", "winner": winners}

