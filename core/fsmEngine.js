/* TFTP FSM Engine — mo phong Stop-and-Wait + Timeout + TID (RFC 1350)
 * TV1: Core Logic & State Machine. Khong phu thuoc DOM, UI chi viec render events.
 * Su dung: new TftpSimulator(opts).run() -> { events, logs, stats }
 */
(function (global) {
  'use strict';

  function getPackets() {
    if (typeof require !== 'undefined') {
      try { return require('./tftpPackets.js'); } catch (e) { /* browser */ }
    }
    return global.TftpPackets;
  }

  var WELL_KNOWN_PORT = 69;

  function TftpSimulator(opts) {
    opts = opts || {};
    this.filename = opts.filename || 'test.txt';
    this.mode = (opts.mode || 'octet').toLowerCase();
    this.fileSize = opts.fileSize != null ? opts.fileSize : 1500;
    this.direction = opts.direction === 'upload' ? 'upload' : 'download'; // download=RRQ, upload=WRQ
    this.lossBlock = opts.lossBlock != null ? opts.lossBlock : null; // vd: 2 => lam mat goi DATA/ACK #2 lan dau
    this.fileNotFound = !!opts.fileNotFound;
    this.clientPort = opts.clientPort || 50000;
    this.serverTid = opts.serverTid || 50001;
    this.maxRetries = opts.maxRetries || 3;
  }

  TftpSimulator.prototype.run = function () {
    var P = getPackets();
    if (!P) throw new Error('Thieu TftpPackets (nap tftpPackets.js truoc)');
    var events = [];
    var logs = [];
    var seq = 0;
    var self = this;

    function log(side, state, message) {
      logs.push({ seq: logs.length, side: side, state: state, message: message });
    }
    function emit(ev) {
      ev.seq = seq++;
      // kem bytes that + hex de Packet Inspector hien thi
      if (ev.bytes) {
        try {
          ev.hex = P.toHex(ev.bytes);
          ev.hexDump = P.toHexDump(ev.bytes.slice(0, 64));
          ev.label = P.describePacket(ev.bytes);
        } catch (e) { ev.label = ev.action; }
      }
      events.push(ev);
      return ev;
    }

    var from = 'client', to = 'server';
    log('client', 'IDLE', 'Client san sang (port ' + self.clientPort + ')');
    log('server', 'LISTEN', 'Server lang nghe tren port 69 (well-known)');

    if (self.direction === 'download') {
      // 1. RRQ client -> server:69
      var rrq = P.encodeRRQ(self.filename, self.mode);
      emit({ action: 'RRQ', from: 'client', to: 'server', fromPort: self.clientPort, toPort: WELL_KNOWN_PORT, block: null, size: rrq.length, bytes: rrq, desc: 'Client xin doc file "' + self.filename + '" (RRQ -> port 69)' });
      log('client', 'RRQ_SENT', 'Da gui RRQ "' + self.filename + '" mode=' + self.mode);
      log('server', 'RRQ_RECEIVED', 'Nhan RRQ, chuan bi mo TID rieng');

      if (self.fileNotFound) {
        var err = P.encodeERROR(1, 'File not found');
        emit({ action: 'TID_SWITCH', from: 'server', to: 'server', fromPort: WELL_KNOWN_PORT, toPort: self.serverTid, desc: 'Server doi port 69 -> TID ' + self.serverTid + ' (giai phong 69)' });
        log('server', 'TID_ALLOCATED', 'Server mo TID ' + self.serverTid + ', port 69 tiep tuc nhan client moi');
        emit({ action: 'ERROR', from: 'server', to: 'client', fromPort: self.serverTid, toPort: self.clientPort, block: null, size: err.length, bytes: err, errorCode: 1, desc: 'File khong ton tai -> ERROR 1 File not found' });
        log('server', 'ERROR_SENT', 'Gui ERROR 1, dong phien');
        log('client', 'ERROR_RECEIVED', 'Nhan ERROR 1, huy tai file');
        return { events: events, logs: logs, stats: stats(0, 1, true) };
      }

      emit({ action: 'TID_SWITCH', from: 'server', to: 'server', fromPort: WELL_KNOWN_PORT, toPort: self.serverTid, desc: 'Server doi port 69 -> TID ' + self.serverTid + ' (giai phong 69)' });
      log('server', 'TID_ALLOCATED', 'Server mo TID ' + self.serverTid + ', port 69 tiep tuc nhan client moi');

      var plan = P.blockPlan(self.fileSize);
      var retrans = 0, timeouts = 0;
      for (var i = 0; i < plan.length; i++) {
        var blk = plan[i].block, sz = plan[i].size;
        // tao payload gia (khong can du lieu that, chi can dung kich thuoc de minh hoa <512B)
        var payload = new Uint8Array(sz);
        for (var b = 0; b < sz; b++) payload[b] = 65 + ((blk + b) % 26); // 'A'..'Z' lap lai
        var data = P.encodeDATA(blk, payload);

        var firstTry = true;
        var needRetransmit = (self.lossBlock === blk);
        // lan gui dau
        emit({ action: 'DATA', from: 'server', to: 'client', fromPort: self.serverTid, toPort: self.clientPort, block: blk, size: sz, bytes: data, isFinal: sz < 512, desc: 'DATA #' + blk + ' (' + sz + 'B' + (sz < 512 ? ', FINAL' : '') + ')' });
        log('server', 'DATA_SENT', 'Gui DATA #' + blk + ' (' + sz + 'B)');
        if (needRetransmit && firstTry) {
          emit({ action: 'PACKET_LOSS', from: 'network', to: 'network', block: blk, desc: 'Gia lap MAT goi DATA #' + blk + ' tren duong truyen' });
          log('network', 'PACKET_LOSS', 'DATA #' + blk + ' bi mat');
          emit({ action: 'TIMEOUT', from: 'server', to: 'server', block: blk, desc: 'Het Timeout chua thay ACK #' + blk + ' -> gui lai' });
          log('server', 'TIMEOUT_WAIT', 'Timeout DATA #' + blk + ', gui lai lan 1');
          timeouts++; retrans++;
          emit({ action: 'DATA_RETRANSMIT', from: 'server', to: 'client', fromPort: self.serverTid, toPort: self.clientPort, block: blk, size: sz, bytes: data, isFinal: sz < 512, desc: 'Gui lai DATA #' + blk });
          log('server', 'DATA_SENT', 'Gui lai DATA #' + blk);
        }
        var ack = P.encodeACK(blk);
        emit({ action: 'ACK', from: 'client', to: 'server', fromPort: self.clientPort, toPort: self.serverTid, block: blk, size: 4, bytes: ack, desc: 'ACK #' + blk });
        log('client', 'ACK_SENT', 'Nhan DATA #' + blk + ' ok -> gui ACK #' + blk);
        log('server', 'ACK_RECEIVED', 'Nhan ACK #' + blk);
        if (sz < 512) {
          log('client', 'COMPLETE', 'DATA #' + blk + ' < 512B -> hieu la goi cuoi, dong phien');
          log('server', 'COMPLETE', 'Nhan du ACK cuoi, dong phien');
        }
      }
      return { events: events, logs: logs, stats: stats(plan.length, retrans, false, timeouts) };
    } else {
      // ---- UPLOAD (WRQ) ----
      var wrq = P.encodeWRQ(self.filename, self.mode);
      emit({ action: 'WRQ', from: 'client', to: 'server', fromPort: self.clientPort, toPort: WELL_KNOWN_PORT, bytes: wrq, size: wrq.length, desc: 'Client xin ghi file "' + self.filename + '" (WRQ -> port 69)' });
      log('client', 'WRQ_SENT', 'Da gui WRQ "' + self.filename + '"');
      log('server', 'WRQ_RECEIVED', 'Nhan WRQ');

      if (self.fileNotFound) { // tai su dung checkbox "File khong ton tai" de demo ERROR ben upload: server tu choi
        var err2 = P.encodeERROR(2, 'Access violation');
        emit({ action: 'TID_SWITCH', from: 'server', to: 'server', fromPort: WELL_KNOWN_PORT, toPort: self.serverTid, desc: 'Server doi port 69 -> TID ' + self.serverTid });
        emit({ action: 'ERROR', from: 'server', to: 'client', fromPort: self.serverTid, toPort: self.clientPort, bytes: err2, size: err2.length, errorCode: 2, desc: 'Tu choi ghi -> ERROR 2 Access violation' });
        log('server', 'ERROR_SENT', 'Gui ERROR 2');
        return { events: events, logs: logs, stats: stats(0, 1, true) };
      }

      emit({ action: 'TID_SWITCH', from: 'server', to: 'server', fromPort: WELL_KNOWN_PORT, toPort: self.serverTid, desc: 'Server doi port 69 -> TID ' + self.serverTid });
      log('server', 'TID_ALLOCATED', 'Server mo TID ' + self.serverTid);

      var ack0 = P.encodeACK(0);
      emit({ action: 'ACK', from: 'server', to: 'client', fromPort: self.serverTid, toPort: self.clientPort, block: 0, size: 4, bytes: ack0, desc: 'ACK #0 (chap nhan WRQ, bat dau nhan DATA)' });
      log('server', 'ACK_SENT', 'Gui ACK #0');

      var planU = P.blockPlan(self.fileSize);
      var retransU = 0, timeoutsU = 0;
      for (var u = 0; u < planU.length; u++) {
        var blkU = planU[u].block, szU = planU[u].size;
        var payloadU = new Uint8Array(szU);
        for (var c = 0; c < szU; c++) payloadU[c] = 65 + ((blkU + c) % 26);
        var dataU = P.encodeDATA(blkU, payloadU);
        emit({ action: 'DATA', from: 'client', to: 'server', fromPort: self.clientPort, toPort: self.serverTid, block: blkU, size: szU, bytes: dataU, isFinal: szU < 512, desc: 'DATA #' + blkU + ' (' + szU + 'B)' });
        log('client', 'DATA_SENT', 'Gui DATA #' + blkU);
        if (self.lossBlock === blkU) {
          emit({ action: 'PACKET_LOSS', from: 'network', to: 'network', block: blkU, desc: 'Gia lap MAT goi DATA #' + blkU });
          emit({ action: 'TIMEOUT', from: 'client', to: 'client', block: blkU, desc: 'Het Timeout chua thay ACK #' + blkU + ' -> gui lai' });
          log('client', 'TIMEOUT_WAIT', 'Timeout DATA #' + blkU);
          timeoutsU++; retransU++;
          emit({ action: 'DATA_RETRANSMIT', from: 'client', to: 'server', fromPort: self.clientPort, toPort: self.serverTid, block: blkU, size: szU, bytes: dataU, isFinal: szU < 512, desc: 'Gui lai DATA #' + blkU });
        }
        var ackU = P.encodeACK(blkU);
        emit({ action: 'ACK', from: 'server', to: 'client', fromPort: self.serverTid, toPort: self.clientPort, block: blkU, size: 4, bytes: ackU, desc: 'ACK #' + blkU });
        log('server', 'ACK_SENT', 'Nhan DATA #' + blkU + ' -> ACK #' + blkU);
        if (szU < 512) {
          log('server', 'COMPLETE', 'DATA #' + blkU + ' < 512B -> goi cuoi');
          log('client', 'COMPLETE', 'Nhan ACK cuoi, dong phien');
        }
      }
      return { events: events, logs: logs, stats: stats(planU.length, retransU, false, timeoutsU) };
    }

    function stats(blocks, retransmits, errored, timeoutsCount) {
      return {
        direction: self.direction, filename: self.filename, fileSize: self.fileSize,
        blocks: blocks, retransmits: retransmits || 0, timeouts: timeoutsCount || 0,
        errored: !!errored, clientPort: self.clientPort, serverTid: self.serverTid
      };
    }
  };

  var api = { TftpSimulator: TftpSimulator, WELL_KNOWN_PORT: WELL_KNOWN_PORT };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.TftpFsm = api;
})(typeof window !== 'undefined' ? window : globalThis);
