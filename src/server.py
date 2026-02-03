"""Main game server - handles players via WebSocket and replicates state to backups via TCP"""

import json
import random
import asyncio
import websockets
import random
import websockets

# Available card symbols
SYMBOLS = [
    "🕵️‍♂️","🐶","🐱","🐭","🐹","🐰","🦊","🐻","🐼","🐨",
    "🐯","🦁","🐮","🐷","🐸","🐵","🐔","🐧","🐦","🐤",
    "🐺","🦄","🐝","🐛","🐌","🐞","🦋","🐢","🐍","🦖",
    "🚗","🚕","🚌","🚙","🚓","🚑","🚒","🚜","🏎️","✈️",
    "🎩","🎲","🎯","🔔","🍎","🍋","🍇","🍉","🍌","🍒"
]

# Connection tracking
clients = set()  # active WebSocket player connections
active_players = set()  # player IDs currently connected
replicas = set()  # old WebSocket replicas (not used anymore)
tcp_replicas = set()  # backup servers (TCP connections)

client_to_player = {}  # map WebSocket to player ID
seq = 0  # sequence number for ordering events


state = {
    "deck": [],              # list[str], length 24
    "face_up": [],           # list[int], up to 2 indices
    "matched": [],           # list[int]
    "scores": {},            # player_id -> int
    "players": [],           # list[player_id]
    "turn": None,            # player_id
    "game_over": False,
}

def load_state(new_state: dict, new_seq: int = 0):
    """Used when backup becomes primary - loads the replicated state"""
    global state, seq
    if new_state:
        state = new_state
    if new_seq > seq:
        seq = new_seq
        print(f"[SERVER] State loaded with seq={seq}")


# Lock to prevent race conditions during card flips
action_lock = asyncio.Lock()


def init_game():
    """Start a new game - pick 12 symbols and shuffle them into pairs"""
    chosen = random.sample(SYMBOLS, 12)
    deck = chosen + chosen
    random.shuffle(deck)

    state["deck"] = deck
    state["face_up"] = []
    state["matched"] = []


def _next_player(current):
    """Get the next player in turn order"""
    players = state["players"]
    if not players:
        return None
    if current not in players:
        return players[0]
    i = players.index(current)
    return players[(i + 1) % len(players)]


async def broadcast(msg: dict):
    """Send a message to all players and backup servers"""
    global seq
    seq += 1
    
    # Collect backup server IPs for client failover
    backup_ips = []
    for writer in tcp_replicas:
        try:
            addr = writer.get_extra_info('peername')
            if addr:
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
    data = json.dumps(payload)

    # Send to all players via WebSocket
    ws_targets = list(clients)
    ws_dead = []
    for c in ws_targets:
        try:
            await c.send(data)
        except Exception:
            ws_dead.append(c)

    for d in ws_dead:
        clients.discard(d)

    # Send to backups via TCP 
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



async def send_to(ws, msg: dict):
    """Send a message to a specific player (for welcome, rejoin, etc)"""
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
    """Handle a card flip - check match/mismatch and update scores"""
    print(f"[DEBUG] FLIP request by {player_id} at index {index}")
    
    if state["turn"] != player_id:
        print(f"[DEBUG] Rejected: Not your turn (Current: {state['turn']})")
        return

    if not isinstance(index, int) or index < 0 or index >= len(state["deck"]):
        print(f"[DEBUG] Rejected: Invalid index {index}")
        return

    if index in state["matched"]:
        print(f"[DEBUG] Rejected: Already matched")
        return

    if index in state["face_up"]:
        print(f"[DEBUG] Rejected: Already face up")
        return

    if len(state["face_up"]) >= 2:
        print(f"[DEBUG] Rejected: 2 cards already up")
        return

    state["face_up"].append(index)
    await broadcast({"event": {"kind": "FLIP", "player": player_id, "index": index}})

    # Check if two cards are flipped
    if len(state["face_up"]) == 2:
        a, b = state["face_up"][0], state["face_up"][1]
        sym_a, sym_b = state["deck"][a], state["deck"][b]

        if sym_a == sym_b:
            # Match found
            state["matched"].append(a)
            state["matched"].append(b)
            state["face_up"] = []

            state["scores"][player_id] = state["scores"].get(player_id, 0) + 1
            await broadcast({"event": {"kind": "MATCH", "player": player_id, "a": a, "b": b}})

            # Check if game over (all cards matched)
            if len(state["matched"]) == len(state["deck"]):
                state["game_over"] = True

                max_score = max(state["scores"].values())
                winners = [p for p, s in state["scores"].items() if s == max_score]

                await broadcast({
                    "event": {
                        "kind": "GAME_OVER",
                        "winners": winners,
                        "scores": state["scores"]
                    }
                })

        else:
            # No match - flip back after 1 second and pass turn
            await broadcast({"event": {"kind": "MISMATCH", "player": player_id, "a": a, "b": b}})
            await asyncio.sleep(1.0)
            state["face_up"] = []
            state["turn"] = _next_player(state["turn"])
            await broadcast({"event": {"kind": "TURN_ADVANCE", "turn": state["turn"]}})


async def handle_client(websocket):
    """Handle a player connection - join, rejoin, and game actions"""
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

            # Handle replica subscription (old WebSocket method)
            if msg.get("type") == "REPL_SUBSCRIBE":
                replicas.add(websocket)
                clients.discard(websocket)

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

            # Handle player rejoin
            if msg.get("type") == "HELLO":
                requested = msg.get("player_id")

                if requested in state["scores"] and requested not in active_players:
                    player_id = requested
                    client_to_player[websocket] = requested
                    active_players.add(requested)

                    if requested not in state["players"]:
                        state["players"].append(requested)

                    if not state["deck"]:
                        init_game()
                    if state["turn"] is None and state["players"]:
                        state["turn"] = state["players"][0]

                    print(f"[SERVER] Rejoined as {requested}")

                    await send_to(websocket, {"welcome": {"player_id": requested}})
                    await broadcast({"event": {"kind": "PLAYER_JOIN", "player": requested}})
                else:
                    current = client_to_player.get(websocket)
                    if current:
                        await send_to(websocket, {"welcome": {"player_id": current}})

                continue

            if msg.get("type") == "JOIN":
                pass

            # Assign new player ID if needed
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

            # Handle game actions
            if msg.get("type") == "FLIP":
                idx = msg.get("index")
                async with action_lock:
                    await apply_flip(player_id, idx)

    except Exception as e:
        print("[SERVER] Connection error:", e)

    finally:
        clients.discard(websocket)
        replicas.discard(websocket)

        pid = client_to_player.pop(websocket, None)

        if pid:
            if pid in active_players:
                active_players.remove(pid)

            if pid in state["players"]:
                state["players"].remove(pid)

            if state["turn"] == pid:
                state["turn"] = _next_player(pid)

            await broadcast({"event": {"kind": "PLAYER_LEAVE", "player": pid}})

        print(f"[SERVER] {pid} disconnected ({len(clients)} ws)")


async def handle_tcp_replica(reader, writer):
    """Handle a backup server connection - send state updates via TCP"""
    addr = writer.get_extra_info('peername')
    print(f"[SERVER] New TCP Replica connected from {addr}")
    
    tcp_replicas.add(writer)

    try:
        # Send current state immediately
        current_seq = seq
        init_payload = {
            "type": "STATE_UPDATE",
            "seq": current_seq,
            "state": state,
            "event": {"kind": "REPL_INIT"}
        }
        writer.write(json.dumps(init_payload).encode("utf-8") + b"\n")
        await writer.drain()

        # Keep connection open
        while True:
            data = await reader.readline()
            if not data:
                break
    except Exception as e:
        print(f"[SERVER] TCP Replica {addr} error: {e}")
    finally:
        print(f"[SERVER] TCP Replica {addr} disconnected")
        tcp_replicas.discard(writer)
        writer.close()
        await writer.wait_closed()


async def start_tcp_server(port: int):
    """Start TCP server for backup connections"""
    server = await asyncio.start_server(handle_tcp_replica, '0.0.0.0', port, reuse_address=True)
    addr = server.sockets[0].getsockname()
    print(f"[SERVER] TCP Socket (Replication) running on {addr}")
    
    async with server:
        await server.serve_forever()


async def start_server(port: int):
    """Start the game server - WebSocket for players, TCP for backups"""
    print(f"[SERVER] WebSocket running on port {port}")
    
    tcp_port = port + 1
    
    await asyncio.gather(
        websockets.serve(handle_client, "0.0.0.0", port, reuse_address=True),
        start_tcp_server(tcp_port)
    )
