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

## Launcher 
```bash
python src/launcher.py
```

## Notes
Do not commit venv/ to GitHub.
