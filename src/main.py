"""Main entry point - starts Primary server or Backup loop with failover logic"""

import threading
import asyncio
import argparse
import random
import time


import replication
from server import start_server, load_state
from discovery import start_discovery_server, discover_server, scan_for_server
from election import BullyElection

WS_PORT = 8765


def run_primary():
    """Start Primary Server mode - launches discovery and main server"""
    discovery_thread = threading.Thread(
        target=start_discovery_server,
        args=(WS_PORT,),
        daemon=True
    )
    discovery_thread.start()

    asyncio.run(start_server(WS_PORT))


async def run_backup_loop(server_id: int):
    """
    Backup loop:
    1. Listen for elections
    2. Sync with Primary
    3. If Primary dies, elect new leader
    4. Become Primary if I win, else follow new leader
    """
    # Start election listener
    elect = BullyElection(server_id=server_id, ws_port=WS_PORT)
    elect.start()

    while True:
        # Step 1: Find the primary server
        # Try UDP Broadcast first
        primary_ip, primary_port = discover_server()

        # If UDP fails, try TCP Scan fallback
        if not primary_ip:
            print("[BACKUP] UDP discovery failed, trying TCP scan...")
            primary_ip, primary_port = scan_for_server(ws_port=WS_PORT)

        # If still not found, retry
        if not primary_ip or not primary_port:
            print("[BACKUP] Primary not found. Retrying...")
            await asyncio.sleep(2)
            continue

        # Step 2: Sync until primary fails
        # Returns last known sequence number
        last_seq = await replication.run_backup_replica(primary_ip, primary_port)

        # Step 3: Primary is gone, run election
        leader_id, leader_ip, leader_ws_port = elect.run_election_blocking()

        # Step 4: If I won, become Primary
        if leader_id == server_id:
            print(f"[FAILOVER] I won election -> becoming PRIMARY with seq={last_seq}")
            load_state(replication.replica_state, last_seq)

            discovery_thread = threading.Thread(
                target=start_discovery_server,
                args=(WS_PORT,),
                daemon=True
            )
            discovery_thread.start()

            await start_server(WS_PORT)
            return

        # Step 5: I lost, follow the new leader
        print(f"[FAILOVER] I lost election -> following leader id={leader_id} at {leader_ip}:{leader_ws_port}")

        time.sleep(0.7)


def main():
    """Parse args and start as primary or backup"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["primary", "backup"], default="primary")
    parser.add_argument("--id", type=int, default=None, help="Backup server id")
    args = parser.parse_args()

    # Generate random ID if needed
    my_id = args.id
    if my_id is None:
        # Use large range to minimize collision chance
        my_id = random.randint(1, 100000)
        print(f"[MAIN] Assigned Random ID: {my_id}")

    if args.role == "primary":
        run_primary()
    else:
        asyncio.run(run_backup_loop(server_id=my_id))


if __name__ == "__main__":
    main()
