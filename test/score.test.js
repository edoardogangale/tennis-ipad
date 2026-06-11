// ============================================================================
// Test della logica punteggio e attribuzione punti.
// Esegui con: npm test  (oppure: TENNIS_TEST=1 PORT=0 node test/score.test.js)
// ============================================================================
process.env.TENNIS_TEST = '1';
process.env.PORT = process.env.PORT || '0'; // porta casuale: non confligge col server vero

const g = require('../index.js');
const { state, COURT, addPoint, pointLabels, stepBall, handleFault, createScoreState } = g;

let passed = 0, failed = 0;
function check(name, cond, detail) {
  if (cond) { passed++; console.log(`  ✓ ${name}`); }
  else { failed++; console.error(`  ✗ ${name}${detail ? ' — ' + detail : ''}`); }
}

function fakePlayers() {
  state.players = {
    p1: { id: 'p1', name: 'Anna', team: 'A', x: 0, z: -11, vx: 0, vz: 0, targetVx: 0, targetVz: 0, stamina: 1, slide: 0, swingT: 0, charging: false, chargeT: 0, canHitUntil: 0 },
    p2: { id: 'p2', name: 'Bea', team: 'B', x: 0, z: 11, vx: 0, vz: 0, targetVx: 0, targetVz: 0, stamina: 1, slide: 0, swingT: 0, charging: false, chargeT: 0, canHitUntil: 0 },
  };
  state.serverId = 'p1';
}

function resetScore() {
  state.score = createScoreState();
  state.events = [];
  state.serveFault = 0;
  state.phase = 'rally';
}

// Palla generica in volo durante il rally
function rallyBall(over) {
  state.ball = Object.assign({
    x: 0, y: 0.2, z: 8, vx: 0, vy: -3, vz: 2,
    spin: 0, curve: 0, bounces: 0, crossedNet: true,
    lastHitter: 'p1', lastHitterTeam: 'A',
    held: false, inPlay: true, type: 'drive',
    bouncedSide: null, bouncedInService: false, marks: [],
  }, over);
}

function lastPointTeam() {
  const ev = state.events.filter(e => e.type === 'pointWon').pop();
  return ev ? ev.team : null;
}

function stepUntilPoint(maxSteps = 600) {
  for (let i = 0; i < maxSteps; i++) {
    stepBall(1 / 60);
    if (state.events.some(e => e.type === 'pointWon' || e.type === 'fault')) return;
  }
}

console.log('\n— Sequenza punti 0 → 15 → 30 → 40 → Game —');
fakePlayers(); resetScore();
addPoint('A'); check('15-0', pointLabels().A === '15' && pointLabels().B === '0');
addPoint('A'); check('30-0', pointLabels().A === '30');
addPoint('A'); check('40-0', pointLabels().A === '40');
addPoint('A'); check('game A: games 1-0, punti azzerati', state.score.games.A === 1 && state.score.points.A === 0 && state.score.points.B === 0);

console.log('\n— Deuce e Vantaggio —');
resetScore();
addPoint('A'); addPoint('B'); addPoint('A'); addPoint('B'); addPoint('A'); addPoint('B');
check('40-40 = deuce', state.score.deuce === true && pointLabels().A === '40' && pointLabels().B === '40');
addPoint('A');
check('vantaggio A', state.score.advantage === 'A' && pointLabels().A === 'AD');
addPoint('B');
check('si torna in parità (deuce)', state.score.deuce === true && state.score.advantage === null);
addPoint('B'); addPoint('B');
check('game B dal vantaggio', state.score.games.B === 1 && state.score.advantage === null);

console.log('\n— Palla OUT: il punto va all\'avversario di chi ha colpito —');
fakePlayers(); resetScore();
rallyBall({ z: 11.5, x: 7.0, y: 0.5, vy: -4, vz: 1, lastHitterTeam: 'A' }); // cadrà larga (x oltre la riga)
stepUntilPoint();
check('out di A → punto a B', lastPointTeam() === 'B', `vincitore=${lastPointTeam()}`);

console.log('\n— Palla in rete: punto all\'avversario —');
fakePlayers(); resetScore();
rallyBall({ z: -0.4, y: 0.4, vy: 0, vz: 3, crossedNet: false, lastHitterTeam: 'A', type: 'drive' });
stepUntilPoint();
check('rete di A → punto a B', lastPointTeam() === 'B', `vincitore=${lastPointTeam()}`);

console.log('\n— Doppio rimbalzo nel campo avversario: punto a chi ha colpito —');
fakePlayers(); resetScore();
rallyBall({ z: 8, y: 0.3, vy: -2, vz: 0.5, bounces: 1, lastHitterTeam: 'A' }); // già rimbalzata una volta lato B
stepUntilPoint();
check('2 rimbalzi lato B → punto ad A', lastPointTeam() === 'A', `vincitore=${lastPointTeam()}`);

console.log('\n— Secondo rimbalzo FUORI dal campo (primo era valido): punto comunque a chi ha colpito —');
fakePlayers(); resetScore();
rallyBall({ z: 12.6, y: 0.3, vy: -2, vz: 1.5, bounces: 1, lastHitterTeam: 'A' }); // secondo rimbalzo oltre la baseline
stepUntilPoint();
check('secondo rimbalzo out → punto ad A (non a B!)', lastPointTeam() === 'A', `vincitore=${lastPointTeam()}`);

console.log('\n— Palla nel proprio campo (non supera la rete): punto all\'avversario —');
fakePlayers(); resetScore();
rallyBall({ z: -5, y: 0.2, vy: -3, vz: 0.5, crossedNet: false, lastHitterTeam: 'A' });
stepUntilPoint();
check('rimbalzo nel campo di A senza superare la rete → punto a B', lastPointTeam() === 'B', `vincitore=${lastPointTeam()}`);

console.log('\n— Servizio in rete = FALLO (non punto) e doppio fallo = punto al ricevitore —');
fakePlayers(); resetScore();
state.serveBox = { xSign: 1, zSign: 1 };
rallyBall({ z: -0.3, y: 0.4, vy: 0, vz: 3, crossedNet: false, lastHitterTeam: 'A', type: 'serve' });
stepUntilPoint();
check('prima di servizio in rete → fallo, nessun punto', state.serveFault === 1 && lastPointTeam() === null, `fault=${state.serveFault} punto=${lastPointTeam()}`);
state.events = [];
state.phase = 'rally';
rallyBall({ z: -0.3, y: 0.4, vy: 0, vz: 3, crossedNet: false, lastHitterTeam: 'A', type: 'serve' });
stepUntilPoint();
check('doppio fallo → punto a B', lastPointTeam() === 'B' && pointLabels().B === '15', `vincitore=${lastPointTeam()} label=${pointLabels().B}`);

console.log('\n— Servizio fuori dal box = fallo —');
fakePlayers(); resetScore();
state.serveBox = { xSign: 1, zSign: 1 };
rallyBall({ x: -3, z: 5, y: 0.2, vy: -3, vz: 1, lastHitterTeam: 'A', type: 'serve', crossedNet: true }); // x lato sbagliato del box
stepUntilPoint();
check('serve fuori box → fallo', state.serveFault === 1 && lastPointTeam() === null, `fault=${state.serveFault}`);

console.log('\n— Palla che vola fuori arena dopo rimbalzo valido: punto a chi ha colpito —');
fakePlayers(); resetScore();
rallyBall({ z: 19.9, y: 3, vy: 1, vz: 8, bounces: 1, lastHitterTeam: 'A' });
stepUntilPoint();
check('fuga oltre arena con rimbalzo valido → punto ad A', lastPointTeam() === 'A', `vincitore=${lastPointTeam()}`);

console.log(`\n========== RISULTATO: ${passed} ok, ${failed} falliti ==========`);
process.exit(failed > 0 ? 1 : 0);
