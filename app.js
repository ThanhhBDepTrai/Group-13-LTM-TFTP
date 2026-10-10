/*
 * app.js (TV2): giao diện mô phỏng TFTP: sơ đồ động, bảng điều khiển, Packet Inspector, nhật ký FSM.
 * Dữ liệu lấy từ core của TV1 thông qua UiAdapter.buildSteps() trong coreAdapter.js.
 */
(function () {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  const XC = 120; // vị trí lifeline Client (trên 600 đơn vị rộng)
  const XS = 480; // vị trí lifeline Server
  const VW = 600;
  const ROW = 70; // chiều cao một hàng gói tin
  const NOTE = 48; // chiều cao một hàng ghi chú
  const FLIGHT = 1100; // ms gói tin bay (tốc độ 1.0x)
  const GAP = 350;
  const NOTE_MS = 1100;

  const $ = (s) => document.querySelector(s);
  const svg = $('#ladder');
  const stage = $('#stage');
  const emptyMsg = $('#stage-empty');
  const inspBody = $('#insp-body');
  const logList = $('#log-list');
  const statsBox = $('#stats');
  const btnRRQ = $('#btn-rrq');
  const btnWRQ = $('#btn-wrq');
  const btnPause = $('#btn-pause');
  const btnReset = $('#btn-reset');
  const inName = $('#in-name');
  const selSize = $('#sel-size');
  const cbLoss = $('#cb-loss');
  const cbError = $('#cb-error');
  const rngSpeed = $('#rng-speed');
  const outSpeed = $('#out-speed');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  let runId = 0;
  let running = false;
  let paused = false;
  let y = 24;
  let selected = null;
  let pktLayer = null;
  let lifeC = null;
  let lifeS = null;

  function svgEl(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  const speed = () => parseFloat(rngSpeed.value);

  /* Chạy hoạt ảnh trong ms (đã tính tốc độ, tạm dừng, hủy). */
  function animate(ms, frame, id) {
    const total = reduceMotion ? ms * 0.2 : ms;
    return new Promise((resolve) => {
      let t = 0;
      let last = performance.now();
      function loop(now) {
        if (id !== runId) return resolve(false);
        const dt = now - last;
        last = now;
        if (!paused) t += dt * speed();
        frame(Math.min(t / total, 1));
        if (t >= total) return resolve(true);
        requestAnimationFrame(loop);
      }
      requestAnimationFrame(loop);
    });
  }

  /* ---------- Sơ đồ ---------- */
  function setHeight(h) {
    svg.setAttribute('viewBox', '0 0 ' + VW + ' ' + h);
    lifeC.setAttribute('y2', h);
    lifeS.setAttribute('y2', h);
  }

  function resetDiagram() {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    lifeC = svgEl('line', { x1: XC, x2: XC, y1: 0, y2: 400, class: 'lifeline' }, svg);
    lifeS = svgEl('line', { x1: XS, x2: XS, y1: 0, y2: 400, class: 'lifeline' }, svg);
    pktLayer = svgEl('g', {}, svg);
    y = 24;
    selected = null;
    setHeight(300);
    stage.scrollTop = 0;
  }

  function growTo(bottom) {
    setHeight(Math.max(300, bottom + 40));
    stage.scrollTop = stage.scrollHeight;
  }

  /* Áp dụng một mục {who, state, msg}: đổi trạng thái FSM trên đầu cột và ghi nhật ký. */
  function applyEntry(en) {
    if (!en) return;
    if (en.who === 'client') $('#c-state').textContent = en.state;
    if (en.who === 'server') $('#s-state').textContent = en.state;
    addLog(en.who, en.state, en.msg);
    if (en.also) applyEntry(en.also);
  }

  async function playPacket(step, id) {
    const fromClient = step.from === 'client';
    const x1 = fromClient ? XC : XS;
    const x2 = fromClient ? XS : XC;
    const y1 = y + 6;
    const y2 = y + ROW - 16;

    growTo(y + ROW);
    applyEntry(step.send);

    const g = svgEl('g', {
      class: 'pkt op-' + step.name.toLowerCase(),
      tabindex: 0,
      role: 'button',
      'aria-label': step.label + ', từ cổng ' + step.src + ' đến cổng ' + step.dst + '. Bấm để xem chi tiết.',
    }, pktLayer);
    const line = svgEl('line', { x1, y1, x2: x1, y2: y1, class: 'pkt-line' }, g);
    const hit = svgEl('line', { x1, y1, x2: x1, y2: y1, class: 'pkt-hit' }, g);
    const head = svgEl('circle', { cx: x1, cy: y1, r: 5, class: 'pkt-head' }, g);
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2;
    const lab = svgEl('text', { x: mx, y: my - 6, class: 'pkt-label', opacity: 0 }, g);
    lab.textContent = step.label + (step.retrans ? ' (gửi lại)' : '');
    const sub = svgEl('text', { x: mx, y: my + 11, class: 'pkt-sub', opacity: 0 }, g);
    sub.textContent = step.src + ' \u2192 ' + step.dst + (step.lost ? ' (mất gói)' : '');

    const select = () => selectPacket(step, g);
    g.addEventListener('click', select);
    g.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(); }
    });

    const end = step.lost ? 0.55 : 1;
    const ok = await animate(FLIGHT, (p) => {
      const q = p * end;
      const cx = x1 + (x2 - x1) * q;
      const cy = y1 + (y2 - y1) * q;
      line.setAttribute('x2', cx); line.setAttribute('y2', cy);
      hit.setAttribute('x2', cx); hit.setAttribute('y2', cy);
      head.setAttribute('cx', cx); head.setAttribute('cy', cy);
      if (p > 0.2) { lab.setAttribute('opacity', 1); sub.setAttribute('opacity', 1); }
    }, id);
    if (!ok) return false;

    const ex = x1 + (x2 - x1) * end;
    const ey = y1 + (y2 - y1) * end;
    head.remove();
    if (step.lost) {
      g.classList.add('lost');
      svgEl('line', { x1: ex - 7, y1: ey - 7, x2: ex + 7, y2: ey + 7, class: 'pkt-x' }, g);
      svgEl('line', { x1: ex - 7, y1: ey + 7, x2: ex + 7, y2: ey - 7, class: 'pkt-x' }, g);
    } else {
      const a = Math.atan2(y2 - y1, x2 - x1);
      const ux = Math.cos(a);
      const uy = Math.sin(a);
      const px = -uy;
      const py = ux;
      const pts = [
        [x2, y2],
        [x2 - 11 * ux + 5 * px, y2 - 11 * uy + 5 * py],
        [x2 - 11 * ux - 5 * px, y2 - 11 * uy - 5 * py],
      ].map((p) => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');
      svgEl('polygon', { points: pts, class: 'pkt-arrow' }, g);
    }
    applyEntry(step.arrive);
    y += ROW;
    return true;
  }

  async function playNote(step, id) {
    growTo(y + NOTE);
    if (step.sPort) $('#s-port').textContent = step.sPort;
    applyEntry(step.log);
    const cy = y + NOTE / 2 - 4;
    const ownerX = step.side === 'client' ? XC : XS;
    svgEl('circle', { cx: ownerX, cy, r: 5, class: 'tick' }, pktLayer);
    const t = svgEl('text', { x: VW / 2, y: cy + 4, class: 'note-text', opacity: 0 }, pktLayer);
    t.textContent = step.text;
    const box = t.getBBox();
    const r = svgEl('rect', {
      x: box.x - 10, y: box.y - 4, width: box.width + 20, height: box.height + 8, rx: 4, class: 'note-box', opacity: 0,
    }, pktLayer);
    pktLayer.insertBefore(r, t);
    const ok = await animate(NOTE_MS, (p) => {
      const o = Math.min(1, p * 4);
      t.setAttribute('opacity', o);
      r.setAttribute('opacity', o);
    }, id);
    if (ok) y += NOTE;
    return ok;
  }

  /* ---------- Packet Inspector ---------- */
  const MEANING = {
    RRQ: 'Client xin đọc (tải về) một file từ Server. Gửi tới cổng 69.',
    WRQ: 'Client xin ghi (tải lên) một file lên Server. Gửi tới cổng 69.',
    ACK: 'Xác nhận đã nhận đúng khối có số hiệu tương ứng. ACK #0 là câu trả lời cho WRQ.',
    ERROR: 'Báo lỗi và kết thúc phiên truyền. Gói ERROR không được ACK và không gửi lại.',
  };

  function selectPacket(step, g) {
    if (selected) selected.classList.remove('selected');
    selected = g;
    g.classList.add('selected');
    const name = step.name;
    const bytes = step.bytes;

    let meaning = MEANING[name] || '';
    if (name === 'DATA') {
      const n = bytes.length - 4;
      meaning = n < 512
        ? 'Chỉ ' + n + ' byte dữ liệu (< 512), đây là gói cuối và báo hiệu hết file.'
        : 'Đủ 512 byte dữ liệu, nên phía nhận hiểu là còn khối tiếp theo.';
    }

    const fieldCls = new Array(bytes.length).fill('');
    step.fields.forEach((f) => {
      for (let i = f.start; i < f.start + f.len && i < bytes.length; i++) fieldCls[i] = f.cls;
    });

    const SHOW = 48;
    let rows = '';
    for (let off = 0; off < Math.min(bytes.length, SHOW); off += 8) {
      let hex = '';
      let asc = '';
      for (let i = off; i < off + 8; i++) {
        if (i < bytes.length && i < SHOW) {
          const b = bytes[i];
          hex += '<span class="' + fieldCls[i] + '">' + b.toString(16).padStart(2, '0') + '</span> ';
          asc += b >= 32 && b < 127 ? esc(String.fromCharCode(b)) : '.';
        } else {
          hex += '   ';
        }
      }
      rows += '<div class="hex-row"><span class="hex-off">' + off.toString(16).padStart(4, '0') + '</span>' +
        '<span class="hex-bytes">' + hex + '</span><span class="hex-ascii">' + asc + '</span></div>';
    }
    const more = bytes.length > SHOW ? '<div class="hex-more">... còn ' + (bytes.length - SHOW) + ' byte nữa</div>' : '';

    const fieldRows = step.fields.map((f) =>
      '<tr><td><span class="' + f.cls + '">' + esc(f.name) + '</span></td>' +
      '<td class="mono">' + f.start + ' (' + f.len + ' B)</td><td>' + esc(f.value) + '</td></tr>').join('');

    const lostNote = step.lost
      ? '<p class="insp-note warn">Gói này bị mất trên đường truyền (giả lập). Bên gửi sẽ gửi lại sau khi hết giờ.</p>' : '';
    const retransNote = step.retrans
      ? '<p class="insp-note">Đây là bản gửi lại, nội dung giống hệt gói đã mất.</p>' : '';
    const hasBlock = step.block !== null && step.block !== undefined;
    const sender = step.from === 'client' ? 'Client' : 'Server';
    const receiver = step.from === 'client' ? 'Server' : 'Client';

    inspBody.innerHTML =
      '<p class="insp-title"><span class="badge op-' + name.toLowerCase() + '">' + name + '</span>' +
      (hasBlock ? 'Block #' + step.block : '') + '</p>' +
      '<p class="insp-route">UDP ' + sender + ' :' + step.src + ' \u2192 ' + receiver + ' :' + step.dst +
      ', ' + bytes.length + ' byte</p>' +
      '<p class="insp-note">' + esc(meaning) + '</p>' + lostNote + retransNote +
      '<table class="fields"><thead><tr><th>Trường</th><th>Vị trí</th><th>Giá trị</th></tr></thead><tbody>' +
      fieldRows + '</tbody></table><div class="hex" aria-label="Dữ liệu dạng hex">' + rows + more + '</div>';
  }

  /* ---------- Nhật ký và thống kê ---------- */
  function addLog(who, state, msg) {
    const li = document.createElement('li');
    const w = document.createElement('span');
    w.className = 'log-who ' + who;
    w.textContent = who === 'client' ? 'Client' : who === 'server' ? 'Server' : who === 'network' ? 'Mạng' : 'Hệ thống';
    const s = document.createElement('span');
    s.className = 'log-state';
    s.textContent = state ? '[' + state + ']' : '';
    const m = document.createElement('span');
    m.textContent = msg;
    li.append(w, s, m);
    logList.appendChild(li);
    logList.parentElement.scrollTop = logList.parentElement.scrollHeight;
  }

  function showStats(st, direction) {
    const rows = [
      ['Hướng', direction === 'download' ? 'Tải về (RRQ)' : 'Tải lên (WRQ)'],
      ['Số khối', st.blocks],
      ['Timeout', st.timeouts],
      ['Gửi lại', st.retransmits],
      ['Kết quả', st.errored ? 'Có lỗi' : 'Thành công'],
    ];
    statsBox.innerHTML = rows.map((r) => '<div><dt>' + r[0] + '</dt><dd>' + esc(r[1]) + '</dd></div>').join('');
    statsBox.hidden = false;
  }

  /* ---------- Điều khiển ---------- */
  function setRunning(on) {
    running = on;
    btnRRQ.disabled = on;
    btnWRQ.disabled = on;
    btnPause.disabled = !on;
    inName.disabled = on;
    selSize.disabled = on;
    cbLoss.disabled = on;
    cbError.disabled = on;
    if (!on) { paused = false; btnPause.textContent = 'Tạm dừng'; }
  }

  function resetView() {
    resetDiagram();
    emptyMsg.hidden = false;
    $('#c-state').textContent = 'IDLE';
    $('#s-state').textContent = 'LISTEN';
    $('#s-port').textContent = '69';
    logList.innerHTML = '';
    statsBox.hidden = true;
    inspBody.innerHTML = '<p class="hint">Chưa chọn gói tin nào. Bấm vào một mũi tên trên sơ đồ.</p>';
  }

  async function start(direction) {
    if (running) return;
    const id = ++runId;
    resetView();
    emptyMsg.hidden = true;

    const filename = inName.value.trim() || 'test.txt';
    const fileSize = parseInt(selSize.value, 10);
    let built;
    try {
      built = UiAdapter.buildSteps({
        filename,
        fileSize,
        direction,
        lossBlock: cbLoss.checked ? (fileSize > 512 ? 2 : 1) : null, // giống bản demo của TV1
        fileNotFound: cbError.checked,
      });
    } catch (err) {
      emptyMsg.hidden = false;
      emptyMsg.textContent = 'Không chạy được core: ' + err.message;
      return;
    }
    setRunning(true);
    $('#c-port').textContent = built.clientPort;
    addLog('client', 'IDLE', 'Client sẵn sàng (cổng ' + built.clientPort + ').');
    addLog('server', 'LISTEN', 'Server lắng nghe trên cổng 69 (well-known).');

    for (const step of built.steps) {
      if (id !== runId) return;
      const ok = step.type === 'packet' ? await playPacket(step, id) : await playNote(step, id);
      if (!ok) return;
      if (step.type === 'packet') {
        const done = await animate(GAP, () => {}, id);
        if (!done) return;
      }
    }
    addLog('sys', '', 'Phiên kết thúc.');
    showStats(built.stats, direction);
    setRunning(false);
  }

  btnRRQ.addEventListener('click', () => start('download'));
  btnWRQ.addEventListener('click', () => start('upload'));
  btnPause.addEventListener('click', () => {
    paused = !paused;
    btnPause.textContent = paused ? 'Tiếp tục' : 'Tạm dừng';
  });
  btnReset.addEventListener('click', () => {
    runId += 1;
    setRunning(false);
    resetView();
  });
  rngSpeed.addEventListener('input', () => {
    outSpeed.textContent = speed().toFixed(2).replace(/0$/, '') + 'x';
  });

  resetView();
  outSpeed.textContent = speed().toFixed(1) + 'x';
})();
