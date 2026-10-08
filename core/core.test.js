/* Unit test cho Core TFTP — chay: node core/core.test.js (khong can npm install) */
const assert = require('assert');
const P = require('./tftpPackets.js');
const { TftpSimulator } = require('./fsmEngine.js');

let pass = 0;
function ok(name, fn) {
  try { fn(); pass++; console.log('PASS ' + name); }
  catch (e) { console.error('FAIL ' + name + ' -> ' + e.message); process.exitCode = 1; }
}

ok('RRQ encode/decode tron vong', () => {
  const b = P.encodeRRQ('test.txt', 'octet');
  assert.strictEqual(b[0], 0); assert.strictEqual(b[1], 1);
  const d = P.decode(b);
  assert.strictEqual(d.type, 'RRQ');
  assert.strictEqual(d.filename, 'test.txt');
  assert.strictEqual(d.mode, 'octet');
});

ok('WRQ encode/decode', () => {
  const b = P.encodeWRQ('up.bin', 'octet');
  const d = P.decode(b);
  assert.strictEqual(d.type, 'WRQ');
  assert.strictEqual(d.filename, 'up.bin');
});

ok('DATA encode/decode + phat hien goi cuoi <512B', () => {
  const full = P.encodeDATA(1, new Uint8Array(512));
  const d1 = P.decode(full);
  assert.strictEqual(d1.block, 1); assert.strictEqual(d1.isFinal, false);
  const last = P.encodeDATA(3, new Uint8Array(476));
  const d2 = P.decode(last);
  assert.strictEqual(d2.isFinal, true);
  const zero = P.encodeDATA(4, new Uint8Array(0));
  assert.strictEqual(P.decode(zero).isFinal, true);
});

ok('DATA qua 512B phai nem loi', () => {
  assert.throws(() => P.encodeDATA(1, new Uint8Array(513)));
});

ok('ACK encode/decode', () => {
  const b = P.encodeACK(7);
  const d = P.decode(b);
  assert.strictEqual(d.type, 'ACK'); assert.strictEqual(d.block, 7);
});

ok('ERROR encode/decode File not found', () => {
  const b = P.encodeERROR(1, 'File not found');
  const d = P.decode(b);
  assert.strictEqual(d.type, 'ERROR'); assert.strictEqual(d.errorCode, 1);
});

ok('Opcode la phai nem loi', () => {
  assert.throws(() => P.decode(new Uint8Array([0, 9])));
});

ok('blockPlan: 1500B -> 3 block (512+512+476)', () => {
  const plan = P.blockPlan(1500);
  assert.deepStrictEqual(plan.map(p => p.size), [512, 512, 476]);
});

ok('blockPlan: 1024B (chia het 512) -> them goi 0-byte', () => {
  const plan = P.blockPlan(1024);
  assert.deepStrictEqual(plan.map(p => p.size), [512, 512, 0]);
});

ok('blockPlan: file rong -> 1 goi 0-byte', () => {
  const plan = P.blockPlan(0);
  assert.deepStrictEqual(plan.map(p => p.size), [0]);
});

ok('FSM download chuan: co RRQ, TID_SWITCH, DATA/ACK, ket thuc <512B', () => {
  const sim = new TftpSimulator({ filename: 'a.txt', fileSize: 1500, direction: 'download' });
  const { events, logs, stats } = sim.run();
  const actions = events.map(e => e.action);
  assert.ok(actions.includes('RRQ'));
  assert.ok(actions.includes('TID_SWITCH'));
  assert.strictEqual(stats.blocks, 3);
  assert.strictEqual(stats.errored, false);
  const lastData = events.filter(e => e.action === 'DATA').pop();
  assert.ok(lastData.isFinal);
  assert.ok(logs.some(l => l.state === 'COMPLETE'));
});

ok('FSM gia lap mat goi: co PACKET_LOSS + TIMEOUT + gui lai', () => {
  const sim = new TftpSimulator({ filename: 'a.txt', fileSize: 1500, direction: 'download', lossBlock: 2 });
  const { events, stats } = sim.run();
  const actions = events.map(e => e.action);
  assert.ok(actions.includes('PACKET_LOSS'));
  assert.ok(actions.includes('TIMEOUT'));
  assert.ok(actions.includes('DATA_RETRANSMIT'));
  assert.strictEqual(stats.timeouts, 1);
});

ok('FSM file khong ton tai: tra ve ERROR 1', () => {
  const sim = new TftpSimulator({ filename: 'nope.txt', fileSize: 500, direction: 'download', fileNotFound: true });
  const { events, stats } = sim.run();
  assert.ok(events.some(e => e.action === 'ERROR' && e.errorCode === 1));
  assert.strictEqual(stats.errored, true);
});

ok('FSM upload WRQ: co WRQ + ACK0 + DATA/ACK', () => {
  const sim = new TftpSimulator({ filename: 'up.txt', fileSize: 600, direction: 'upload' });
  const { events, stats } = sim.run();
  const actions = events.map(e => e.action);
  assert.ok(actions.includes('WRQ'));
  assert.ok(events.some(e => e.action === 'ACK' && e.block === 0));
  assert.strictEqual(stats.blocks, 2); // 512 + 88
});

console.log('\n' + pass + ' test passed.');
