/* The phone-side runner: Web Bluetooth + the same routine semantics k1lib uses.
 *
 * It is deliberately small and transparent rather than clever:
 *   - one frame in flight at a time, exactly like RobotCore's command queue;
 *   - a stunt waits for its progress-100 (now, not for its clock), and a 100
 *     without a start packet is ignored, because that is how a late packet
 *     used to end the next stunt instantly;
 *   - the routine's own waits carry the timeline, and the same 0.35 s breath
 *     `i_routine` adds after each intent is reproduced here;
 *   - a client-side 6 s settle is a backstop only: the generated routine
 *     already satisfies it, so it should never fire.
 */
(function () {
  "use strict";

  var DATA = window.K1_DATA;
  var F = window.K1Frames;
  if (!DATA || !F) return;

  var $ = function (id) { return document.getElementById(id); };
  var now = function () { return performance.now(); };

  var state = {
    device: null,
    server: null,
    char: null,
    connected: false,
    running: false,
    abort: false,
    blocked: false,
    runId: 0,
    stream: { remainder: new Uint8Array(0) },
    action: null,
    run: null,
    lastActionEnd: null,
    wake: null,
    writeChain: Promise.resolve()
  };

  var els = {};

  // ------------------------------------------------------------------ utils
  function describe(err) {
    if (!err) return "unknown error";
    return (err.name ? err.name + ": " : "") + (err.message || String(err));
  }

  function abortError() {
    var e = new Error("stopped");
    e.name = "AbortError";
    return e;
  }

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function mmss(seconds) {
    var s = Math.max(0, Math.round(seconds));
    return Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2);
  }

  function log(message, kind) {
    var line = document.createElement("div");
    line.className = "entry" + (kind ? " " + kind : "");
    var t = document.createElement("span");
    t.className = "t";
    var d = new Date();
    t.textContent = ("0" + d.getHours()).slice(-2) + ":" +
                    ("0" + d.getMinutes()).slice(-2) + ":" +
                    ("0" + d.getSeconds()).slice(-2) + " ";
    line.appendChild(t);
    line.appendChild(document.createTextNode(message));
    els.log.appendChild(line);
    while (els.log.childElementCount > 600) els.log.removeChild(els.log.firstChild);
    els.log.scrollTop = els.log.scrollHeight;
  }

  function setStatus(text, kind) {
    els.statusText.textContent = text;
    els.statusDot.className = "dot" + (kind ? " " + kind : "");
  }

  function setStageState(text, kind) {
    els.stageState.textContent = text;
    els.stageState.className = "chip" + (kind ? " " + kind : "");
  }

  function setCaption(text, kind) {
    els.caption.textContent = text || "…";
    els.caption.className = "caption" + (kind ? " " + kind : "");
    els.caption.scrollTop = 0;
  }

  function setStageSub(text) {
    els.captionSub.textContent = text || "";
  }

  // ---------------------------------------------------------------- frames
  function sendFrame(type, payload, label) {
    var frame = F.encode(type, payload);
    // Serialise writes even though the runner is sequential: Stop is a second
    // writer, and overlapping GATT writes are how "operation already in
    // progress" errors start.
    state.writeChain = state.writeChain.then(function () {
      return writeNow(frame, label);
    }, function () {
      return writeNow(frame, label);
    });
    return state.writeChain;
  }

  function writeNow(frame, label) {
    if (!state.char) return Promise.reject(new Error("not connected"));
    var props = state.char.properties || {};
    if (!props.writeWithoutResponse && !props.write) {
      return Promise.reject(new Error("characteristic is not writable"));
    }

    var attempts = 0;

    function retryable(err) {
      var msg = describe(err);
      if (/already in progress/i.test(msg) && attempts < 4) {
        attempts += 1;
        return sleep(180).then(function () { return run(); });
      }
      throw err;
    }

    function withResponse(note) {
      return state.char.writeValueWithResponse(frame).then(function () {
        log("→ " + label + "  " + F.hex(frame) + "  (" + frame.length + " B, with response" +
            (note ? "; " + note : "") + ")", "tx");
      }).catch(retryable);
    }

    function withoutResponse() {
      return state.char.writeValueWithoutResponse(frame).then(function () {
        log("→ " + label + "  " + F.hex(frame) + "  (" + frame.length + " B, no response)", "tx");
      }).catch(function (first) {
        if (/already in progress/i.test(describe(first))) return retryable(first);
        // The K1's action paths are 24-34 bytes. If this stack will not take a
        // long write without response, a write *with* response lets the GATT
        // layer fragment it at the MTU. Same frame on the wire either way.
        return withResponse("no-response failed: " + describe(first));
      });
    }

    function run() {
      if (els.chkWithResponse.checked) return withResponse("forced");
      return withoutResponse();
    }

    return run();
  }

  function onValue(event) {
    var view = event.target.value;
    var chunk = new Uint8Array(view.buffer, view.byteOffset, view.byteLength);
    var frames = F.splitStream(state.stream, chunk);
    for (var i = 0; i < frames.length; i++) {
      var pkt = F.parse(frames[i]);
      if (!pkt) continue;
      if (!pkt.valid) {
        log("← bad checksum  " + F.hex(frames[i]), "warn");
        continue;
      }
      if (pkt.type === F.CODES.action && pkt.data.length === 1) {
        handleProgress(pkt.data[0]);
      } else {
        var body = pkt.data.length > 1 ? F.text(pkt.data) : F.hex(pkt.data);
        log("← type 0x" + pkt.type.toString(16) + " (" + pkt.typeName + ") " + body, "rx");
      }
    }
  }

  // -------------------------------------------------------------- progress
  function handleProgress(pct) {
    var a = state.action;
    if (!a) {
      log("progress " + pct + "% with no stunt in flight (ignored)", "warn");
      return;
    }
    if (a.done) return;
    a.packets += 1;
    if (pct >= 100) {
      if (a.started) {
        a.done = true;
        a.fwMs = now() - a.t0;
        log("← " + a.name + " 100% after " + (a.fwMs / 1000).toFixed(2) +
            "s (" + a.packets + " progress packets)", "ok");
        setStageSub("stunt · " + a.name + " · finished in " + (a.fwMs / 1000).toFixed(1) + "s");
      } else {
        // An unprovoked 100 cannot be this stunt finishing: a late packet from
        // the previous action is exactly what used to make a 9 s dance report
        // success in 47 ms. Keep waiting.
        log("ignored an unprovoked 100% for " + a.name + " (no start packet)", "warn");
      }
      return;
    }
    a.started = true;
    if (a.first === null) a.first = pct;
    a.peak = a.peak === null ? pct : Math.max(a.peak, pct);
    if (pct >= a.lastLogged + 10) {
      a.lastLogged = pct;
      log("  " + a.name + " " + pct + "%", "progress");
    }
    setStageSub("stunt · " + a.name + " · " + pct + "%");
  }

  // ------------------------------------------------------------------- BLE
  function connect() {
    if (state.connected || state.running) return;
    if (!navigator.bluetooth) {
      log("navigator.bluetooth is missing. Chrome on Android is required; Firefox and Safari have no Web Bluetooth.", "bad");
      setStatus("No Bluetooth API", "bad");
      return;
    }
    if (!window.isSecureContext) {
      log("This page is not a secure context, so the browser will refuse Bluetooth. Serve it from http://localhost (see the note below) instead of opening the file directly.", "bad");
      setStatus("Not a secure context", "bad");
      return;
    }

    var options = { optionalServices: [DATA.service] };
    if (els.chkAll.checked) {
      options.acceptAllDevices = true;
    } else {
      options.filters = [{ namePrefix: "K1" }, { services: [DATA.service] }];
    }

    setStatus("Choosing a device…", "wait");
    log("opening the Bluetooth chooser (pick K1-…)", "info");
    navigator.bluetooth.requestDevice(options).then(function (device) {
      state.device = device;
      device.addEventListener("gattserverdisconnected", onDisconnect);
      log("chosen: " + (device.name || "(no name)") + "  " + device.id, "info");
      setStatus("Connecting…", "wait");
      return device.gatt.connect();
    }).then(function (server) {
      state.server = server;
      return server.getPrimaryService(DATA.service);
    }).then(function (service) {
      return service.getCharacteristic(DATA.characteristic);
    }).then(function (ch) {
      state.char = ch;
      state.connected = true;
      log("characteristic ready: write=" + !!ch.properties.write +
          " writeWithoutResponse=" + !!ch.properties.writeWithoutResponse +
          " notify=" + !!ch.properties.notify, "ok");
      return ch.startNotifications();
    }).then(function () {
      state.char.addEventListener("characteristicvaluechanged", onValue);
      // K1.connect() enables autoStand on connect, so every story rehearsed on
      // the laptop ran with the robot's own posture management on. Ask for the
      // same thing here or the phone runs a different robot than the one the
      // owner approved: harmless for a frame, but it is the stance the actions
      // return to between stunts. Non-fatal: the story can still run if this
      // one toggle is refused.
      return sendFrame("autoStand", [1], "autoStand on").catch(function (err) {
        log("could not enable autoStand (" + describe(err) +
            "); continuing, but the robot may not hold its stance between stunts.", "warn");
      });
    }).then(function () {
      setStatus("Connected", "ok");
      log("notifications on. Keep this tab in the foreground for the whole story.", "ok");
      log("if the robot does not move, check that the K One app is closed — it holds the only connection.", "info");
      render();
    }).catch(function (err) {
      var name = err && err.name;
      if (name === "NotFoundError") {
        log("No device chosen.", "warn");
      } else if (name === "SecurityError") {
        log("Bluetooth was refused: " + describe(err) +
            ". If this page is a file:// URL, serve it from http://localhost instead.", "bad");
      } else {
        log("connect failed: " + describe(err), "bad");
      }
      cleanupConnection();
      setStatus("Disconnected", "off");
      render();
    });
  }

  function onDisconnect() {
    var wasRunning = state.running;
    log("the robot disconnected", "warn");
    state.abort = true;
    cleanupConnection();
    if (wasRunning) {
      state.blocked = true;
      log('It was playing when the link dropped. Watch the robot; when it is standing and still, tap "Robot is ready".', "warn");
    }
    setStatus("Disconnected", "off");
    render();
  }

  function cleanupConnection() {
    state.connected = false;
    state.char = null;
    state.server = null;
    state.action = null;
    state.stream = { remainder: new Uint8Array(0) };
    if (state.device && state.device.gatt && state.device.gatt.connected) {
      try { state.device.gatt.disconnect(); } catch (e) { /* already gone */ }
    }
  }

  function disconnect() {
    if (state.running) {
      log("stopping the story before disconnecting", "warn");
      stop();
    }
    if (state.device && state.device.gatt && state.device.gatt.connected) {
      try { state.device.gatt.disconnect(); } catch (e) { log(describe(e), "warn"); }
    }
    cleanupConnection();
    setStatus("Disconnected", "off");
    log("disconnected", "info");
    render();
  }

  // ---------------------------------------------------------------- runner
  function checkAbort() {
    if (state.abort) throw abortError();
  }

  function nap(seconds) {
    var ms = Math.max(0, Number(seconds) * 1000);
    return new Promise(function (resolve, reject) {
      var t0 = now();
      var id = setInterval(function () {
        if (state.abort) {
          clearInterval(id);
          reject(abortError());
          return;
        }
        if (now() - t0 >= ms) {
          clearInterval(id);
          resolve();
        }
      }, 100);
    });
  }

  // A step names either a robot.json action (`action`) or a card path
  // (`actionPath`). The path is the only name a card-only script has.
  function stepPath(step) {
    if (step.actionPath !== undefined) return String(step.actionPath);
    return DATA.actions[step.action];
  }

  function stepName(step) {
    var path = stepPath(step);
    return step.action !== undefined ? step.action
         : String(path || "").replace(/^.*\//, "");
  }

  async function performAction(step) {
    var name = stepName(step);
    var path = stepPath(step);
    if (!path) throw new Error("no wire path for action " + JSON.stringify(name));
    var grace = Number(step.grace);
    if (!isFinite(grace) || grace <= 0) grace = 30;

    state.action = {
      name: name, started: false, done: false, peak: null, first: null,
      t0: now(), fwMs: null, warned: false, packets: 0, lastLogged: -10
    };
    setStageSub("stunt · " + name + " · sent");

    await sendFrame("action", path, "action " + path);
    var outcome = await waitCompletion(grace);
    if (outcome === "timeout") {
      throw new Error(
        name + " did not report completion within " + grace.toFixed(0) +
        "s. The robot may still be animating — do not send another command " +
        "until it is standing and still. Reconnect when it has settled."
      );
    }
    var a = state.action;
    log(name + " finished (" + outcome + ")", "ok");
    if (a && a.peak !== null) {
      log("  trace: " + a.first + "% → " + a.peak + "% in " + a.packets + " packets", "progress");
    }
  }

  async function waitCompletion(grace) {
    var startedAt = now();
    var deadline = startedAt + grace * 1000;
    while (true) {
      checkAbort();
      var a = state.action;
      if (a && a.done) return "progress-100";
      var t = now();
      if (a && !a.warned && !a.started && t - startedAt > 12000) {
        a.warned = true;
        log("no progress packet after 12 s. Still waiting to " + grace.toFixed(0) +
            "s; the robot may be animating without reporting.", "warn");
      }
      if (t >= deadline) return "timeout";
      await sleep(50);
    }
  }

  // Rehearsal walks the same timeline without a robot. A stunt's house clock
  // is `grace - GRACE_S` (the generator gives every action measured + 15 s),
  // so this is the length the owner actually watched.
  async function rehearseAction(step) {
    var name = stepName(step);
    var seconds = Math.max(0.5, (Number(step.grace) || 30) - 15);
    var t0 = now();
    log("(rehearsal) " + name + " would run about " + seconds.toFixed(1) + "s", "info");
    while (true) {
      checkAbort();
      var elapsed = (now() - t0) / 1000;
      if (elapsed >= seconds) break;
      var pct = Math.min(99, Math.floor(elapsed / seconds * 100));
      setStageSub("stunt · " + name + " · " + pct + "% (rehearsal)");
      await sleep(100);
    }
    setStageSub("stunt · " + name + " · finished (rehearsal)");
  }

  async function runStory(story, rehearsal) {
    var steps = story.steps || [];
    var notes = story.notes || [];
    var lastNote = "";
    var lastActionDone = null;

    for (var i = 0; i < steps.length; i++) {
      checkAbort();
      var step = steps[i];

      if (step.wait !== undefined) {
        setCaption(lastNote, "pause");
        setStageSub("pause " + Number(step.wait).toFixed(1) + "s");
        await nap(Number(step.wait));
      } else if (step.speak !== undefined) {
        lastNote = notes[i] || step.speak;
        setCaption(lastNote, "speech");
        setStageSub("narration · " + step.speak);
        log("line " + step.speak + (notes[i] ? " — " + notes[i] : ""), "line");
        if (rehearsal) {
          log("(rehearsal) audio " + step.speak + " would play from the card", "info");
        } else {
          await sendFrame("audio", step.speak, "audio " + step.speak);
        }
      } else if (step.action !== undefined || step.actionPath !== undefined) {
        var name = stepName(step);
        lastNote = name;
        if (lastActionDone !== null) {
          var gap = (now() - lastActionDone) / 1000;
          if (gap < DATA.settle) {
            var extra = DATA.settle - gap;
            log("settle backstop: " + extra.toFixed(1) + "s more before " + name, "warn");
            await nap(extra);
          }
        }
        setCaption(name, "action");
        setStageSub("stunt · " + name);
        log("stunt " + name + (rehearsal ? " (rehearsal)" : ""), "stunt");
        if (rehearsal) {
          await rehearseAction(step);
        } else {
          await performAction(step);
        }
        lastActionDone = now();
        state.lastActionEnd = lastActionDone;
      } else {
        throw new Error("unknown step " + JSON.stringify(step));
      }

      // i_routine's breath after each intent (not after an explicit wait).
      if (DATA.gap > 0) await nap(DATA.gap);
    }
  }

  // A single move from the menu: same wire rules as a story beat, but with no
  // narration or waits in front of it. The 6 s settle is enforced here too,
  // because a stunt frame sent too soon after the previous one is dropped.
  async function settleFirst(name) {
    if (state.lastActionEnd === null) return;
    var gap = (now() - state.lastActionEnd) / 1000;
    if (gap >= DATA.settle) return;
    var extra = DATA.settle - gap;
    log("settle backstop: " + extra.toFixed(1) + "s more before " + name, "warn");
    setStageSub("settle " + extra.toFixed(1) + "s");
    await nap(extra);
  }

  async function runMove(move, rehearsal) {
    var step = { actionPath: move.path, grace: move.grace };
    await settleFirst(move.name);
    if (rehearsal) await rehearseAction(step);
    else await performAction(step);
    state.lastActionEnd = now();
  }

  function playMove(move) {
    if (!move || state.running) return;
    if (state.blocked) {
      log('Stop was sent. Watch the robot until it is standing, then tap "Robot is ready".', "warn");
      return;
    }
    var rehearsal = els.chkRehearse.checked;
    if (!rehearsal && !state.connected) {
      log("Not connected. Tap Connect first, or tick Rehearse to walk the move without the robot.", "bad");
      setStatus("Disconnected", "off");
      return;
    }

    state.running = true;
    state.abort = false;
    state.blocked = false;
    state.runId += 1;
    var runId = state.runId;
    state.run = { move: move, t0: now(), expected: move.seconds || 5 };

    els.stage.hidden = false;
    els.stageTitle.textContent = move.name + (rehearsal ? " · rehearsal" : "");
    setStageState("playing", "live");
    setCaption(move.name, "action");
    setStageSub("move · " + move.path);
    els.barFill.style.width = "0%";
    els.timeNow.textContent = "0:00";
    els.timeTotal.textContent = move.seconds ? mmss(move.seconds) : "—";
    log("move " + move.name + " (" + move.path + ")" +
        (rehearsal ? " (rehearsal)" : ""), "stunt");
    render();
    acquireWake();

    var ticker = setInterval(function () {
      if (!state.run) return;
      var elapsed = (now() - state.run.t0) / 1000;
      var frac = Math.min(0.999, elapsed / Math.max(1, state.run.expected));
      els.barFill.style.width = (frac * 100).toFixed(1) + "%";
      els.timeNow.textContent = mmss(elapsed);
    }, 200);

    runMove(move, rehearsal).then(function () {
      if (!state.abort) {
        els.barFill.style.width = "100%";
        setStageState("finished", "ok");
        setStageSub("finished");
        setCaption("Finished.", "done");
        log("move finished — watch the robot until it is still.", "ok");
      }
    }).catch(function (err) {
      if (err && err.name === "AbortError") {
        setStageState("stopped", "warn");
        setStageSub("stopped");
        log("move stopped.", "warn");
      } else {
        setStageState("aborted", "bad");
        setStageSub(describe(err));
        log("ABORTED: " + describe(err), "bad");
        state.blocked = true;
        log('Do not start another move yet. Watch the robot; when it is standing and still, tap "Robot is ready".', "warn");
      }
    }).then(function () {
      if (runId !== state.runId) return;
      clearInterval(ticker);
      state.running = false;
      if (!state.abort) state.action = null;
      releaseWake();
      render();
    });
  }

  // -------------------------------------------------------------- playback
  function play(id) {
    if (state.running) return;
    if (state.blocked) {
      log('Stop was sent. Watch the robot until it is standing, then tap "Robot is ready".', "warn");
      return;
    }
    var rehearsal = els.chkRehearse.checked;
    if (!rehearsal && !state.connected) {
      log("Not connected. Tap Connect first, or tick Rehearse to watch the timing without the robot.", "bad");
      setStatus("Disconnected", "off");
      return;
    }
    var story = null;
    for (var i = 0; i < DATA.stories.length; i++) {
      if (DATA.stories[i].id === id) story = DATA.stories[i];
    }
    if (!story) { log("unknown story " + id, "bad"); return; }

    state.running = true;
    state.abort = false;
    state.blocked = false;
    state.runId += 1;
    var runId = state.runId;
    state.run = { story: story, t0: now(), expected: story.expected };

    els.stage.hidden = false;
    els.stageTitle.textContent = story.title + (rehearsal ? " · rehearsal" : "");
    setStageState("playing", "live");
    setCaption("", "pause");
    setStageSub("starting");
    els.barFill.style.width = "0%";
    els.timeNow.textContent = "0:00";
    els.timeTotal.textContent = mmss(story.expected);
    log("starting " + story.title + " · " + mmss(story.expected) +
        (rehearsal ? " (rehearsal, no robot)" : " (robot)"), "ok");
    render();
    acquireWake();

    var ticker = setInterval(function () {
      if (!state.run) return;
      var elapsed = (now() - state.run.t0) / 1000;
      var frac = Math.min(0.999, elapsed / Math.max(1, state.run.expected));
      els.barFill.style.width = (frac * 100).toFixed(1) + "%";
      els.timeNow.textContent = mmss(elapsed);
    }, 200);

    runStory(story, rehearsal).then(function () {
      if (!state.abort) {
        var elapsed = (now() - state.run.t0) / 1000;
        els.barFill.style.width = "100%";
        els.timeNow.textContent = mmss(elapsed);
        setStageState("finished", "ok");
        setStageSub("finished in " + mmss(elapsed));
        setCaption("Finished.", "done");
        log("story finished in " + mmss(elapsed) + " — the robot should be standing.", "ok");
      }
    }).catch(function (err) {
      if (err && err.name === "AbortError") {
        setStageState("stopped", "warn");
        setStageSub("stopped");
        log("story stopped.", "warn");
      } else {
        setStageState("aborted", "bad");
        setStageSub(describe(err));
        log("ABORTED: " + describe(err), "bad");
        state.blocked = true;
        log('Do not start another story yet. Watch the robot; when it is standing and still, tap "Robot is ready".', "warn");
      }
    }).then(function () {
      if (runId !== state.runId) return;
      clearInterval(ticker);
      state.running = false;
      if (!state.abort) state.action = null;
      releaseWake();
      render();
    });
  }

  function stop() {
    if (!state.running) {
      log("nothing is playing.", "info");
      return;
    }
    state.abort = true;
    log("STOP pressed", "warn");
    if (state.connected) {
      sendFrame("stop", null, "STOP").then(function () {
        log("stop frame sent. The robot may need a moment to settle.", "ok");
      }).catch(function (err) {
        log("could not send the stop frame: " + describe(err), "bad");
      });
    } else {
      log("(rehearsal) no robot to stop.", "info");
    }
    state.blocked = true;
    setStageState("stopping", "warn");
    setStageSub("stop sent — watch the robot");
    render();
  }

  function ready() {
    state.blocked = false;
    els.stage.hidden = true;
    state.run = null;
    state.action = null;
    log("cleared. You can start again.", "ok");
    render();
  }

  // ------------------------------------------------------------ wake lock
  async function acquireWake() {
    if (!navigator.wakeLock) return;
    try {
      state.wake = await navigator.wakeLock.request("screen");
      log("screen wake lock held", "info");
      state.wake.addEventListener("release", function () { state.wake = null; });
    } catch (err) {
      log("could not hold the screen awake: " + describe(err) + " — keep the tab in front and the screen on.", "warn");
    }
  }

  function releaseWake() {
    if (state.wake) {
      try { state.wake.release(); } catch (e) { /* gone */ }
      state.wake = null;
    }
  }

  // ------------------------------------------------------------------- UI
  function render() {
    els.btnConnect.disabled = state.connected || state.running;
    els.btnDisconnect.disabled = !state.connected || state.running;
    els.chkAll.disabled = state.connected || state.running;
    els.chkRehearse.disabled = state.running;
    els.chkWithResponse.disabled = state.running;
    els.btnStop.disabled = !state.running;
    els.btnReady.hidden = !state.blocked;
    els.chkAll.parentNode.classList.toggle("muted", state.connected);

    var buttons = els.stories.querySelectorAll("button.play");
    for (var i = 0; i < buttons.length; i++) buttons[i].disabled = state.running || state.blocked;

    els.moveSearch.disabled = state.running;
    els.moveAll.disabled = state.running;
    els.moveCard.disabled = state.running;
    els.moveProAction.disabled = state.running;
    els.moveAction.disabled = state.running;
    var moveButtons = els.moveGrid.querySelectorAll("button.move");
    for (var j = 0; j < moveButtons.length; j++) {
      moveButtons[j].disabled = state.running || state.blocked;
    }

    if (state.connected) els.connectHint.textContent = "Connected. Keep this tab in the foreground while a story runs; a background tab can have its timers throttled.";
    else if (els.chkRehearse.checked) els.connectHint.textContent = "Rehearsal: the timeline, captions and stunts step through without a robot. Nothing is sent.";
    else els.connectHint.textContent = "Close the K One app first — the robot accepts one Bluetooth connection. Tap Connect, pick K1-…, then keep this tab in front.";
  }

  function buildStories() {
    DATA.stories.forEach(function (story) {
      var card = document.createElement("article");
      card.className = "card story";

      var head = document.createElement("div");
      head.className = "storyHead";
      var title = document.createElement("h2");
      title.textContent = story.title;
      head.appendChild(title);
      var lang = document.createElement("span");
      lang.className = "chip";
      lang.textContent = story.language;
      head.appendChild(lang);
      card.appendChild(head);

      var meta = document.createElement("p");
      meta.className = "meta";
      meta.textContent = mmss(story.expected) + " · " +
        story.stunt_count + " stunts · " + story.clip_count + " clips";
      card.appendChild(meta);

      var blurb = document.createElement("p");
      blurb.className = "blurb";
      blurb.textContent = story.blurb;
      card.appendChild(blurb);

      var row = document.createElement("div");
      row.className = "row";
      var btn = document.createElement("button");
      btn.className = "primary play";
      btn.textContent = "Play";
      btn.addEventListener("click", function () { play(story.id); });
      row.appendChild(btn);
      var info = document.createElement("span");
      info.className = "clipnote";
      info.textContent = "needs " + story.clip_range + " on the card";
      row.appendChild(info);
      card.appendChild(row);

      els.stories.appendChild(card);
    });
  }

  var moveFilter = "all";
  var moveQuery = "";
  var MOVE_GROUPS = ["Card", "ProAction", "Action"];
  var MOVE_GROUP_LABELS = {
    Card: "Card & custom", ProAction: "Stunts", Action: "Shows"
  };

  function moveSub(move) {
    var bits = [(move.seconds === null ? "clock unknown"
      : (move.seconds >= 60 ? mmss(move.seconds) : move.seconds.toFixed(1) + " s"))];
    if (move.note) bits.push(move.note);
    else if (move.verified === "no") bits.push("no motion observed");
    return bits.join(" · ");
  }

  function setMoveFilter(group) {
    moveFilter = group;
    var map = { all: els.moveAll, Card: els.moveCard,
                ProAction: els.moveProAction, Action: els.moveAction };
    for (var key in map) {
      map[key].className = "chipbtn" + (key === group ? " on" : "");
    }
    renderMoves();
  }

  function renderMoves() {
    els.moveGrid.textContent = "";
    var q = moveQuery.trim().toLowerCase();
    var shown = 0;

    MOVE_GROUPS.forEach(function (group) {
      if (moveFilter !== "all" && moveFilter !== group) return;
      var items = DATA.moves.filter(function (move) {
        if (move.group !== group) return false;
        if (!q) return true;
        return move.name.toLowerCase().indexOf(q) !== -1 ||
               move.path.toLowerCase().indexOf(q) !== -1;
      });
      if (!items.length) return;
      shown += items.length;

      var block = document.createElement("div");
      block.className = "moveGroup";
      var head = document.createElement("h3");
      head.textContent = MOVE_GROUP_LABELS[group] || group;
      block.appendChild(head);

      var grid = document.createElement("div");
      grid.className = "moveGrid";
      items.forEach(function (move) {
        var caution = /seated|assumes it is seated/.test(move.note || "");
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "move" + (caution ? " caution" : "") +
                        (move.verified === null ? " untested" : "");
        btn.title = move.path + (move.note ? " — " + move.note : "");
        var name = document.createElement("span");
        name.className = "nm";
        name.textContent = move.name;
        var sub = document.createElement("span");
        sub.className = "sub";
        sub.textContent = moveSub(move);
        btn.appendChild(name);
        btn.appendChild(sub);
        btn.addEventListener("click", function () { playMove(move); });
        grid.appendChild(btn);
      });
      block.appendChild(grid);
      els.moveGrid.appendChild(block);
    });

    if (!shown) {
      var empty = document.createElement("p");
      empty.className = "moveEmpty";
      empty.textContent = q ? "No move matches “" + moveQuery + "”."
                            : "No moves in this group.";
      els.moveGrid.appendChild(empty);
    }
    els.moveCount.textContent = shown + (shown === 1 ? " move" : " moves");
  }

  function diagnostics() {
    var rows = [
      ["secure context", String(window.isSecureContext) + (window.isSecureContext ? "" : " — Bluetooth will be refused; serve from http://localhost")],
      ["navigator.bluetooth", navigator.bluetooth ? "present" : "missing — use Chrome on Android"],
      ["origin", String(location.origin || "(file)")],
      ["wake lock", navigator.wakeLock ? "available" : "not available"],
      ["user agent", navigator.userAgent]
    ];
    rows.forEach(function (r) {
      var div = document.createElement("div");
      div.className = "diagrow";
      var k = document.createElement("span");
      k.className = "k";
      k.textContent = r[0];
      var v = document.createElement("span");
      v.className = "v";
      v.textContent = r[1];
      div.appendChild(k);
      div.appendChild(v);
      els.diag.appendChild(div);
    });
    if (!navigator.bluetooth || !window.isSecureContext) {
      els.envWarn.hidden = false;
    }
  }

  function copyLog() {
    var text = els.log.innerText;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        log("log copied to the clipboard", "ok");
      }).catch(function (err) {
        log("could not copy: " + describe(err), "warn");
      });
    } else {
      log("clipboard API unavailable; select the log text manually.", "warn");
    }
  }

  // ------------------------------------------------------------------ boot
  function init() {
    els = {
      statusText: $("statusText"),
      statusDot: $("statusDot"),
      connectHint: $("connectHint"),
      btnConnect: $("btnConnect"),
      btnDisconnect: $("btnDisconnect"),
      chkAll: $("chkAll"),
      chkRehearse: $("chkRehearse"),
      chkWithResponse: $("chkWithResponse"),
      stories: $("stories"),
      moveCount: $("moveCount"),
      moveSearch: $("moveSearch"),
      moveGrid: $("moveGrid"),
      moveAll: $("moveAll"),
      moveCard: $("moveCard"),
      moveProAction: $("moveProAction"),
      moveAction: $("moveAction"),
      stage: $("stage"),
      stageTitle: $("stageTitle"),
      stageState: $("stageState"),
      caption: $("caption"),
      captionSub: $("captionSub"),
      barFill: $("barFill"),
      timeNow: $("timeNow"),
      timeTotal: $("timeTotal"),
      btnStop: $("btnStop"),
      btnReady: $("btnReady"),
      log: $("log"),
      diag: $("diag"),
      envWarn: $("envWarn"),
      btnCopyLog: $("btnCopyLog"),
      btnClearLog: $("btnClearLog")
    };

    els.btnConnect.addEventListener("click", connect);
    els.btnDisconnect.addEventListener("click", disconnect);
    els.btnStop.addEventListener("click", stop);
    els.btnReady.addEventListener("click", ready);
    els.chkRehearse.addEventListener("change", render);
    els.chkAll.addEventListener("change", render);
    els.btnCopyLog.addEventListener("click", copyLog);
    els.btnClearLog.addEventListener("click", function () { els.log.textContent = ""; });

    els.moveAll.addEventListener("click", function () { setMoveFilter("all"); });
    els.moveCard.addEventListener("click", function () { setMoveFilter("Card"); });
    els.moveProAction.addEventListener("click", function () { setMoveFilter("ProAction"); });
    els.moveAction.addEventListener("click", function () { setMoveFilter("Action"); });
    els.moveSearch.addEventListener("input", function () {
      moveQuery = els.moveSearch.value;
      renderMoves();
    });

    buildStories();
    renderMoves();
    diagnostics();
    setStatus("Disconnected", "off");
    render();

    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible" && state.running && !state.wake) acquireWake();
      if (document.visibilityState !== "visible" && state.running) {
        log("the tab went to the background; timing may be throttled until it returns.", "warn");
      }
    });

    log("ready. The robot must be powered on and the K One app closed.", "info");
    if (!window.isSecureContext) {
      log("this page is not a secure context: Chrome will refuse navigator.bluetooth.requestDevice.", "bad");
    }
    if (!navigator.bluetooth) {
      log("this browser has no Web Bluetooth. Chrome on Android (or desktop Chrome) is required.", "bad");
    }
  }

  // Exposed for the console and for tests that load this file under a stub DOM.
  window.K1Player = {
    play: play,
    playMove: playMove,
    stop: stop,
    ready: ready,
    connect: connect,
    disconnect: disconnect,
    state: state,
    data: DATA
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
