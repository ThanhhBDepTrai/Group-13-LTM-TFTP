/* TFTP Packet codec — RFC 1350
 * TV1 (Nhom truong): Core Logic & State Machine
 * Chay duoc ca Node.js va browser (khong dung require).
 * 5 goi tin: RRQ(1) WRQ(2) DATA(3) ACK(4) ERROR(5)
 */
(function (global) {
  'use strict';

  var OPCODES = { RRQ: 1, WRQ: 2, DATA: 3, ACK: 4, ERROR: 5 };
  var OPCODE_NAMES = { 1: 'RRQ', 2: 'WRQ', 3: 'DATA', 4: 'ACK', 5: 'ERROR' };

  var ERROR_CODES = {
    0: 'Not defined',
    1: 'File not found',
    2: 'Access violation',
    3: 'Disk full or allocation exceeded',
    4: 'Illegal TFTP operation',
    5: 'Unknown transfer ID',
    6: 'File already exists',
    7: 'No such user'
  };

  var VALID_MODES = ['netascii', 'octet', 'mail'];

  function encodeString(s) {
    if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(s);
    // fallback Node < 11
    var buf = Buffer.from(s, 'ascii');
    return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
  }
  function decodeString(bytes) {
    if (typeof TextDecoder !== 'undefined') return new TextDecoder('ascii').decode(bytes);
    return Buffer.from(bytes).toString('ascii');
  }

  function normalizeMode(mode) {
    if (!mode) return 'octet';
    var m = String(mode).toLowerCase();
    if (VALID_MODES.indexOf(m) === -1) throw new Error('Mode khong hop le: ' + mode + ' (cho phep: netascii|octet|mail)');
    return m;
  }

  function validateFilename(filename) {
    if (typeof filename !== 'string' || filename.length === 0) throw new Error('Filename rong');
    if (filename.length > 255) throw new Error('Filename qua dai');
    if (filename.indexOf('\x00') !== -1) throw new Error('Filename chua byte 0');
    return filename;
  }

  function validateBlock(block) {
    if (!Number.isInteger(block) || block < 0 || block > 65535) throw new Error('Block number phai 0..65535, nhan: ' + block);
    return block;
  }

  // ---- RRQ / WRQ ----
  function encodeRequest(opcode, filename, mode) {
    validateFilename(filename);
    mode = normalizeMode(mode);
    var fb = encodeString(filename);
    var mb = encodeString(mode);
    var out = new Uint8Array(2 + fb.length + 1 + mb.length + 1);
    out[0] = 0; out[1] = opcode;
    out.set(fb, 2);
    out[2 + fb.length] = 0;
    out.set(mb, 2 + fb.length + 1);
    out[out.length - 1] = 0;
    return out;
  }
  function encodeRRQ(filename, mode) { return encodeRequest(OPCODES.RRQ, filename, mode || 'octet'); }
  function encodeWRQ(filename, mode) { return encodeRequest(OPCODES.WRQ, filename, mode || 'octet'); }

  function decodeRequest(buf) {
    var opcode = (buf[0] << 8) | buf[1];
    if (opcode !== 1 && opcode !== 2) throw new Error('Khong phai RRQ/WRQ, opcode=' + opcode);
    // tach 2 chuoi ket thuc bang 0
    var i = 2, j;
    for (j = i; j < buf.length; j++) if (buf[j] === 0) break;
    if (j >= buf.length) throw new Error('Thieu byte 0 sau filename');
    var filename = decodeString(buf.slice(i, j));
    i = j + 1;
    for (j = i; j < buf.length; j++) if (buf[j] === 0) break;
    if (j >= buf.length) throw new Error('Thieu byte 0 sau mode');
    var mode = decodeString(buf.slice(i, j));
    if (j + 1 !== buf.length) throw new Error('Du byte sau mode');
    return { opcode: opcode, type: OPCODE_NAMES[opcode], filename: filename, mode: mode.toLowerCase() };
  }

  // ---- DATA ----
  function encodeDATA(block, data) {
    validateBlock(block);
    var d = data instanceof Uint8Array ? data : new Uint8Array(data || []);
    if (d.length > 512) throw new Error('DATA vuot 512 bytes: ' + d.length);
    var out = new Uint8Array(4 + d.length);
    out[0] = 0; out[1] = OPCODES.DATA;
    out[2] = (block >> 8) & 0xff; out[3] = block & 0xff;
    out.set(d, 4);
    return out;
  }
  function decodeDATA(buf) {
    if (buf.length < 4) throw new Error('DATA qua ngan');
    var opcode = (buf[0] << 8) | buf[1];
    if (opcode !== 3) throw new Error('Khong phai DATA, opcode=' + opcode);
    var block = (buf[2] << 8) | buf[3];
    var data = buf.slice(4);
    if (data.length > 512) throw new Error('DATA vuot 512 bytes');
    return { opcode: 3, type: 'DATA', block: block, data: data, dataLen: data.length, isFinal: data.length < 512 };
  }

  // ---- ACK ----
  function encodeACK(block) {
    validateBlock(block);
    var out = new Uint8Array(4);
    out[0] = 0; out[1] = OPCODES.ACK;
    out[2] = (block >> 8) & 0xff; out[3] = block & 0xff;
    return out;
  }
  function decodeACK(buf) {
    if (buf.length !== 4) throw new Error('ACK phai dung 4 bytes, nhan ' + buf.length);
    var opcode = (buf[0] << 8) | buf[1];
    if (opcode !== 4) throw new Error('Khong phai ACK, opcode=' + opcode);
    return { opcode: 4, type: 'ACK', block: (buf[2] << 8) | buf[3] };
  }

  // ---- ERROR ----
  function encodeERROR(code, msg) {
    if (!Number.isInteger(code) || code < 0 || code > 7) throw new Error('Error code phai 0..7');
    msg = msg == null ? (ERROR_CODES[code] || '') : String(msg);
    var mb = encodeString(msg);
    var out = new Uint8Array(4 + mb.length + 1);
    out[0] = 0; out[1] = OPCODES.ERROR;
    out[2] = (code >> 8) & 0xff; out[3] = code & 0xff;
    out.set(mb, 4);
    out[out.length - 1] = 0;
    return out;
  }
  function decodeERROR(buf) {
    if (buf.length < 5) throw new Error('ERROR qua ngan');
    var opcode = (buf[0] << 8) | buf[1];
    if (opcode !== 5) throw new Error('Khong phai ERROR, opcode=' + opcode);
    var code = (buf[2] << 8) | buf[3];
    if (buf[buf.length - 1] !== 0) throw new Error('ERROR thieu byte 0 ket thuc');
    var msg = decodeString(buf.slice(4, buf.length - 1));
    return { opcode: 5, type: 'ERROR', errorCode: code, errorMsg: msg, meaning: ERROR_CODES[code] || 'Unknown' };
  }

  // ---- Generic decode ----
  function decode(buf) {
    var u8 = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
    if (u8.length < 2) throw new Error('Goi tin qua ngan (<2 bytes)');
    var opcode = (u8[0] << 8) | u8[1];
    switch (opcode) {
      case 1:
      case 2: return decodeRequest(u8);
      case 3: return decodeDATA(u8);
      case 4: return decodeACK(u8);
      case 5: return decodeERROR(u8);
      default: throw new Error('Opcode la: ' + opcode);
    }
  }

  // ---- Helpers cho Packet Inspector ----
  function toHex(buf, maxBytes) {
    var n = Math.min(buf.length, maxBytes == null ? buf.length : maxBytes);
    var parts = [];
    for (var i = 0; i < n; i++) {
      var h = buf[i].toString(16).toUpperCase();
      if (h.length < 2) h = '0' + h;
      parts.push(h);
    }
    return parts.join(' ');
  }
  function toHexDump(buf) {
    // ASCII + hex giong Wireshark, 16 bytes/dong
    var lines = [];
    for (var off = 0; off < buf.length; off += 16) {
      var chunk = buf.slice(off, off + 16);
      var hex = [];
      for (var i = 0; i < 16; i++) {
        if (i < chunk.length) {
          var h = chunk[i].toString(16).toUpperCase();
          hex.push(h.length < 2 ? '0' + h : h);
        } else hex.push('  ');
      }
      var ascii = '';
      for (var k = 0; k < chunk.length; k++) {
        var c = chunk[k];
        ascii += (c >= 32 && c <= 126) ? String.fromCharCode(c) : '.';
      }
      var addr = ('0000' + off.toString(16).toUpperCase()).slice(-4);
      lines.push(addr + '  ' + hex.join(' ') + '  |' + ascii + '|');
    }
    return lines.join('\n');
  }
  function describePacket(buf) {
    var d = decode(buf);
    switch (d.type) {
      case 'RRQ': return 'RRQ "' + d.filename + '" mode=' + d.mode;
      case 'WRQ': return 'WRQ "' + d.filename + '" mode=' + d.mode;
      case 'DATA': return 'DATA #' + d.block + ' (' + d.dataLen + 'B' + (d.isFinal ? ', FINAL' : '') + ')';
      case 'ACK': return 'ACK #' + d.block;
      case 'ERROR': return 'ERROR ' + d.errorCode + ' "' + d.errorMsg + '"';
      default: return 'UNKNOWN';
    }
  }
  function nextBlock(block) { return (block + 1) & 0xffff; } // wrap 65535 -> 0

  // So block can cho 1 file (gom ca goi 0-byte cuoi neu file chia het cho 512)
  function blockPlan(fileSize) {
    if (!Number.isInteger(fileSize) || fileSize < 0) throw new Error('fileSize phai >= 0');
    if (fileSize === 0) return [{ block: 1, size: 0 }];
    var plan = [];
    var remaining = fileSize, block = 1;
    while (remaining > 0) {
      var s = Math.min(512, remaining);
      plan.push({ block: block, size: s });
      remaining -= s;
      block = nextBlock(block);
      if (block === 0) block = 1; // tranh block 0 giua chung (block 0 chi danh cho WRQ-ACK0); thuc te RFC cho wrap ve 0
    }
    var last = plan[plan.length - 1];
    if (last.size === 512) {
      // file chia het cho 512 -> them goi 0-byte ket thuc
      plan.push({ block: block, size: 0 });
    }
    return plan;
  }

  var api = {
    OPCODES: OPCODES, OPCODE_NAMES: OPCODE_NAMES, ERROR_CODES: ERROR_CODES,
    encodeRRQ: encodeRRQ, encodeWRQ: encodeWRQ, decodeRequest: decodeRequest,
    encodeDATA: encodeDATA, decodeDATA: decodeDATA,
    encodeACK: encodeACK, decodeACK: decodeACK,
    encodeERROR: encodeERROR, decodeERROR: decodeERROR,
    decode: decode, toHex: toHex, toHexDump: toHexDump,
    describePacket: describePacket, nextBlock: nextBlock, blockPlan: blockPlan
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else { global.TftpPackets = api; }
})(typeof window !== 'undefined' ? window : globalThis);
