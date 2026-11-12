/* Inbox cleanup review page. No Gmail access: builds a plan for Zero. */
(function () {
  "use strict";
  var META = JSON.parse(document.getElementById("cl-meta").textContent);
  var STACKS = JSON.parse(document.getElementById("cl-data").textContent);
  var BY_ID = {}; STACKS.forEach(function (s) { BY_ID[s.id] = s; });
  var PREFIX = "cleanup:" + META.dataset_id + ":";
  var KEY = PREFIX + META.chunk, UNSUB_KEY = PREFIX + "unsub", KEEP_KEY = PREFIX + "keepsenders";
  var UNSUB = { kill: 1, archive_unsub: 1, delete_unsub: 1 };
  var HIST = { kill: 1, delete_hist: 1 };
  var DESTRUCTIVE = { kill: 1, delete: 1, delete_unsub: 1, delete_hist: 1 };
  var ORDER = ["archive", "rule", "keep", "delete", "delete_unsub", "kill", "delete_hist", "archive_unsub"];

  function load(k, d) { try { var v = JSON.parse(localStorage.getItem(k) || "null"); return v || d; } catch (e) { return d; } }
  function save(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { status("Could not save in this browser: " + e.message, true); } }
  var state = load(KEY, { stacks: {}, emails: {}, reviewed: {}, submitted: null });
  state.stacks = state.stacks || {}; state.emails = state.emails || {}; state.reviewed = state.reviewed || {};

  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function sec(id) { return $$(".cl-stack").filter(function (e) { return e.dataset.sid === id; })[0]; }
  function status(msg, bad) { var el = $("#cl-status"); el.textContent = msg; el.classList.toggle("bad", !!bad); }

  function chosen(s) { var a = state.stacks[s.id]; return (a && s.allowed.indexOf(a) >= 0) ? a : s.default; }

  /* Keep anywhere for a sender (this chunk or another chunk) blocks delete-history. */
  function keepSenders() {
    var g = load(KEEP_KEY, {}), out = {};
    Object.keys(g).forEach(function (snd) { (g[snd] || []).forEach(function (slug) { if (slug !== META.chunk) out[snd] = "another sitting"; }); });
    STACKS.forEach(function (s) {
      if (chosen(s) === "keep") out[s.sender] = out[s.sender] || "you chose Keep for " + (s.name || s.sender);
      s.items.forEach(function (it) { if (state.emails[it.tid] === "keep") out[s.sender] = "you marked an email Keep (“" + (it.subject || "").slice(0, 50) + "”)"; });
    });
    return out;
  }
  function syncKeepGlobal(ks) {
    var g = load(KEEP_KEY, {});
    Object.keys(g).forEach(function (snd) { g[snd] = (g[snd] || []).filter(function (x) { return x !== META.chunk; }); if (!g[snd].length) delete g[snd]; });
    Object.keys(ks).forEach(function (snd) { if (ks[snd] !== "another sitting") { g[snd] = g[snd] || []; g[snd].push(META.chunk); } });
    save(KEEP_KEY, g);
  }

  function effective(s, ks) {
    var a = chosen(s);
    if (HIST[a] && ks[s.sender]) {
      var to = a === "kill" ? (s.newsletter && s.allowed.indexOf("delete_unsub") >= 0 ? "delete_unsub" : "delete") : "delete";
      if (s.allowed.indexOf(to) < 0) to = "archive";
      return { action: to, from: a, reason: "Changed from " + META.labels[a] + " to " + META.labels[to] + " because " + ks[s.sender] + ": past mail from this sender will not be deleted." };
    }
    return { action: a };
  }

  function syncUnsub(ks) {
    var g = load(UNSUB_KEY, {});
    STACKS.forEach(function (s) {
      var e = effective(s, ks), cur = g[s.sender];
      if (UNSUB[e.action]) g[s.sender] = { name: s.name || s.sender, unsub: s.unsub || null, chunk: META.chunk };
      else if (cur && cur.chunk === META.chunk) delete g[s.sender];
    });
    save(UNSUB_KEY, g); return g;
  }

  /* Update attributes/text only, never rebuild DOM, so focus stays put. */
  function refresh() {
    var ks = keepSenders(); syncKeepGlobal(ks); var g = syncUnsub(ks);
    STACKS.forEach(function (s) {
      var el = sec(s.id); if (!el) return;
      var e = effective(s, ks), n = $(".cl-downgrade", el);
      if (e.from) { n.hidden = false; n.textContent = e.reason; } else if (!n.hidden) { n.hidden = true; n.textContent = ""; }
      el.classList.toggle("is-downgraded", !!e.from);
      $(".cl-badge.srcunsub", el).hidden = !(g[s.sender] && !UNSUB[e.action]);
      el.classList.toggle("is-reviewed", !!state.reviewed[s.id]);
      el.dataset.effective = e.action;
    });
    var done = STACKS.filter(function (s) { return state.reviewed[s.id]; }).length;
    $("#cl-progress").value = done; $("#cl-progress-text").textContent = done + " / " + STACKS.length + (state.submitted ? " · submitted" : "");
  }

  /* restore */
  STACKS.forEach(function (s) {
    var el = sec(s.id); if (!el) return;
    var a = chosen(s), r = $$('input[type=radio]', $(".cl-radios", el)).filter(function (i) { return i.value === a; })[0];
    if (r) r.checked = true;
    $$(".cl-email", el).forEach(function (li) {
      var v = state.emails[li.dataset.tid] || "";
      $$("input", li).forEach(function (i) { i.checked = i.value === v; });
    });
  });

  document.addEventListener("change", function (ev) {
    var t = ev.target; if (!t.matches || !t.matches("input[type=radio]")) return;
    var el = t.closest(".cl-stack"); if (!el) return;
    var s = BY_ID[el.dataset.sid];
    if (t.closest(".cl-radios")) { state.stacks[s.id] = t.value; }
    else { var tid = t.closest(".cl-email").dataset.tid; if (t.value) state.emails[tid] = t.value; else delete state.emails[tid]; }
    state.reviewed[s.id] = 1; save(KEY, state); refresh();
  });
  document.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest(".cl-title"); if (!b) return;
    var open = b.getAttribute("aria-expanded") !== "true";
    b.setAttribute("aria-expanded", String(open));
    document.getElementById(b.getAttribute("aria-controls")).hidden = !open;
  });
  $("#cl-resume").addEventListener("click", function () {
    var s = STACKS.filter(function (x) { return !state.reviewed[x.id]; })[0];
    if (!s) { status("Every sender in this sitting is decided."); $("#cl-review").focus(); return; }
    var el = sec(s.id); el.scrollIntoView({ block: "center" }); ($(".cl-radios input:checked", el) || $(".cl-title", el)).focus();
  });
  $("#cl-accept").addEventListener("click", function () {
    var n = 0; STACKS.forEach(function (s) { if (!state.reviewed[s.id]) { state.reviewed[s.id] = 1; n++; } });
    save(KEY, state); refresh(); status(n + " senders set to their defaults.");
  });

  /* ---- plan ---- */
  function canon(v) {
    if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
    if (v && typeof v === "object") return "{" + Object.keys(v).sort().map(function (k) { return JSON.stringify(k) + ":" + canon(v[k]); }).join(",") + "}";
    return JSON.stringify(v === undefined ? null : v);
  }
  function sha256hex(str) {
    var bytes = new TextEncoder().encode(str);
    var K = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
    var H = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    var l = bytes.length, nb = ((l + 9 + 63) >> 6) << 6, m = new Uint8Array(nb); m.set(bytes); m[l] = 0x80;
    var bits = l * 8; for (var i = 0; i < 8; i++) m[nb - 1 - i] = (i < 4) ? (bits >>> (8 * i)) & 255 : Math.floor(bits / Math.pow(2, 8 * i)) & 255;
    var W = new Array(64);
    for (var o = 0; o < nb; o += 64) {
      for (i = 0; i < 16; i++) W[i] = (m[o + 4 * i] << 24) | (m[o + 4 * i + 1] << 16) | (m[o + 4 * i + 2] << 8) | m[o + 4 * i + 3];
      for (i = 16; i < 64; i++) {
        var a0 = W[i - 15], a1 = W[i - 2];
        var s0 = ((a0 >>> 7) | (a0 << 25)) ^ ((a0 >>> 18) | (a0 << 14)) ^ (a0 >>> 3);
        var s1 = ((a1 >>> 17) | (a1 << 15)) ^ ((a1 >>> 19) | (a1 << 13)) ^ (a1 >>> 10);
        W[i] = (W[i - 16] + s0 + W[i - 7] + s1) | 0;
      }
      var a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], h = H[7];
      for (i = 0; i < 64; i++) {
        var S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
        var t1 = (h + S1 + ((e & f) ^ (~e & g)) + K[i] + W[i]) | 0;
        var S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
        var t2 = (S0 + ((a & b) ^ (a & c) ^ (b & c))) | 0;
        h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      H[0] = (H[0] + a) | 0; H[1] = (H[1] + b) | 0; H[2] = (H[2] + c) | 0; H[3] = (H[3] + d) | 0;
      H[4] = (H[4] + e) | 0; H[5] = (H[5] + f) | 0; H[6] = (H[6] + g) | 0; H[7] = (H[7] + h) | 0;
    }
    return H.map(function (x) { return ("00000000" + (x >>> 0).toString(16)).slice(-8); }).join("");
  }
  window.__clSha256 = sha256hex;

  function unsubEntry(sender, name, u) {
    u = u || {};
    var method = u.one_click ? "one-click" : (u.url ? "web" : (u.mailto ? "mailto" : "none"));
    // No Dodosend search URL form is known, and this helper has no thread id,
    // so leave the Gmail search link as mail.google.com.
    return { source: sender, name: name, method: method, url: u.url || null, mailto: u.mailto || null, auto_allowed: !!u.one_click,
             gmail_search: "https://mail.google.com/mail/u/0/#search/" + encodeURIComponent("from:" + sender) };
  }

  function buildPlan() {
    var ks = keepSenders(), stacks = [], totals = { archive: 0, rule: 0, keep: 0, trash: 0, history: 0, unsub: 0, oneclick: 0, stacks: 0 };
    var unsubs = {};
    STACKS.forEach(function (s) {
      var e = effective(s, ks), ov = {};
      s.items.forEach(function (it) { if (state.emails[it.tid]) ov[it.tid] = state.emails[it.tid]; });
      var st = { id: s.id, sender: s.sender, kind: s.kind, money: !!s.money, action: e.action, default_action: s.default,
                 changed: e.action !== s.default, threads: s.items.map(function (i) { return i.tid; }), overrides: ov,
                 unsub: !!UNSUB[e.action], delete_history: !!HIST[e.action] };
      if (e.from) { st.downgraded_from = e.from; st.downgrade_reason = e.reason; }
      stacks.push(st); totals.stacks++;
      s.items.forEach(function (it) {
        var a = ov[it.tid] || e.action;
        if (a === "keep") totals.keep++; else if (a === "rule") totals.rule++;
        else if (a === "archive" || a === "archive_unsub") totals.archive++; else totals.trash++;
      });
      if (HIST[e.action]) totals.history++;
      if (UNSUB[e.action]) unsubs[s.sender] = unsubEntry(s.sender, s.name || s.sender, s.unsub);
    });
    var ulist = Object.keys(unsubs).sort().map(function (k) { return unsubs[k]; });
    totals.unsub = ulist.length; totals.oneclick = ulist.filter(function (u) { return u.auto_allowed; }).length;
    var rand = (crypto.getRandomValues ? Array.prototype.map.call(crypto.getRandomValues(new Uint8Array(4)), function (b) { return ("0" + b.toString(16)).slice(-2); }).join("") : Math.random().toString(16).slice(2, 10));
    var plan = {
      schema: "zero-cleanup-plan/v2", version: 2,
      plan_id: (META.dataset_id + "-" + META.chunk + "-" + rand).replace(/[^A-Za-z0-9._-]/g, "-"),
      generated_at: new Date().toISOString(),
      source: { chunk: META.chunk, dataset_id: META.dataset_id, dataset_sha256: META.dataset_sha256 },
      order: ["archive", "confirm", "trash", "delete_history", "unsubscribe"],
      semantics: { delete: "trash", archive_marks_read: false, unsub_auto_only_one_click: true, rule_actions_not_executed: true },
      stacks: stacks, unsubscribe: ulist, totals: totals
    };
    plan.checksum = "sha256:" + sha256hex(canon(plan));
    return plan;
  }
  window.__clBuildPlan = buildPlan;

  function zeroText(plan) {
    var b64 = btoa(unescape(encodeURIComponent(JSON.stringify(plan))));
    var t = plan.totals;
    return "Zero: run this Gmail cleanup plan (" + plan.plan_id + "). Archive first; show me counts and wait for my OK before any Trash/Unsub. Trash only, never permanent delete. Unsubscribe only via one-click.\n" +
      "PLAN-JSON-BASE64:" + b64 + "\n" +
      "Summary: archive " + t.archive + ", trash " + t.trash + ", delete-history senders " + t.history + ", unsub " + t.unsub + " (" + t.oneclick + " one-click), keep " + t.keep + ", left to rules " + t.rule + ". " + plan.checksum;
  }

  $("#cl-copy").addEventListener("click", function () {
    var txt = zeroText(buildPlan()), fb = $("#cl-copy-fallback"), ta = $("#cl-copy-text");
    function fallback(err) { fb.hidden = false; ta.value = txt; ta.focus(); ta.select(); status("Copy failed (" + err + "). The text is selected below — press Ctrl/Cmd+C.", true); }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(txt).then(function () { fb.hidden = true; status("Copied plan for Zero (" + txt.length + " characters)."); }, function (e) { fallback(e && e.message || "blocked"); });
    } else fallback("clipboard not available on this connection");
  });
  $("#cl-download").addEventListener("click", function () {
    var p = buildPlan(), a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(p, null, 1)], { type: "application/json" }));
    a.download = p.plan_id + ".json"; document.body.appendChild(a); a.click(); a.remove();
    status("Downloaded " + p.plan_id + ".json");
  });

  var pending = null;
  $("#cl-review").addEventListener("click", function () {
    pending = buildPlan(); var t = pending.totals, box = $("#cl-confirm");
    Object.keys(t).forEach(function (k) { $$('[data-k="' + k + '"]', box).forEach(function (e) { e.textContent = t[k]; }); });
    var destructive = t.trash + t.history + t.unsub > 0;
    $("#cl-ack-row").hidden = !destructive; $("#cl-ack").checked = false;
    box.hidden = false; box.focus();
  });
  $("#cl-cancel").addEventListener("click", function () { $("#cl-confirm").hidden = true; $("#cl-review").focus(); });
  $("#cl-submit").addEventListener("click", function () {
    var t = pending.totals;
    if (t.trash + t.history + t.unsub > 0 && !$("#cl-ack").checked) { status("Tick “I understand” first — this plan trashes or unsubscribes.", true); $("#cl-ack").focus(); return; }
    var btn = this; btn.disabled = true; status("Submitting…");
    fetch("/cleanup/plan", { method: "POST", headers: { "Content-Type": "application/json", "HX-Request": "true" }, body: JSON.stringify(pending), credentials: "same-origin" })
      .then(function (r) { return r.json().catch(function () { return { ok: false, errors: ["HTTP " + r.status] }; }).then(function (j) { j._status = r.status; return j; }); })
      .then(function (j) {
        btn.disabled = false;
        if (!j.ok) { status("Not submitted: " + (j.errors || [j.detail || ("HTTP " + j._status)]).join("; ") + ". Use Copy or Download instead.", true); return; }
        state.submitted = { plan_id: j.plan_id, at: new Date().toISOString() }; save(KEY, state);
        $("#cl-confirm").hidden = true; refresh();
        status("Submitted " + j.plan_id + (j.queued_for ? " — queued for Zero." : " — saved (Zero bot not found; tell Zero manually)."));
        showUnsub(j.unsubscribe || pending.unsubscribe, true);
      })
      .catch(function (e) { btn.disabled = false; status("Network error: " + e.message + ". Use Copy or Download instead.", true); });
  });

  // M-5: only follow http(s)/mailto links taken from email headers (no javascript:/data:).
  function okUrl(s, schemes) {
    try { return schemes.indexOf(new URL(String(s), location.href).protocol) >= 0; } catch (e) { return false; }
  }
  function showUnsub(list, after) {
    var sec2 = $("#cl-unsub"), ul = $("#cl-unsub-list"); ul.textContent = "";
    var hand = list.filter(function (u) { return !u.auto_allowed; }), auto = list.length - hand.length;
    $("#cl-unsub-h").textContent = after ? "To unsubscribe by hand" : "Where to unsubscribe";
    $("#cl-unsub-intro").textContent = auto + " source(s) have one-click unsubscribe; Zero does those after you confirm. " +
      (hand.length ? hand.length + " need you (Zero will not guess):" : "Nothing to do by hand.");
    hand.forEach(function (u) {
      var li = document.createElement("li"), b = document.createElement("strong"); b.textContent = u.name + " "; li.appendChild(b);
      var small = document.createElement("span"); small.className = "cl-sender"; small.textContent = u.source + " · "; li.appendChild(small);
      var a = document.createElement("a"); a.rel = "noopener noreferrer"; a.target = "_blank";
      if (u.url && okUrl(u.url, ["http:", "https:"])) { a.href = u.url; a.textContent = "Open unsubscribe page"; }
      else if (u.mailto && okUrl(u.mailto, ["mailto:"])) { a.href = u.mailto; a.textContent = "Send unsubscribe email"; }
      else { a.href = u.gmail_search; a.textContent = "No link found — open their latest email and use its footer link"; }
      li.appendChild(a); ul.appendChild(li);
    });
    sec2.hidden = false;
  }
  if (state.submitted) { var p0 = buildPlan(); showUnsub(p0.unsubscribe, true); status("Already submitted as " + state.submitted.plan_id + ". Changes now would need a new submission."); }

  refresh();
})();
