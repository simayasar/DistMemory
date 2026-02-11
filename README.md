# DistMemory

Multiplayer memory-matching game (Distributed Systems project).

## Setup
```bash
python -m venv venv
# Windows: .\venv\Scripts\activate
# macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
```
## Run
Open separate terminals (one per process) and run:

## Primary
```bash
python src/main.py --role primary
```

## Backup (you can run multiple backups)
```bash
python src/main.py --role backup
```

## Run another backup in a new terminal if needed:
```bash
python src/main.py --role backup
```

## Client Terminal ( playing on terminal)
```bash
python src/client_terminal.py
```

## Launcher ( playing on browser)
```bash
python src/launcher.py
```

## Features
- **Primary + multiple backups (warm-standby replication)**
- **Automatic leader failover** using a Bully-style election
- **Browser client (WebSocket)** and **terminal client (TCP)**
- **Sequencer-based total order** (`seq`) for consistent state up

## How to Test Failover
- **Start Primary + at least one Backup**
- **Start a browser client and join a game**
- **Kill the Primary process**
  
## Observe:
- **Backups run election (UDP 10001)**
- **One backup becomes the new Primary**
- **Clients reconnect, and the game continues**

## Ports:
- **8765**: WebSocket server (browser client)
- **8766**: TCP replication (primary → backups)
- **8767**: TCP server (terminal clients)
- **9999**: UDP discovery
- **10001**: UDP election

## Notes
Do not commit venv/ to GitHub.


