#!/usr/bin/env node
/* Run the real player.js against a fake K1, with no browser and no robot.
 *
 *     node tools/phone/check_player.js
 *
 * This is the offline proof of the parts that are awkward to watch on hardware:
 * the progress rules (a 100 without a start packet is ignored), the write
 * fallback (no-response → with-response for a 24-byte action frame), the
 * rehearsal path (no writes at all), and Stop / ready. A tiny DOM stands in for
 * the real page; every BLE packet still goes through frames.js.
 */
"use strict";

var fs = require("fs");
var path = require("path");
var vm = require("vm");
var F = require("./frames.js");

var here = __dirname;
var failures = 0;
var checks = 0;

function check(label, ok, extra) {
  checks += 1;
  if (!ok) failures += 1;
  console.log((ok ? "PASS" : "FAIL") + "  " + label + (extra && !ok ? "  " + extra : ""));
}

function waitFor(fn, label, timeout) {
  var limit = timeout || 8000;
  return new Promise(function (resolve, reject) {
    var t0 = Date.now();
    var id = setInterval(function () {
      var value;
      try { value = fn(); } catch (err) { clearInterval(id); reject(err); return; }
      if (value) { clearInterval(id); resolve(value); return; }
      if (Date.now() - t0 > limit) { clearInterval(id); reject(new Error("timeout: " + label)); }
    }, 20);
  });
}

// ------------------------------------------------------------------- fake DOM
function El(tag) {
  this.tagName = tag || "div";
  this.children = [];
  this._text = "";
  this.className = "";
  this.id = "";
  this.hidden = false;
  this.disabled = false;
  this.checked = false;
  this.value = "";
  this.style = {};
  this.scrollTop = 0;
  this.scrollHeight = 0;
  this.handlers = {};
  this.classList = { toggle: function () {} };
  this.parentNode = { classList: { toggle: function () {} } };
}
Object.defineProperty(El.prototype, "textContent", {
  get: function () { return this._text + this.children.map(function (c) { return c.textContent; }).join(""); },
  set: function (v) { this._text = String(v); this.children = []; }
});
Object.defineProperty(El.prototype, "innerText", {
  get: function () { return this.textContent; }
});
Object.defineProperty(El.prototype, "childElementCount", {
  get: function () { return this.children.length; }
});
Object.defineProperty(El.prototype, "firstChild", {
  get: function () { return this.children[0] || null; }
});
El.prototype.appendChild = function (child) { this.children.push(child); return child; };
El.prototype.removeChild = function (child) {
  this.children = this.children.filter(function (x) { return x !== child; });
  return child;
};
El.prototype.addEventListener = function (type, fn) { this.handlers[type] = fn; };
El.prototype.querySelectorAll = function () { return []; };

var els = {};
var ids = [
  "statusText", "statusDot", "connectHint", "btnConnect", "btnDisconnect",
  "chkAll", "chkRehearse", "chkWithResponse", "stories", "stage", "stageTitle", "stageState",
  "caption", "captionSub", "barFill", "timeNow", "timeTotal", "btnStop",
  "btnReady", "log", "diag", "envWarn", "btnCopyLog", "btnClearLog"
];
ids.forEach(function (id) {
  var el = new El("div");
  el.id = id;
  els[id] = el;
});

var document = {
  readyState: "complete",
  getElementById: function (id) { return els[id] || null; },
  createElement: function (tag) { return new El(tag); },
  createTextNode: function (text) {
    var node = { textContent: String(text) };
    return node;
  },
  addEventListener: function () {}
};

// --------------------------------------------------------------- fake radio
var sent = [];
var notify = null;
var failNoResponse = false;

function emitProgress(pct) {
  if (!notify) throw new Error("the player never subscribed to notifications");
  var frame = F.encode("action", [pct]);
  notify({ target: { value: new DataView(frame.buffer, frame.byteOffset, frame.byteLength) } });
}

var char = {
  properties: { write: true, writeWithoutResponse: true, notify: true },
  writeValueWithoutResponse: function (frame) {
    if (failNoResponse) {
      var err = new Error("This stack will not write without a response");
      err.name = "NotSupportedError";
      return Promise.reject(err);
    }
    sent.push(F.hex(frame));
    onActionWrite(frame);
    return Promise.resolve();
  },
  writeValueWithResponse: function (frame) {
    sent.push(F.hex(frame));
    onActionWrite(frame);
    return Promise.resolve();
  },
  startNotifications: function () { return Promise.resolve(); },
  addEventListener: function (type, fn) {
    if (type === "characteristicvaluechanged") notify = fn;
  }
};

function onActionWrite(frame) {
  var pkt = F.parse(new Uint8Array(frame));
  // Outgoing action frames carry a text path; the progress notifications we
  // inject carry one byte. Ignore the former's length and answer the latter.
  if (!pkt || pkt.type !== F.CODES.action || pkt.data.length === 1) return;
  // The first stunt in the test story deliberately opens with an unprovoked
  // 100 (the late-packet bug), then streams a real 15 → 60 → 100.
  setTimeout(function () { emitProgress(100); }, 10);
  setTimeout(function () { emitProgress(15); }, 45);
  setTimeout(function () { emitProgress(60); }, 85);
  setTimeout(function () { emitProgress(100); }, 180);
}

var device = {
  name: "K1-TEST",
  id: "fake-k1",
  addEventListener: function () {},
  gatt: {
    connected: true,
    connect: function () {
      return Promise.resolve({
        getPrimaryService: function () {
          return Promise.resolve({
            getCharacteristic: function () { return Promise.resolve(char); }
          });
        }
      });
    },
    disconnect: function () { this.connected = false; }
  }
};

var navigator = {
  bluetooth: {
    requestDevice: function () { return Promise.resolve(device); }
  },
  userAgent: "k1-phone-test"
};

// ------------------------------------------------------------------ the data
var DATA = {
  gap: 0.35,
  settle: 6.0,
  service: "0000ffe0-0000-1000-8000-00805f9b34fb",
  characteristic: "0000ffe1-0000-1000-8000-00805f9b34fb",
  actions: { "Say Hello": "ProAction/Say Hello" },
  stories: [
    {
      id: "test", title: "Test", language: "EN", blurb: "",
      steps: [{ speak: "02/1" }, { wait: 0.02 }, { action: "Say Hello", grace: 15.5 }],
      notes: ["hello", null, "Say Hello"],
      expected: 1.0, preview_seconds: 0.6, stunt_count: 1, clip_count: 1,
      clip_range: "1-1", voice: "test"
    },
    {
      id: "fallback", title: "Fallback", language: "EN", blurb: "",
      steps: [{ speak: "02/9" }, { wait: 0.02 }],
      notes: ["fallback", null],
      expected: 0.4, preview_seconds: 0.02, stunt_count: 0, clip_count: 1,
      clip_range: "9-9", voice: "test"
    },
    {
      id: "rehearse", title: "Rehearse", language: "EN", blurb: "",
      steps: [{ action: "Say Hello", grace: 15.6 }],
      notes: ["Say Hello"],
      expected: 0.95, preview_seconds: 0.6, stunt_count: 1, clip_count: 0,
      clip_range: "-", voice: "test"
    },
    {
      id: "hold", title: "Hold", language: "EN", blurb: "",
      steps: [{ speak: "02/2" }, { wait: 5 }],
      notes: ["hold", null],
      expected: 6, preview_seconds: 5, stunt_count: 0, clip_count: 1,
      clip_range: "2-2", voice: "test"
    }
  ]
};

// ------------------------------------------------------------------ sandbox
var sandbox = {
  console: console,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  setInterval: setInterval,
  clearInterval: clearInterval,
  performance: performance,
  TextEncoder: TextEncoder,
  TextDecoder: TextDecoder,
  document: document,
  location: { origin: "http://localhost:8000" },
  isSecureContext: true,
  navigator: navigator
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.K1_DATA = DATA;
vm.createContext(sandbox);

vm.runInContext(fs.readFileSync(path.join(here, "frames.js"), "utf8"), sandbox, { filename: "frames.js" });
vm.runInContext(fs.readFileSync(path.join(here, "player.js"), "utf8"), sandbox, { filename: "player.js" });

var player = sandbox.K1Player;
var audioHex = F.hex(F.encode("audio", "02/1"));
var actionHex = F.hex(F.encode("action", "ProAction/Say Hello"));
var autoStandHex = F.hex(F.encode("autoStand", true));
var stopHex = F.hex(F.encode("stop", null));

function logText() { return els.log.textContent; }

async function main() {
  check("player loaded", !!player, "window.K1Player missing");
  check("story cards were built", els.stories.children.length === DATA.stories.length,
        els.stories.children.length + " cards");

  console.log("\n-- connect --");
  player.connect();
  await waitFor(function () { return player.state.connected; }, "connect");
  check("notifications subscribed", !!notify);
  check("autoStand is enabled on connect, like K1.connect()",
        autoStandHex === "ffff03110115" && sent.indexOf(autoStandHex) !== -1,
        "sent " + sent.join(","));
  check("status says connected", els.statusText.textContent === "Connected",
        JSON.stringify(els.statusText.textContent));

  console.log("\n-- a stunt waits for real progress --");
  player.play("test");
  await waitFor(function () { return !player.state.running; }, "story finish");
  check("audio frame sent", sent.indexOf(audioHex) !== -1);
  check("24-byte action frame sent", sent.indexOf(actionHex) !== -1,
        "sent " + sent.join(","));
  check("an unprovoked 100 is ignored",
        logText().indexOf("ignored an unprovoked 100%") !== -1);
  check("the real stream completes the stunt",
        logText().indexOf("Say Hello 100% after") !== -1);
  check("the story reports finished", logText().indexOf("story finished") !== -1);
  check("nothing is blocked after a clean run", player.state.blocked === false);

  console.log("\n-- long writes fall back to with-response --");
  failNoResponse = true;
  var before = sent.length;
  player.play("fallback");
  await waitFor(function () { return !player.state.running; }, "fallback finish");
  check("the fallback frame was written", sent.length > before);
  check("the fallback is reported",
        logText().indexOf("with response; no-response failed") !== -1);
  failNoResponse = false;

  console.log("\n-- forced with-response --");
  els.chkWithResponse.checked = true;
  var forcedBefore = sent.length;
  player.play("fallback");
  await waitFor(function () { return !player.state.running; }, "forced finish");
  check("the forced frame was written", sent.length > forcedBefore);
  check("forced mode is reported", logText().indexOf("with response; forced") !== -1);
  els.chkWithResponse.checked = false;

  console.log("\n-- rehearsal sends nothing --");
  els.chkRehearse.checked = true;
  var quiet = sent.length;
  player.play("rehearse");
  await waitFor(function () { return !player.state.running; }, "rehearsal finish");
  check("no frame was sent", sent.length === quiet, (sent.length - quiet) + " sent");
  check("rehearsal is named in the log", logText().indexOf("(rehearsal)") !== -1);

  console.log("\n-- stop and ready --");
  els.chkRehearse.checked = false;
  player.play("hold");
  await waitFor(function () { return player.state.running; }, "hold starts");
  await new Promise(function (r) { setTimeout(r, 250); });
  player.stop();
  await waitFor(function () { return !player.state.running; }, "stop settles");
  check("the stop frame was sent", sent.indexOf(stopHex) !== -1);
  check("story stopped", logText().indexOf("story stopped") !== -1);
  check("play is blocked until the robot is acknowledged", player.state.blocked === true);
  player.ready();
  check("ready clears the block", player.state.blocked === false);

  console.log("\n" + (failures ? failures + " FAILURE(S)" : "ALL PASS") +
              "  (" + checks + " checks)");
  process.exit(failures ? 1 : 0);
}

main().catch(function (err) {
  console.error("HARNESS ERROR: " + (err && err.stack || err));
  console.error("running=" + (player && player.state.running) +
                " blocked=" + (player && player.state.blocked) +
                " connected=" + (player && player.state.connected));
  console.error("--- page log ---\n" + logText());
  process.exit(1);
});
