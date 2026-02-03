"""Backup server logic - listens for state updates from primary via TCP"""

import json
import asyncio

# Holds the replica copy (in-memory)
replica_state = None
replica_seq = 0


async def run_backup_replica(primary_ip: str, ws_port: int) -> int:
    """Connect to Primary server and sync state updates"""
    global replica_state, replica_seq

    tcp_port = ws_port + 1
    print(f"[REPL] Connecting to primary (TCP) at {primary_ip}:{tcp_port}")

    writer = None
    try:
        reader, writer = await asyncio.open_connection(primary_ip, tcp_port)
        print("[REPL] Connected to Primary via TCP")

        # Keep reading lines (one JSON per line)
        while True:
            line = await reader.readline()
            if not line:
                break
            
            try:
                msg = json.loads(line.decode("utf-8").strip())
            except Exception:
                continue

            if msg.get("type") == "STATE_UPDATE" and "state" in msg:
                replica_state = msg["state"]
                
                # Update sequence number
                if "seq" in msg:
                    replica_seq = msg["seq"]
                
                ev = msg.get("event", {}).get("kind")
                if ev:
                    print(f"[REPL] seq={replica_seq} event={ev}")

    except Exception as e:
        print("[REPL] Connection lost / failed:", e)
    finally:
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except:
                pass

    # Return last known seq so we can take over if needed
    return replica_seq
