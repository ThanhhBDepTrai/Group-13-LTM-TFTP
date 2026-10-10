/*
 * coreAdapter.js (TV2): cầu nối giữa core của TV1 và giao diện.
 *
 * Gọi TftpFsm.TftpSimulator (core/fsmEngine.js) để lấy danh sách events thật,
 * giải mã byte bằng TftpPackets (core/tftpPackets.js), rồi đổi thành các "bước"
 * (steps) để app.js vẽ lên sơ đồ. Không sửa bất kỳ file nào trong /core.
 *
 * Mỗi step là một trong hai dạng:
 *   { type:'packet', from, name, label, block, src, dst, bytes, fields, lost, retrans,
 *     send:{who,state,msg}, arrive:{who,state,msg,also?} | null }
 *   { type:'note', side, text, log:{who,state,msg}, sPort? }
 */
(function (global) {
  'use strict';

  function core() {
    var P = global.TftpPackets;
    var F = global.TftpFsm;
    if (!P || !F) throw new Error('Thiếu core: nạp ../core/tftpPackets.js và ../core/fsmEngine.js trước coreAdapter.js');
    return { P: P, F: F };
  }

  var other = function (side) { return side === 'client' ? 'server' : 'client'; };
  var cap = function (side) { return side === 'client' ? 'Client' : 'Server'; };

  /* Danh sách trường của gói tin (cho Packet Inspector), tính từ kết quả decode của core. */
  function fieldsFor(P, bytes) {
    var d = P.decode(bytes);
    var op = { name: 'Opcode', start: 0, len: 2, cls: 'f-op', value: d.opcode + ' (' + d.type + ')' };
    var nul = function (at) { return { name: 'Byte 0', start: at, len: 1, cls: 'f-nul', value: '0 (hết chuỗi)' }; };
    if (d.type === 'RRQ' || d.type === 'WRQ') {
      var fl = d.filename.length;
      var ml = d.mode.length;
      return [
        op,
        { name: 'Filename', start: 2, len: fl, cls: 'f-body', value: '"' + d.filename + '"' },
        nul(2 + fl),
        { name: 'Mode', start: 3 + fl, len: ml, cls: 'f-body', value: '"' + d.mode + '"' },
        nul(3 + fl + ml),
      ];
    }
    if (d.type === 'DATA') {
      var f = [op, { name: 'Block #', start: 2, len: 2, cls: 'f-blk', value: String(d.block) }];
      if (d.dataLen > 0) f.push({ name: 'Data', start: 4, len: d.dataLen, cls: 'f-body', value: d.dataLen + ' byte' });
      return f;
    }
    if (d.type === 'ACK') {
      return [op, { name: 'Block #', start: 2, len: 2, cls: 'f-blk', value: String(d.block) }];
    }
    return [
      op,
      { name: 'ErrorCode', start: 2, len: 2, cls: 'f-blk', value: d.errorCode + ' (' + d.meaning + ')' },
      { name: 'ErrMsg', start: 4, len: d.errorMsg.length, cls: 'f-body', value: '"' + d.errorMsg + '"' },
      nul(4 + d.errorMsg.length),
    ];
  }

  /*
   * opts: { filename, fileSize, direction: 'download'|'upload', lossBlock: số|null, fileNotFound: bool }
   * Trả về { steps, stats, clientPort }.
   */
  function buildSteps(opts) {
    var c = core();
    var P = c.P;
    var sim = new c.F.TftpSimulator({
      filename: opts.filename,
      fileSize: opts.fileSize,
      direction: opts.direction,
      lossBlock: opts.lossBlock,
      fileNotFound: opts.fileNotFound,
    });
    var res = sim.run();
    var ev = res.events;
    var steps = [];

    // Khối DATA cuối cùng của phiên: ACK của nó đóng phiên (COMPLETE).
    var finalBlock = null;
    ev.forEach(function (e) { if (e.action === 'DATA') finalBlock = e.block; });

    for (var i = 0; i < ev.length; i++) {
      var e = ev[i];
      var a = e.action;

      if (a === 'PACKET_LOSS') continue; // đã gộp vào gói DATA ngay trước nó

      if (a === 'TID_SWITCH') {
        steps.push({
          type: 'note', side: 'server',
          text: 'Server đổi cổng 69 \u2192 TID ' + e.toPort + ', giải phóng cổng 69',
          sPort: e.toPort + ' (TID)',
          log: { who: 'server', state: 'TID_ALLOCATED', msg: 'Mở TID ' + e.toPort + ', cổng 69 tiếp tục nhận Client mới.' },
        });
        continue;
      }

      if (a === 'TIMEOUT') {
        steps.push({
          type: 'note', side: e.from,
          text: cap(e.from) + ': hết giờ chờ ACK #' + e.block + ', sẽ gửi lại',
          log: { who: e.from, state: 'TIMEOUT_WAIT', msg: 'Hết giờ chờ ACK #' + e.block + ', gửi lại.' },
        });
        continue;
      }

      // Còn lại là các gói tin thật: RRQ, WRQ, DATA, DATA_RETRANSMIT, ACK, ERROR
      var base = a === 'DATA_RETRANSMIT' ? 'DATA' : a;
      var retrans = a === 'DATA_RETRANSMIT';
      var d = P.decode(e.bytes);
      var lost = a === 'DATA' && ev[i + 1] && ev[i + 1].action === 'PACKET_LOSS';
      var sender = e.from;
      var recv = other(sender);
      var n = e.block;
      var label = base === 'ERROR' ? 'ERROR ' + e.errorCode : base + (n != null ? ' #' + n : '');
      var final = base === 'DATA' && d.isFinal;

      var send;
      var arrive;
      if (base === 'RRQ' || base === 'WRQ') {
        send = { who: sender, state: base + '_SENT', msg: 'Gửi ' + base + ' "' + d.filename + '" tới cổng ' + e.toPort + '.' };
        arrive = { who: recv, state: base + '_RECEIVED', msg: 'Nhận ' + base + ', chuẩn bị mở TID riêng.' };
      } else if (base === 'DATA') {
        send = {
          who: sender, state: 'DATA_SENT',
          msg: (retrans ? 'Gửi lại DATA #' : 'Gửi DATA #') + n + ' (' + d.dataLen + ' B' + (final ? ', gói cuối < 512 B' : '') + ').',
        };
        arrive = lost
          ? { who: 'network', state: 'PACKET_LOSS', msg: 'DATA #' + n + ' bị mất trên đường truyền (giả lập).' }
          : { who: recv, state: 'DATA_RECEIVED', msg: 'Nhận DATA #' + n + '.' };
      } else if (base === 'ACK') {
        send = { who: sender, state: 'ACK_SENT', msg: 'Gửi ACK #' + n + (n === 0 ? ' (chấp nhận WRQ).' : '.') };
        if (n === finalBlock) {
          arrive = {
            who: recv, state: 'COMPLETE', msg: 'Nhận ACK #' + n + ', DATA cuối đã được xác nhận, đóng phiên.',
            also: { who: sender, state: 'COMPLETE', msg: 'Gói cuối < 512 B đã xong, đóng phiên.' },
          };
        } else {
          arrive = { who: recv, state: 'ACK_RECEIVED', msg: 'Nhận ACK #' + n + '.' };
        }
      } else { // ERROR
        send = { who: sender, state: 'ERROR_SENT', msg: 'Gửi ERROR ' + e.errorCode + ' "' + d.errorMsg + '".' };
        arrive = { who: recv, state: 'ERROR_RECEIVED', msg: 'Nhận ERROR ' + e.errorCode + ', huỷ phiên.' };
      }

      steps.push({
        type: 'packet', from: sender, name: base, label: label, block: n,
        src: e.fromPort, dst: e.toPort, bytes: e.bytes, fields: fieldsFor(P, e.bytes),
        lost: lost, retrans: retrans, send: send, arrive: arrive,
      });
    }

    // Core báo retransmits=1 cả khi phiên chỉ có ERROR, nên đếm lại từ các gói gửi lại thật.
    var retransmits = steps.filter(function (s) { return s.type === 'packet' && s.retrans; }).length;
    var stats = Object.assign({}, res.stats, { retransmits: retransmits });
    return { steps: steps, stats: stats, clientPort: res.stats.clientPort };
  }

  global.UiAdapter = { buildSteps: buildSteps };
})(typeof window !== 'undefined' ? window : globalThis);
