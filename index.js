// ============================================================================
// TENNIS MULTIPLAYER - server autoritativo
// Express + Socket.io. Game loop a 60fps, broadcast state a 30fps.
// ============================================================================

const path = require('path');
const http = require('http');
const express = require('express');
const { Server } = require('socket.io');

const app = express();
const server = http.createServer(app);
const io = new Server(server, {
  cors: { origin: '*' },
  pingInterval: 10000,
  pingTimeout: 20000,
});

app.use(express.static(path.join(__dirname, 'public')));
app.get('/health', (_, res) => res.send('ok'));

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
  console.log(`[tennis] http://localhost:${PORT}`);
});

// ---------------------------------------------------------------------------
// Costanti campo (metri reali)
// ---------------------------------------------------------------------------
const COURT = {
  DOUBLES_HALF_W: 5.485,
  SINGLES_HALF_W: 4.115,
  BASELINE_Z: 11.885,
  SERVICE_Z: 6.4,
  NET_H_CENTER: 0.91,
  NET_H_POST: 1.07,
  NET_OVERHANG: 0.914, // post extends past doubles sideline
};

// maxSpeed ridotto ~37% e accel/decel espressi come costanti di tempo (tau, in secondi):
// accelTau = tempo per prendere velocità, decelTau = tempo per fermarsi (più alto = scivola).
const COURT_PROFILES = {
  clay:  { restY: 0.74, fricH: 0.78, accelTau: 0.22, decelTau: 0.46, maxSpeed: 3.7, slide: 0.90 },
  grass: { restY: 0.55, fricH: 0.96, accelTau: 0.12, decelTau: 0.16, maxSpeed: 4.1, slide: 0.30 },
  hard:  { restY: 0.66, fricH: 0.88, accelTau: 0.16, decelTau: 0.28, maxSpeed: 3.9, slide: 0.55 },
};

// ---------------------------------------------------------------------------
// Tuning giocabilità (facile da regolare in un punto solo)
// ---------------------------------------------------------------------------
const SHOT_SPEED_SCALE = 0.5;   // -50% sulla velocità di TUTTI i colpi: palla pesante e controllabile, non un missile
const AIR_DRAG = 0.024;         // attrito in aria (era 0.012): la palla rallenta naturalmente durante il rally
const GROUND_FRIC_SCALE = 0.85; // attrito a terra: dopo il rimbalzo trattiene meno velocità orizzontale
const PLAYER_SPEED_SCALE = 1.3; // +30% sulla velocità massima del giocatore
const ACCEL_TAU = 0.15;         // accelerazione graduale: ~0.15s per raggiungere la velocità massima
const HIT_REACH = 4.0;          // raggio hit zone raddoppiato (era 2.0): più facile arrivare sulla palla
const HIT_GRACE_MS = 500;       // finestra di colpo: una volta entrata in zona, colpibile per almeno 0.5s

const TICK_HZ = 60;
const NET_HZ = 30;
const DT = 1 / TICK_HZ;

// ---------------------------------------------------------------------------
// Stato globale: una singola partita condivisa
// ---------------------------------------------------------------------------
const COLORS = ['#ffd166', '#06d6a0', '#118ab2', '#ef476f', '#a78bfa', '#f59e0b', '#10b981', '#f43f5e'];

const state = {
  court: 'hard',
  phase: 'lobby',            // lobby | serving | rally | pointEnd | gameEnd
  players: {},               // id -> player
  ball: null,
  score: createScoreState(),
  serverId: null,            // chi serve
  serveSide: 'right',        // right = deuce court, left = ad court (per chi serve)
  serveBox: null,            // { xSign, zSign } box di servizio valido (diagonale)
  serveFault: 0,             // 0 primo servizio, 1 dopo fault
  pointTimer: 0,
  rallyCount: 0,
  energy: { A: 0, B: 0 },
  lastEventTs: 0,
  events: [],                // eventi da broadcast (one-shot)
  nameCounter: 1,
};

function createScoreState() {
  return {
    games: { A: 0, B: 0 },
    sets: [{ A: 0, B: 0 }],
    points: { A: 0, B: 0 },  // 0,1,2,3 = 0/15/30/40
    deuce: false,
    advantage: null,         // 'A' | 'B' | null
    tiebreak: false,
    tbPoints: { A: 0, B: 0 },
    setsWon: { A: 0, B: 0 },
    matchOver: false,
    winner: null,
    lastServeKmh: 0,
    serveSideHistory: 'right',
  };
}

function resetMatch() {
  state.score = createScoreState();
  state.phase = 'lobby';
  state.events.push({ type: 'matchReset' });
}

// ---------------------------------------------------------------------------
// Helpers giocatori e squadre
// ---------------------------------------------------------------------------
function activePlayers() {
  return Object.values(state.players);
}
function teamCount(team) {
  return activePlayers().filter(p => p.team === team).length;
}
function assignTeam() {
  return teamCount('A') <= teamCount('B') ? 'A' : 'B';
}

function placePlayer(p) {
  // Posiziona giocatori al fondo del proprio campo, distribuiti in larghezza
  const team = p.team;
  const sameTeam = activePlayers().filter(q => q.team === team);
  const idx = sameTeam.indexOf(p);
  const n = Math.max(1, sameTeam.length);
  const span = COURT.DOUBLES_HALF_W * 1.4;
  const slot = n === 1 ? 0 : (idx - (n - 1) / 2) / Math.max(1, (n - 1) / 2);
  p.x = slot * span * 0.55;
  p.z = (team === 'A' ? -1 : 1) * (COURT.BASELINE_Z - 0.6);
  p.vx = 0; p.vz = 0;
  p.targetVx = 0; p.targetVz = 0;
}

function rebalanceTeams() {
  // Sistema squadre se grosso sbilancio
  const list = activePlayers();
  const a = list.filter(p => p.team === 'A');
  const b = list.filter(p => p.team === 'B');
  while (Math.abs(a.length - b.length) > 1) {
    if (a.length > b.length) {
      const mv = a.pop(); mv.team = 'B'; b.push(mv);
    } else {
      const mv = b.pop(); mv.team = 'A'; a.push(mv);
    }
  }
  list.forEach(placePlayer);
}

function gameMode() {
  const n = activePlayers().length;
  if (n >= 8) return '4v4';
  if (n >= 4) return '2v2';
  return '1v1';
}

// ---------------------------------------------------------------------------
// Punteggio tennis
// ---------------------------------------------------------------------------
function addPoint(team) {
  const s = state.score;
  if (s.matchOver) return;
  const other = team === 'A' ? 'B' : 'A';

  if (s.tiebreak) {
    s.tbPoints[team]++;
    const a = s.tbPoints.A, b = s.tbPoints.B;
    if ((a >= 7 || b >= 7) && Math.abs(a - b) >= 2) {
      // tiebreak vinto
      s.games[team]++;
      finishSet(team);
    } else {
      // cambio servizio in tiebreak: dopo il primo punto, ogni 2
      const total = a + b;
      if (total === 1 || (total > 1 && (total - 1) % 2 === 0)) {
        rotateServer();
      }
      // cambio lati ogni 6 punti (visual: ignoriamo)
      setupServe();
    }
    return;
  }

  if (s.advantage === team) {
    // vince il game
    s.games[team]++;
    s.points = { A: 0, B: 0 };
    s.advantage = null; s.deuce = false;
    onGameWon(team);
    return;
  }
  if (s.advantage === other) {
    s.advantage = null; s.deuce = true;
    state.events.push({ type: 'deuce' });
    setupServe();
    return;
  }
  if (s.deuce) {
    s.advantage = team;
    state.events.push({ type: 'advantage', team });
    setupServe();
    return;
  }
  s.points[team]++;
  // 0->15->30->40, oltre 40
  if (s.points[team] >= 4) {
    if (s.points[team] - s.points[other] >= 2) {
      // game vinto
      s.games[team]++;
      s.points = { A: 0, B: 0 };
      onGameWon(team);
    } else if (s.points.A === 3 && s.points.B === 3) {
      s.deuce = true;
      state.events.push({ type: 'deuce' });
      setupServe();
    }
  } else {
    if (s.points.A === 3 && s.points.B === 3) {
      s.deuce = true;
      state.events.push({ type: 'deuce' });
    }
    setupServe();
  }
}

function onGameWon(team) {
  state.events.push({ type: 'game', team });
  rotateServer();
  // set?
  const s = state.score;
  const other = team === 'A' ? 'B' : 'A';
  if (s.games[team] >= 6 && s.games[team] - s.games[other] >= 2) {
    finishSet(team);
  } else if (s.games.A === 6 && s.games.B === 6) {
    s.tiebreak = true;
    s.tbPoints = { A: 0, B: 0 };
    state.events.push({ type: 'tiebreak' });
    setupServe();
  } else {
    setupServe();
  }
}

function finishSet(team) {
  const s = state.score;
  s.setsWon[team]++;
  s.sets[s.sets.length - 1] = { A: s.games.A, B: s.games.B, tb: s.tiebreak ? { A: s.tbPoints.A, B: s.tbPoints.B } : null };
  state.events.push({ type: 'set', team });
  if (s.setsWon[team] >= 2) {
    s.matchOver = true; s.winner = team;
    state.events.push({ type: 'match', team });
    state.phase = 'gameEnd';
    return;
  }
  s.games = { A: 0, B: 0 };
  s.tiebreak = false;
  s.tbPoints = { A: 0, B: 0 };
  s.sets.push({ A: 0, B: 0 });
  setupServe();
}

function rotateServer() {
  // ruota tra giocatori (alterna squadra)
  const list = activePlayers();
  if (list.length === 0) { state.serverId = null; return; }
  const idx = list.findIndex(p => p.id === state.serverId);
  if (idx < 0) { state.serverId = list[0].id; return; }
  // prossimo della squadra opposta
  const cur = list[idx];
  const oppTeam = cur.team === 'A' ? 'B' : 'A';
  const opp = list.filter(p => p.team === oppTeam);
  state.serverId = opp.length ? opp[0].id : list[(idx + 1) % list.length].id;
}

// ---------------------------------------------------------------------------
// Servizio
// ---------------------------------------------------------------------------
function setupServe(keepFault = false) {
  const sv = state.players[state.serverId];
  if (!sv) {
    rotateServer();
    return;
  }
  state.phase = 'serving';
  // Il conteggio falli si azzera solo a inizio punto nuovo, non sulla seconda di servizio.
  if (!keepFault) state.serveFault = 0;
  state.rallyCount = 0;

  // Lato di battuta: punti pari = destra (deuce court), dispari = sinistra (ad court).
  // In vantaggio si serve sempre dal lato ad (sinistra).
  const s = state.score;
  let parity;
  if (s.tiebreak) parity = (s.tbPoints.A + s.tbPoints.B) % 2;
  else if (s.advantage) parity = 1;
  else parity = (s.points.A + s.points.B) % 2;
  state.serveSide = parity === 0 ? 'right' : 'left';

  // Sistema di riferimento globale: team A al fondo z<0 guarda +z, team B z>0 guarda -z.
  const serverFace = sv.team === 'A' ? 1 : -1;     // direzione verso cui serve (segno z avversario)
  // "destra" del servitore in x globale: per A è +x, per B è -x.
  const rightSign = serverFace;
  // Posizione di battuta: lato deuce/ad a ~1.9m dal centro, dietro la propria baseline.
  const standSign = state.serveSide === 'right' ? rightSign : -rightSign;
  sv.x = standSign * 1.9;
  sv.z = serverFace > 0 ? -(COURT.BASELINE_Z + 0.15) : (COURT.BASELINE_Z + 0.15);
  sv.vx = 0; sv.vz = 0; sv.targetVx = 0; sv.targetVz = 0;
  // azzera le finestre di colpo a inizio punto (niente cerchio verde "fantasma")
  for (const pl of Object.values(state.players)) pl.canHitUntil = 0;

  // Box di servizio valido (diagonale): x dal lato opposto a dove sta il servitore,
  // z tra la rete e la riga di servizio avversaria.
  state.serveBox = { xSign: -standSign, zSign: serverFace };

  // palla in mano del servitore
  state.ball = {
    x: sv.x, y: 0.9, z: sv.z,
    vx: 0, vy: 0, vz: 0,
    spin: 0,
    lastHitter: null,
    lastHitterTeam: null,
    crossedNet: false,
    bounces: 0,
    bouncedSide: null,       // 'A' o 'B' dove ha rimbalzato l'ultima volta
    bouncedInService: false, // valida per servizio
    inPlay: false,
    held: true,
    serveValid: false,
    type: 'serve',
    lastBouncePos: null,
    marks: state.ball ? state.ball.marks : [],
  };
}

// serveType: 'flat' | 'slice' | 'kick'. charge in 0..1 (già normalizzato dal client).
function performServe(p, charge, joyAngle, serveType) {
  const ball = state.ball;
  if (!ball || !ball.held || state.serverId !== p.id) return;

  const sv = p;
  const box = state.serveBox || { xSign: 1, zSign: sv.team === 'A' ? 1 : -1 };
  const zSign = box.zSign;          // segno z del campo avversario
  const xSign = box.xSign;          // lato x del box diagonale
  const aimX = (joyAngle ? joyAngle.x : 0); // -1..1 piccola correzione manuale

  const tt = clamp(charge, 0, 1);
  // potenza: cresce con la carica. precisione: massima vicino allo "sweet spot" (~0.8).
  const power = 0.55 + 0.45 * Math.min(1, tt / 0.85);
  const sweet = 0.8;
  const off = Math.abs(tt - sweet);
  const precise = off < 0.16;

  // parametri per tipo di servizio (velocità già ridotte ~40%)
  let base, spin, curve;
  if (serveType === 'slice') {
    base = 25; spin = -0.1; curve = xSign * 0.9;  // taglia di lato, curva verso il box
  } else if (serveType === 'kick') {
    base = 23; spin = 0.7; curve = 0;             // topspin → rimbalzo che salta
  } else { // flat
    base = 29; spin = 0.08; curve = 0;            // veloce, teso
  }
  const speed = base * power * SHOT_SPEED_SCALE;

  // bersaglio dentro il box diagonale
  let targetX = xSign * (COURT.SINGLES_HALF_W * 0.55) + aimX * 1.6;
  let targetZ = zSign * (COURT.SERVICE_Z - 1.0 - Math.random() * 0.8);
  if (!precise) {
    const err = Math.min(1.2, off * 1.5);
    targetX += (Math.random() - 0.5) * 4.2 * err;
    targetZ += zSign * Math.random() * 2.2 * err; // può andare lungo → fallo
  }
  if (tt < 0.4) {
    // carica troppo debole: rischio rete
    targetZ *= 0.35;
  }

  const launchY = 2.5;
  ball.x = sv.x;
  ball.y = launchY;
  ball.z = sv.z + zSign * 0.3;
  const dx = targetX - ball.x;
  const dz = targetZ - ball.z;
  const dist = Math.max(0.5, Math.hypot(dx, dz));
  const ndx = dx / dist, ndz = dz / dist;
  const tFlight = dist / speed;
  // gravità efficace: il topspin aggiunge spinta verso il basso, va compensata nell'arco.
  const gEff = 9.8 + Math.max(0, spin) * 8.0;
  ball.vx = ndx * speed;
  ball.vz = ndz * speed;
  ball.vy = (0.25 - launchY) / tFlight + 0.5 * gEff * tFlight;
  ball.spin = spin;
  ball.curve = curve;
  ball.held = false;
  ball.inPlay = true;
  ball.lastHitter = sv.id;
  ball.lastHitterTeam = sv.team;
  ball.crossedNet = false;
  ball.bounces = 0;
  ball.bouncedSide = null;
  ball.bouncedInService = false;
  ball.serveValid = true;
  ball.type = 'serve';
  state.phase = 'rally';
  state.score.lastServeKmh = Math.round(speed * 3.6);
  state.events.push({ type: 'serveHit', kmh: state.score.lastServeKmh, id: sv.id, serveType: serveType || 'flat' });
  pushHitEffect(sv, tt, precise ? 'perfect' : 'good', 'serve');
}

// ---------------------------------------------------------------------------
// Colpi durante rally
// ---------------------------------------------------------------------------
function performShot(p, shotType, charge, joyAngle, useSuper) {
  const ball = state.ball;
  if (!ball || !ball.inPlay || ball.held) return;
  // distanza orizzontale palla-giocatore
  const dx = ball.x - p.x;
  const dz = ball.z - p.z;
  const distH = Math.hypot(dx, dz);

  // reach: colpibile se la palla è nella hit zone ADESSO, oppure se lo è stata
  // negli ultimi HIT_GRACE_MS (così c'è una finestra di almeno 0.5s, non solo l'istante esatto).
  const REACH = HIT_REACH;
  const inZoneNow = distH <= REACH;
  const inGrace = p.canHitUntil && Date.now() < p.canHitUntil;
  if (!inZoneNow && !inGrace) {
    // colpo a vuoto
    state.events.push({ type: 'miss', id: p.id });
    p.lastSwing = Date.now();
    return;
  }
  // altezza raggiungibile (smash possibile se palla alta)
  const reachableY = ball.y < 3.6;
  const isSmash = ball.y > 4.0 && shotType === 'drive';
  if (!reachableY && !isSmash) {
    state.events.push({ type: 'miss', id: p.id });
    return;
  }

  // timing: in base alla distanza dal centro player
  let timing;
  const tightWin = Math.max(0.35, 0.7 - state.rallyCount * 0.02);
  if (distH < tightWin) timing = 'perfect';
  else if (distH < REACH * 0.85) timing = 'good';
  else timing = 'late';

  // applica super
  const teamEnergyFull = state.energy[p.team] >= 1.0 && useSuper;
  if (teamEnergyFull) {
    state.energy[p.team] = 0;
  }

  // direzione verso campo avversario
  const opp = p.team === 'A' ? 1 : -1;
  // target base
  let targetX = (joyAngle ? joyAngle.x : 0) * COURT.DOUBLES_HALF_W * 0.85;
  let targetZ = opp * (COURT.BASELINE_Z * 0.85);
  let height = 0.9; // altezza di atterraggio desiderata
  let speed = 16;
  let vyBoost = 0;
  let spin = 0;
  let shotName = shotType;

  // velocità ridotte ~40% e fortemente dipendenti dalla carica:
  // colpo leggero (charge~0) = palla lenta, colpo carico (charge~1) = palla veloce.
  if (isSmash) {
    shotName = 'smash';
    speed = 24 + charge * 8;
    targetZ = opp * (COURT.SERVICE_Z + 1.5 + Math.random() * 2);
    height = 0.1;
    spin = 0.8;
  } else if (shotType === 'drive') {
    speed = 14 + charge * 12;
    height = 0.4 + Math.random() * 0.4;
    spin = 0.6;
  } else if (shotType === 'lob') {
    speed = 10 + charge * 4;
    targetZ = opp * (COURT.BASELINE_Z * 0.85 - Math.random() * 1.5);
    height = 0.9;
    vyBoost = 2; // l'arco alto nasce già dalla bassa velocità (tempo di volo lungo)
    spin = 0.3;
  } else if (shotType === 'drop') {
    speed = 8 + charge * 2; // colpo di tocco: resta morbido anche caricato
    targetZ = opp * (1.8 + Math.random() * 1.0);
    height = 0.35;
    spin = -0.8; // backspin
  } else if (shotType === 'slice') {
    speed = 13 + charge * 6;
    height = 0.3;
    spin = -0.6;
  }

  // bonus super
  if (teamEnergyFull) {
    speed *= 1.8;
    spin *= 1.2;
  }

  // perfect → maggiore precisione e potenza
  if (timing === 'perfect') {
    speed *= 1.15;
    targetX += (joyAngle ? joyAngle.x : 0) * 1.2;
  } else if (timing === 'late') {
    speed *= 0.65;
    // rischio rete o out
    if (Math.random() < 0.4) {
      // tiro debole verso rete
      targetZ *= 0.25;
      height = 0.05;
    } else {
      // out
      targetZ *= 1.3;
    }
  }

  // limiti
  targetX = Math.max(-COURT.DOUBLES_HALF_W - 1.5, Math.min(COURT.DOUBLES_HALF_W + 1.5, targetX));

  // -50% globale: palla più lenta e controllabile (l'arco si adatta per restare nel campo)
  speed *= SHOT_SPEED_SCALE;

  // calcola velocità per arrivare al target
  const dxT = targetX - p.x;
  const dzT = targetZ - p.z;
  const distT = Math.max(0.5, Math.hypot(dxT, dzT));
  const ndx = dxT / distT, ndz = dzT / distT;
  const tFlight = distT / speed;
  // gravità efficace: topspin (spin>0) accorcia, backspin (spin<0) allunga.
  // Compensiamo nell'arco così la palla atterra vicino al bersaglio voluto.
  const gEff = Math.max(4, 9.8 + spin * 8.0);
  const launchY = isSmash ? 2.6 : 1.2;
  ball.x = p.x + ndx * 0.6;
  ball.y = launchY;
  ball.z = p.z + ndz * 0.6;
  ball.vx = ndx * speed;
  ball.vz = ndz * speed;
  ball.vy = (height - launchY) / tFlight + 0.5 * gEff * tFlight + vyBoost;
  ball.spin = spin;
  ball.curve = shotName === 'slice' ? (joyAngle && joyAngle.x ? Math.sign(joyAngle.x) * 1.2 : 0) : 0;
  ball.lastHitter = p.id;
  ball.lastHitterTeam = p.team;
  ball.crossedNet = false;
  ball.bounces = 0;
  ball.bouncedSide = null;
  ball.type = shotName;
  ball.isSuper = teamEnergyFull;
  state.rallyCount++;
  pushHitEffect(p, charge, timing, shotName, teamEnergyFull);

  // energy gain
  const gain = timing === 'perfect' ? 0.18 : timing === 'good' ? 0.09 : 0.03;
  state.energy[p.team] = Math.min(1, state.energy[p.team] + gain);
  p.lastSwing = Date.now();
  p.lastTiming = timing;
}

function pushHitEffect(p, charge, timing, shotName, isSuper) {
  state.events.push({
    type: 'hit',
    id: p.id,
    x: p.x, z: p.z,
    timing: timing || 'good',
    shot: shotName || 'serve',
    super: !!isSuper,
    charge: charge || 0,
  });
  if (timing === 'perfect') {
    state.events.push({ type: 'perfect', id: p.id, x: p.x, z: p.z });
  }
}

// ---------------------------------------------------------------------------
// Fisica palla
// ---------------------------------------------------------------------------
function stepBall(dt) {
  const ball = state.ball;
  if (!ball || ball.held) return;

  // air drag + magnus
  const dragK = AIR_DRAG;
  const v = Math.hypot(ball.vx, ball.vy, ball.vz);
  ball.vx -= ball.vx * dragK * v * dt;
  ball.vy -= ball.vy * dragK * v * dt;
  ball.vz -= ball.vz * dragK * v * dt;
  // gravity
  ball.vy -= 9.8 * dt;
  // magnus: topspin spinge giù; backspin spinge su
  ball.vy -= ball.spin * 8.0 * dt;
  // sidespin (slice): curva laterale mentre la palla è in volo
  if (ball.curve) {
    ball.vx += ball.curve * 5.0 * dt;
    ball.curve *= (1 - dt * 0.5);
  }

  ball.x += ball.vx * dt;
  ball.y += ball.vy * dt;
  ball.z += ball.vz * dt;

  // rete: piano a z=0, altezza ≈ NET_H_CENTER, larghezza fino a sideline doubles
  const prevCrossed = ball.crossedNet;
  if (Math.abs(ball.x) < COURT.DOUBLES_HALF_W + COURT.NET_OVERHANG && ball.y < COURT.NET_H_CENTER + 0.15 && Math.sign(ball.vz) !== 0) {
    // se attraversa z=0 e palla sotto rete → colpisce
    // controllo: il segno di z è cambiato in questo step?
    const zPrev = ball.z - ball.vz * dt;
    if (zPrev * ball.z < 0 && ball.y < COURT.NET_H_CENTER) {
      // hit net
      ball.vy = Math.abs(ball.vy) * 0.3;
      ball.vz = -ball.vz * 0.2;
      ball.y = Math.max(ball.y, 0.05);
      state.events.push({ type: 'net', x: ball.x, y: ball.y, z: 0 });
      // se non era ancora attraversato → punto avversario
      if (!ball.crossedNet) {
        endPointToOpponent(ball.lastHitterTeam, 'rete');
        return;
      }
    }
  }
  // detect crossing net (z change of sign)
  const zPrev2 = ball.z - ball.vz * dt;
  if (zPrev2 * ball.z < 0) ball.crossedNet = true;

  // rimbalzo a terra
  if (ball.y <= 0.034) {
    ball.y = 0.034;
    const prof = COURT_PROFILES[state.court];
    // record mark
    ball.lastBouncePos = { x: ball.x, z: ball.z, t: Date.now() };
    ball.marks = ball.marks || [];
    ball.marks.push({ x: ball.x, z: ball.z, t: Date.now() });
    if (ball.marks.length > 30) ball.marks.shift();

    // verifica IN/OUT
    const inBounds = ballInBounds(ball);
    const sideOfBounce = ball.z < 0 ? 'A' : 'B';

    // se primo rimbalzo dopo servizio → deve essere nel box di servizio diagonale
    if (ball.type === 'serve' && ball.bounces === 0) {
      const box = state.serveBox || { xSign: 1, zSign: 1 };
      const ok = isInServiceBox(ball.x, ball.z, box.zSign, box.xSign);
      if (!ok) {
        // fault
        handleFault();
        return;
      } else {
        ball.bouncedInService = true;
      }
    } else if (!inBounds) {
      // out
      state.events.push({ type: 'out', x: ball.x, z: ball.z });
      endPointToOpponent(ball.lastHitterTeam, 'out');
      return;
    }

    // bounce
    ball.bounces++;
    ball.bouncedSide = sideOfBounce;
    ball.vy = -ball.vy * prof.restY;
    ball.vx *= prof.fricH * GROUND_FRIC_SCALE;
    ball.vz *= prof.fricH * GROUND_FRIC_SCALE;
    // spin influisce: topspin → vz aumenta (kick), vy ridotta; backspin → vz ridotta, salta basso
    ball.vz += ball.spin * 4.0;
    ball.vy *= (1 - Math.max(0, ball.spin) * 0.15);
    ball.spin *= 0.6;

    state.events.push({ type: 'bounce', x: ball.x, z: ball.z, court: state.court });

    if (ball.bounces >= 2) {
      // due rimbalzi sullo stesso lato → punto avversario di chi ha rimbalzato (lato di rimbalzo perde)
      // chi non riesce a rispondere perde: la palla rimbalza nel campo di chi doveva rispondere
      const losingTeam = sideOfBounce; // squadra del lato dove è caduta
      const winning = losingTeam === 'A' ? 'B' : 'A';
      state.events.push({ type: 'point', team: winning, reason: '2 rimbalzi' });
      awardPoint(winning);
      return;
    }
  }

  // palla esce dietro senza rimbalzare entro 1 secondo dal limite — non gestiamo
  // se va molto fuori senza rimbalzare → out
  if (Math.abs(ball.z) > 20 || Math.abs(ball.x) > 14 || ball.y > 25) {
    endPointToOpponent(ball.lastHitterTeam, 'fuori');
  }
}

function ballInBounds(b) {
  // singoli o doppi? Se 2v2/4v4 usiamo doubles
  const useDoubles = activePlayers().length >= 4;
  const hw = useDoubles ? COURT.DOUBLES_HALF_W : COURT.SINGLES_HALF_W;
  return Math.abs(b.x) <= hw + 0.05 && Math.abs(b.z) <= COURT.BASELINE_Z + 0.05;
}

function isInServiceBox(x, z, zSign, xSign) {
  // zSign: segno z del campo avversario; xSign: lato x del box diagonale.
  const inZ = zSign > 0 ? (z > 0.05 && z <= COURT.SERVICE_Z) : (z < -0.05 && z >= -COURT.SERVICE_Z);
  if (!inZ) return false;
  if (Math.abs(x) > COURT.SINGLES_HALF_W) return false;
  // deve cadere sul lato x corretto (con piccola tolleranza al centro)
  return xSign > 0 ? x > -0.1 : x < 0.1;
}

function handleFault() {
  state.serveFault++;
  state.events.push({ type: 'fault', n: state.serveFault });
  if (state.serveFault >= 2) {
    // doppio fallo: punto all'avversario del servitore
    const sv = state.players[state.serverId];
    if (sv) {
      const opp = sv.team === 'A' ? 'B' : 'A';
      state.events.push({ type: 'doubleFault', team: opp });
      awardPoint(opp);
    } else {
      setupServe();
    }
  } else {
    // seconda di servizio: ripristina la palla ma conserva il conteggio falli
    setupServe(true);
    state.phase = 'serving';
  }
}

function endPointToOpponent(hitterTeam, reason) {
  if (!hitterTeam) return;
  const winner = hitterTeam === 'A' ? 'B' : 'A';
  state.events.push({ type: 'point', team: winner, reason });
  awardPoint(winner);
}

function awardPoint(team) {
  // aggiungi al punteggio + reset rally
  state.phase = 'pointEnd';
  state.pointTimer = 1.6;
  state.events.push({ type: 'pointWon', team });
  // energia
  state.energy[team] = Math.min(1, state.energy[team] + 0.18);
  // applica al punteggio
  addPoint(team);
}

// ---------------------------------------------------------------------------
// Movimento giocatori
// ---------------------------------------------------------------------------
function stepPlayers(dt) {
  const prof = COURT_PROFILES[state.court];
  for (const p of Object.values(state.players)) {
    // Il servitore resta fermo durante la fase di servizio: non si muove da solo.
    if (state.phase === 'serving' && p.id === state.serverId) {
      p.vx = 0; p.vz = 0; p.targetVx = 0; p.targetVz = 0;
      p.slide = Math.max(0, p.slide - dt * 2);
      p.swingT = Math.max(0, (p.swingT || 0) - dt * 4);
      continue;
    }
    // target velocity da joystick (+30% di velocità massima del giocatore)
    const maxSpeed = prof.maxSpeed * PLAYER_SPEED_SCALE;
    const tvx = p.targetVx * maxSpeed * (p.stamina < 0.2 ? 0.6 : 1);
    const tvz = p.targetVz * maxSpeed * (p.stamina < 0.2 ? 0.6 : 1);

    // smoothing esponenziale: accelera/decelera in modo morbido e "pesante".
    // In accelerazione usiamo ACCEL_TAU (~0.15s per la velocità max) per una risposta pronta ma graduale.
    const moving = (Math.abs(tvx) + Math.abs(tvz)) > 0.05;
    const tau = moving ? ACCEL_TAU : prof.decelTau;
    const k = 1 - Math.exp(-dt / tau);
    p.vx += (tvx - p.vx) * k;
    p.vz += (tvz - p.vz) * k;

    // slide: rilevamento cambio direzione brusco
    p.slide = Math.max(0, p.slide - dt * 2);
    const dot = (p.lastTargetVx || 0) * tvx + (p.lastTargetVz || 0) * tvz;
    if (dot < -0.4 && (Math.abs(p.vx) + Math.abs(p.vz)) > 2.0) {
      p.slide = Math.min(1, p.slide + prof.slide * 0.6);
    }
    p.lastTargetVx = tvx; p.lastTargetVz = tvz;

    p.x += p.vx * dt;
    p.z += p.vz * dt;

    // limiti campo (margine)
    const limX = COURT.DOUBLES_HALF_W + 2.5;
    const limZ = COURT.BASELINE_Z + 3.0;
    p.x = clamp(p.x, -limX, limX);
    p.z = clamp(p.z, -limZ, limZ);

    // stamina
    const running = Math.hypot(p.vx, p.vz) > 4.0;
    const charging = p.charging;
    if (running) p.stamina -= dt * 0.06;
    if (charging) p.stamina -= dt * 0.12;
    // recupero
    const inBack = Math.abs(p.z) > COURT.BASELINE_Z - 1.5;
    if (!running && !charging) p.stamina += dt * (inBack ? 0.18 : 0.08);
    p.stamina = clamp(p.stamina, 0, 1);

    // anim swing decay
    p.swingT = Math.max(0, (p.swingT || 0) - dt * 4);
  }
}

function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }

// Aggiorna, per ogni giocatore, l'istante fino al quale la palla resta "colpibile".
// Quando la palla entra nella hit zone (raggio HIT_REACH, altezza raggiungibile) la
// finestra viene estesa di HIT_GRACE_MS: il giocatore può colpire per almeno 0.5s,
// non solo nell'istante esatto. Il flag canHit alimenta il cerchio verde lato client.
function updateHitWindows() {
  const ball = state.ball;
  if (!ball || !ball.inPlay || ball.held) return;
  const now = Date.now();
  for (const p of Object.values(state.players)) {
    const distH = Math.hypot(ball.x - p.x, ball.z - p.z);
    if (distH <= HIT_REACH && ball.y < 3.6) {
      p.canHitUntil = now + HIT_GRACE_MS;
    }
  }
}

// ---------------------------------------------------------------------------
// Game loop
// ---------------------------------------------------------------------------
let pointEndTimer = 0;
function tick() {
  const dt = DT;

  // pointEnd cooldown
  if (state.phase === 'pointEnd') {
    state.pointTimer -= dt;
    stepPlayers(dt);
    if (state.pointTimer <= 0) {
      if (!state.score.matchOver) {
        setupServe();
      }
    }
  } else if (state.phase === 'rally' || state.phase === 'serving') {
    stepPlayers(dt);
    if (state.phase === 'rally') { stepBall(dt); updateHitWindows(); }
    // se serving, la palla è in mano
    if (state.phase === 'serving' && state.ball) {
      const sv = state.players[state.serverId];
      if (sv) {
        state.ball.x = sv.x;
        state.ball.y = 0.9;
        state.ball.z = sv.z;
      }
    }
  } else if (state.phase === 'lobby') {
    stepPlayers(dt);
  } else if (state.phase === 'gameEnd') {
    stepPlayers(dt);
  }
}

setInterval(tick, 1000 / TICK_HZ);

// broadcast
setInterval(() => {
  const snap = {
    t: Date.now(),
    phase: state.phase,
    court: state.court,
    mode: gameMode(),
    hitReach: HIT_REACH,
    serverId: state.serverId,
    serveSide: state.serveSide,
    serveBox: state.serveBox,
    serveFault: state.serveFault,
    rallyCount: state.rallyCount,
    energy: state.energy,
    score: state.score,
    players: Object.values(state.players).map(p => ({
      id: p.id, name: p.name, team: p.team, color: p.color,
      x: round(p.x), z: round(p.z),
      vx: round(p.vx), vz: round(p.vz),
      stamina: round(p.stamina, 1000),
      slide: round(p.slide, 1000),
      swingT: round(p.swingT, 1000),
      charging: !!p.charging,
      chargeT: round(p.chargeT, 1000),
      canHit: !!(p.canHitUntil && Date.now() < p.canHitUntil),
      emote: p.emote && (Date.now() - p.emote.t < 2200) ? p.emote : null,
      lastTiming: p.lastTiming || null,
    })),
    ball: state.ball ? {
      x: round(state.ball.x), y: round(state.ball.y), z: round(state.ball.z),
      vx: round(state.ball.vx), vy: round(state.ball.vy), vz: round(state.ball.vz),
      spin: round(state.ball.spin, 100),
      type: state.ball.type, isSuper: !!state.ball.isSuper,
      held: !!state.ball.held,
      lastHitter: state.ball.lastHitter,
      marks: (state.ball.marks || []).slice(-12).map(m => ({ x: round(m.x), z: round(m.z), t: m.t })),
    } : null,
    events: state.events,
  };
  io.emit('state', snap);
  state.events = [];
}, 1000 / NET_HZ);

function round(v, p = 100) { return Math.round((v || 0) * p) / p; }

// ---------------------------------------------------------------------------
// Socket.io
// ---------------------------------------------------------------------------
io.on('connection', (socket) => {
  // lobby info
  socket.emit('hello', {
    id: socket.id,
    court: state.court,
    courts: Object.keys(COURT_PROFILES),
    canPickCourt: activePlayers().length === 0,
    playerCount: activePlayers().length,
  });

  socket.on('join', ({ name, court }) => {
    if (state.players[socket.id]) return;
    if (activePlayers().length === 0 && court && COURT_PROFILES[court]) {
      state.court = court;
    }
    const team = assignTeam();
    const colorIdx = activePlayers().length % COLORS.length;
    const p = {
      id: socket.id,
      name: (name || 'Player ' + state.nameCounter).slice(0, 14),
      team, color: COLORS[colorIdx],
      x: 0, z: 0, vx: 0, vz: 0,
      targetVx: 0, targetVz: 0,
      stamina: 1.0, slide: 0,
      swingT: 0, lastSwing: 0,
      canHitUntil: 0,
      charging: false, chargeT: 0,
      emote: null,
      lastTiming: null,
    };
    state.nameCounter++;
    state.players[socket.id] = p;
    placePlayer(p);
    rebalanceTeams();
    if (!state.serverId) state.serverId = socket.id;
    socket.emit('joined', { id: socket.id, team, color: p.color });
    io.emit('lobby', { count: activePlayers().length, court: state.court, mode: gameMode() });

    // se abbiamo almeno 2 giocatori e siamo in lobby → inizia
    if (activePlayers().length >= 2 && state.phase === 'lobby') {
      resetMatch();
      state.phase = 'serving';
      setupServe();
    }
  });

  socket.on('setCourt', ({ court }) => {
    if (COURT_PROFILES[court] && (activePlayers().length === 0 || activePlayers().length === 1 && activePlayers()[0].id === socket.id) ) {
      state.court = court;
      io.emit('lobby', { count: activePlayers().length, court: state.court, mode: gameMode() });
    }
  });

  socket.on('input', ({ mx, mz }) => {
    const p = state.players[socket.id];
    if (!p) return;
    const m = Math.hypot(mx || 0, mz || 0);
    if (m > 1) { mx = mx / m; mz = mz / m; }
    p.targetVx = mx || 0;
    p.targetVz = mz || 0;
  });

  socket.on('chargeStart', () => {
    const p = state.players[socket.id];
    if (!p) return;
    p.charging = true; p.chargeT = 0;
  });
  socket.on('chargeTick', ({ t }) => {
    const p = state.players[socket.id];
    if (!p) return;
    p.chargeT = Math.min(1.5, t);
  });

  socket.on('shot', ({ shot, charge, angle, useSuper, serveType }) => {
    const p = state.players[socket.id];
    if (!p) return;
    p.charging = false;
    p.chargeT = 0;
    p.swingT = 1.0;
    const ch = clamp((charge || 0) / 1.5, 0, 1);
    if (state.phase === 'serving' && state.serverId === socket.id) {
      performServe(p, ch, angle || { x: 0, y: 0 }, serveType || 'flat');
    } else if (state.phase === 'rally') {
      performShot(p, shot, ch, angle || { x: 0, y: 0 }, !!useSuper);
    }
  });

  socket.on('emote', ({ key }) => {
    const p = state.players[socket.id];
    if (!p) return;
    p.emote = { key, t: Date.now() };
  });

  socket.on('disconnect', () => {
    const wasServer = state.serverId === socket.id;
    delete state.players[socket.id];
    rebalanceTeams();
    if (wasServer) rotateServer();
    if (activePlayers().length === 0) {
      state.phase = 'lobby';
      state.ball = null;
      state.serverId = null;
      state.score = createScoreState();
    } else if (activePlayers().length === 1 && state.phase !== 'lobby') {
      state.phase = 'lobby';
      state.ball = null;
    }
    io.emit('lobby', { count: activePlayers().length, court: state.court, mode: gameMode() });
  });
});
