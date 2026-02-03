const boardEl = document.getElementById("board");
const scoresEl = document.getElementById("scores");
const turnTextEl = document.getElementById("turnText");
const toastEl = document.getElementById("toast");
const mePillEl = document.getElementById("mePill");

const modalEl = document.getElementById("resultModal");
const modalTitleEl = document.getElementById("resultTitle");
const modalBodyEl = document.getElementById("resultBody");
const closeModalBtn = document.getElementById("closeModal");


const STORAGE_KEY = "distmemory_player_id";
let reconnectTimer = null;
let reconnectAttempts = 0;


closeModalBtn.addEventListener("click", () => {
  modalEl.style.display = "none";
});

function showResultModal(title, bodyHtml) {
  modalTitleEl.textContent = title;
  modalBodyEl.innerHTML = bodyHtml;
  modalEl.style.display = "block";
}

const turnHintEl = document.getElementById("turnHint");

const params = new URLSearchParams(window.location.search);
const ip = params.get("ip");
const port = params.get("port");

let ws = null;
let myId = null;
let lastState = null;

function toast(msg) { toastEl.textContent = msg; }

function isFlipped(i, st) {
  return st.matched.includes(i) || st.face_up.includes(i);
}

function canClickCard(i, st) {
  if (!myId) return false;
  if (st.turn !== myId) return false;
  if (st.matched.includes(i)) return false;
  if (st.face_up.includes(i)) return false;
  if (st.face_up.length >= 2) return false;
  return true;
}

function render(st) {
  lastState = st;

  // Turn panel
  turnTextEl.textContent = st.turn ? st.turn : "—";

  if (!myId) {
    turnHintEl.textContent = "Connecting…";
  } else if (st.turn === myId) {
    turnHintEl.textContent = "Your turn ✅";
  } else {
    turnHintEl.textContent = "Wait your turn…";
  }

  // Scoreboard
  scoresEl.innerHTML = "";
  (st.players || []).forEach(p => {
    const row = document.createElement("div");
    row.className = "scoreRow";

    const left = document.createElement("div");
    left.className = "who";

    const name = document.createElement("b");
    name.textContent = p + (p === myId ? " (you)" : "");
    const sub = document.createElement("span");
    sub.textContent = (st.turn === p) ? "playing now" : "waiting";

    left.appendChild(name);
    left.appendChild(sub);

    const sc = document.createElement("div");
    sc.className = "score";
    sc.textContent = String(st.scores?.[p] ?? 0);

    row.appendChild(left);
    row.appendChild(sc);
    scoresEl.appendChild(row);
  });

  // Board
  boardEl.innerHTML = "";
  for (let i = 0; i < st.deck.length; i++) {
    const card = document.createElement("div");
    card.className = "card";

    const flipped = isFlipped(i, st);
    const matched = st.matched.includes(i);

    if (flipped) card.classList.add("flipped");
    if (matched) card.classList.add("matched");
    if (!canClickCard(i, st)) card.classList.add("disabled");

    const inner = document.createElement("div");
    inner.className = "cardInner";

    const front = document.createElement("div");
    front.className = "cardFace front";
    front.textContent = "★";

    const back = document.createElement("div");
    back.className = "cardFace back";
    back.textContent = st.deck[i];

    inner.appendChild(front);
    inner.appendChild(back);
    card.appendChild(inner);

    if (!ws || ws.readyState !== WebSocket.OPEN) return;

    card.addEventListener("click", () => {
      if (!lastState) return;
      if (!canClickCard(i, lastState)) return;

      sendFlip(i);
    });

    boardEl.appendChild(card);
  }
}

// Retry logic for reliability
let pendingFlip = null;
let retryTimer = null;

function sendFlip(index) {
  if (myId !== lastState.turn) {
    toast("Not your turn!");
    return;
  }

  // Unique request ID
  const reqId = Date.now() + "_" + Math.floor(Math.random() * 1000);

  const msg = {
    type: "FLIP",
    index: index,
    req_id: reqId
  };

  // Clear pending flip
  if (pendingFlip) {
    clearTimeout(retryTimer);
  }

  pendingFlip = { msg, attempts: 0 };
  performSend();
}


// Retry if no response
function performSend() {
  if (!pendingFlip) return;

  pendingFlip.attempts++;
  if (pendingFlip.attempts > 5) {
    toast("Server unreachable, giving up action.");
    pendingFlip = null;
    return;
  }

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(pendingFlip.msg));
    if (pendingFlip.attempts > 1) {
      console.log(`[Reliability] Retrying action (Attempt ${pendingFlip.attempts})...`);
    }
  }

  // Wait 1.5s then retry
  retryTimer = setTimeout(performSend, 1500);
}

// Stop retrying when confirmed
function onActionConfirmed(player, index) {
  if (pendingFlip && pendingFlip.msg.index === index && player === myId) {
    // Success!
    clearTimeout(retryTimer);
    pendingFlip = null;
    retryTimer = null;
    console.log("[Reliability] Action confirmed by server.");
  }
}

// Failover logic
let knownBackups = [];
let currentIp = ip;    // The IP we are currently trying to connect to
let currentPort = port;

function connect() {
  if (!currentIp || !currentPort) {
    toast("Missing ip/port. Open via launcher.py");
    return;
  }

  toast(`Connecting to ws://${currentIp}:${currentPort} ...`);
  ws = new WebSocket(`ws://${currentIp}:${currentPort}`);

  ws.onopen = () => {
    toast("Connected ✅");
    reconnectAttempts = 0;

    const saved = sessionStorage.getItem(STORAGE_KEY);

    if (saved) {
      ws.send(JSON.stringify({ type: "HELLO", player_id: saved }));
    } else {
      // Request new player ID
      ws.send(JSON.stringify({ type: "JOIN" }));
    }
  };


  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);

    // FAILOVER: Update backup list
    if (msg.backups && Array.isArray(msg.backups)) {
      // Filter out our current IP just in case, merge logic
      msg.backups.forEach(bkIp => {
        if (!knownBackups.includes(bkIp) && bkIp !== currentIp) {
          knownBackups.push(bkIp);
        }
      });
      console.log("Updated Backup List:", knownBackups);
    }

    if (msg.welcome && msg.welcome.player_id) {
      myId = msg.welcome.player_id;
      mePillEl.textContent = myId;
      sessionStorage.setItem(STORAGE_KEY, myId);
      toast(`Welcome ${myId}`);
    }

    if (msg.state) {
      render(msg.state);
    }

    if (msg.event?.kind === "FLIP") {
      // Action confirmed, stop retrying
      onActionConfirmed(msg.event.player, msg.event.index);
    }

    if (msg.event?.kind === "MATCH") toast(`${msg.event.player} found a match!`);
    if (msg.event?.kind === "MISMATCH") toast(`${msg.event.player} mismatched…`);
    if (msg.event?.kind === "TURN_ADVANCE") toast(`Turn: ${msg.event.turn}`);

    if (msg.event?.kind === "GAME_OVER") {
      // (your existing modal logic stays here)
      const winners = msg.event.winners || [];
      const scores = msg.event.scores || {};

      let title = "Draw";
      if (winners.length === 1) {
        title = (winners[0] === myId) ? "You won ✅" : "You lost ❌";
      } else {
        title = "Draw 🤝";
      }

      const lines = Object.keys(scores)
        .sort((a, b) => (scores[b] ?? 0) - (scores[a] ?? 0))
        .map(p => `<div style="display:flex; justify-content:space-between; margin-top:6px;">
                    <span>${p}${p === myId ? " (you)" : ""}</span>
                    <b>${scores[p]}</b>
                  </div>`)
        .join("");

      showResultModal(title, `<div>Game finished.</div>${lines}`);
    }
  };

  ws.onclose = () => {
    toast("Disconnected — trying next server…");
    scheduleReconnect();
  };

  ws.onerror = () => {
    // often onerror is followed by onclose
    console.log("WebSocket error");
  };
}

function scheduleReconnect() {
  if (reconnectTimer) return;

  reconnectAttempts += 1;
  const delay = 1500;

  console.log(`Scheduling reconnect #${reconnectAttempts} in ${delay}ms`);

  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;

    // Try next backup
    if (knownBackups.length > 0) {
      const nextIp = knownBackups.shift();
      knownBackups.push(currentIp);

      currentIp = nextIp;
      console.log(`Failover: Switching to ${currentIp}`);
    }

    connect();
  }, delay);
}



connect();

