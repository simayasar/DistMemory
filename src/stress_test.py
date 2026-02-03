"""This script creates multiple fake clients (bots) that connect to the server and send random FLIP requests rapidly. """

import asyncio
import websockets
import json
import random
import time
import threading

SERVER_URI = "ws://localhost:8765"  # where the primary server is running
NUM_CLIENTS = 10     # how many fake players to create
DURATION = 5         # how long each bot runs 

async def bot_client(client_id):
    """A single bot that connects to the server and spams random card flips.This doesn't follow game rules - it just sends requests fast to stress test."""
    uri = SERVER_URI
    try:
        async with websockets.connect(uri) as websocket:
            print(f"[BOT-{client_id}] Connected!")
            
            await websocket.send(json.dumps({"type": "JOIN"}))
            start_time = time.time()
            
            # Send random flips for DURATION seconds
            while time.time() - start_time < DURATION:
                # Pick a random card
                idx = random.randint(0, 23)
                
                msg = {
                    "type": "FLIP", 
                    "index": idx
                }
                await websocket.send(json.dumps(msg))
                await asyncio.sleep(random.uniform(0.05, 0.2))
                
                # Read responses to prevent buffer overflow
                try:
                    while True:
                        resp = await asyncio.wait_for(websocket.recv(), timeout=0.01)
                except asyncio.TimeoutError:
                    pass
                except:
                    break  
                    
    except Exception as e:
        print(f"[BOT-{client_id}] Error: {e}")

async def main():
    """Start all bots at the same time and wait for them to finish."""
    tasks = []
    print(f"--- STRESS TEST STARTING: {NUM_CLIENTS} Bots, {DURATION} Seconds ---")
    
    # Create a task for each bot
    for i in range(NUM_CLIENTS):
        tasks.append(bot_client(i))
    
    # Run all bots at the same time
    await asyncio.gather(*tasks)
    print("--- STRESS TEST FINISHED ---")

if __name__ == "__main__":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
