/* K1 wire codec, ported from k1lib/protocol.py so the phone can build the same
 * bytes the laptop sends. No dependencies, no globals required:
 *
 *     +--------+---------+--------+---------+----------+
 *     | header | numByte | type   | data... | checksum |
 *     | ff ff  | 1 byte  | 1 byte | 0..n    | 1 byte   |
 *     +--------+---------+--------+---------+----------+
 *
 * numBytes = 1 (type) + len(data) + 1 (checksum)
 * checksum = sum(numBytes, type, *data) % 256
 *
 * In the browser it attaches `window.K1Frames`; in Node it is a module. That
 * is deliberate: tests run the same code in Node, against bytes produced by
 * Python, before the page ever touches a robot.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.K1Frames = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var HEADER = [0xff, 0xff];

  // robot.json type codes, limited to what a story needs.
  var CODES = {
    action: 0x17,     // action / progress notification
    audio: 0x19,      // play a clip by path
    stop: 0x0c,       // stop motion
    autoStand: 0x11   // posture management on/off, sent once on connect
  };

  var NAMES = {};
  Object.keys(CODES).forEach(function (name) { NAMES[CODES[name]] = name; });

  function checksum(bytes) {
    var sum = 0;
    for (var i = 0; i < bytes.length; i++) sum += bytes[i];
    return sum & 0xff;
  }

  function utf8(text) {
    if (typeof TextEncoder !== "undefined") return new TextEncoder().encode(text);
    // Node < 11 / ancient browsers: every payload we send is ASCII, but be
    // strict rather than silently mangling an accented path.
    var out = [];
    for (var i = 0; i < text.length; i++) {
      var c = text.charCodeAt(i);
      if (c > 0x7f) throw new Error("TextEncoder missing; cannot encode non-ASCII payload");
      out.push(c);
    }
    return new Uint8Array(out);
  }

  function asBytes(value) {
    if (value === null || value === undefined) return new Uint8Array(0);
    if (value instanceof Uint8Array) return value;
    if (typeof value === "boolean") return new Uint8Array([value ? 1 : 0]);
    if (typeof value === "number") return new Uint8Array([value & 0xff]);
    if (Array.isArray(value)) return new Uint8Array(value.map(function (b) { return b & 0xff; }));
    if (typeof value === "string") return utf8(value);
    throw new Error("cannot encode payload of type " + typeof value);
  }

  function codeOf(type) {
    if (typeof type === "number") return type & 0xff;
    if (typeof type === "string") {
      var key = type.toLowerCase();
      if (key.indexOf("0x") === 0) return parseInt(key, 16) & 0xff;
      // robot.json names are camelCase (autoStand), so try the name as written
      // before giving up on the lower-cased form.
      var code = CODES[key] !== undefined ? CODES[key] : CODES[type];
      if (code === undefined) throw new Error("unknown frame type " + JSON.stringify(type));
      return code;
    }
    throw new Error("frame type must be a name or a number");
  }

  function encode(type, payload) {
    var code = codeOf(type);
    var data = asBytes(payload);
    var numBytes = 1 + data.length + 1;
    var out = new Uint8Array(2 + 1 + numBytes);
    out[0] = HEADER[0];
    out[1] = HEADER[1];
    out[2] = numBytes;
    out[3] = code;
    out.set(data, 4);
    out[out.length - 1] = checksum(out.subarray(2, out.length - 1));
    return out;
  }

  function parse(frame) {
    if (!frame || frame.length < 5) return null;
    if (frame[0] !== HEADER[0] || frame[1] !== HEADER[1]) return null;
    var numBytes = frame[2];
    if (numBytes < 2) return null;
    var total = numBytes + 3;
    if (frame.length < total) return null;
    var body = frame.subarray(2, 2 + numBytes);
    var data = frame.subarray(4, numBytes + 2);
    return {
      valid: checksum(body) === frame[2 + numBytes],
      type: frame[3],
      typeName: NAMES[frame[3]] || "?",
      data: data,
      numBytes: numBytes,
      checksum: frame[2 + numBytes],
      raw: frame.subarray(0, total)
    };
  }

  /* Reassemble the notification stream. A BLE notification is not guaranteed
   * to be exactly one frame: two can arrive together, or one can be split.
   * `state` is a caller-owned object with a `remainder` Uint8Array. */
  function splitStream(state, chunk) {
    if (!state.remainder) state.remainder = new Uint8Array(0);
    var buf = new Uint8Array(state.remainder.length + chunk.length);
    buf.set(state.remainder, 0);
    buf.set(chunk, state.remainder.length);

    var frames = [];
    var pos = 0;
    while (pos + 1 < buf.length) {
      var start = -1;
      for (var i = pos; i + 1 < buf.length; i++) {
        if (buf[i] === 0xff && buf[i + 1] === 0xff) { start = i; break; }
      }
      if (start < 0) { pos = buf.length - 1; break; }
      if (buf.length - start < 3) { pos = start; break; }
      var total = buf[start + 2] + 3;
      if (buf[start + 2] < 2) { pos = start + 1; continue; }  // not a frame
      if (buf.length - start < total) { pos = start; break; }
      frames.push(buf.subarray(start, start + total));
      pos = start + total;
    }
    state.remainder = buf.slice(pos);
    return frames;
  }

  function hex(bytes) {
    var out = "";
    for (var i = 0; i < bytes.length; i++) {
      out += (bytes[i] < 16 ? "0" : "") + bytes[i].toString(16);
    }
    return out;
  }

  function text(bytes) {
    if (typeof TextDecoder !== "undefined") {
      return new TextDecoder("utf-8").decode(bytes);
    }
    var out = "";
    for (var i = 0; i < bytes.length; i++) out += String.fromCharCode(bytes[i]);
    return out;
  }

  return {
    HEADER: HEADER,
    CODES: CODES,
    NAMES: NAMES,
    checksum: checksum,
    encode: encode,
    parse: parse,
    splitStream: splitStream,
    hex: hex,
    text: text
  };
});
