// 사진 여러 장 업로드 (TODO.md 6.4 ①): 고른 사진을 썸네일 카드로 보여 주고, 몇 장씩 나눠 서버로 보낸다.
// - 원본 바이트를 그대로 보낸다 (서버가 저장한 사진과 모델 A 가 본 사진이 같아야 마스크 좌표가 맞는다)
// - 프록시 본문 한도에 걸리지 않게 요청 1회 ≤ CHUNK_BYTES (사진 1장이 더 크면 그 1장만 따로)
// - 첫 요청이 UPLOAD 로그를 만들고(manifest = 전체 파일명), 이어지는 요청은 같은 log_id 에 덧붙인다
(function () {
  var form = document.getElementById("upload-form");
  if (!form) return;
  var MAX = Number(form.dataset.max) || 20, MAX_BYTES = 10 * 1024 * 1024, MAX_PIXELS = 20000000;
  var CHUNK_BYTES = 8 * 1024 * 1024, CHUNK_FILES = 5;
  var input = document.getElementById("file-input"), zone = document.getElementById("dropzone");
  var grid = document.getElementById("photo-grid"), tpl = document.getElementById("photo-card");
  var btn = document.getElementById("submit-btn"), errBox = document.getElementById("upload-error");
  var items = [], busy = false, seq = 0, logId = null, review = null;  // 실패 뒤 다시 보내면 같은 로그에 이어 붙인다

  function fmtSize(n) { return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }
  function showError(msg) { errBox.textContent = msg || ""; errBox.hidden = !msg; }
  function ok(it) { return !it.error && !it.sent; }

  function refresh() {
    var good = items.filter(ok), bytes = good.reduce(function (s, it) { return s + it.file.size; }, 0);
    document.getElementById("pick-summary").textContent = items.length
      ? good.length + "장 선택 · " + fmtSize(bytes) + (items.length > good.length ? " · 올릴 수 없는 사진 " + (items.length - good.length) + "장" : "")
      : "선택한 사진 없음";
    document.getElementById("submit-count").textContent = good.length + "장";
    document.getElementById("pick-toolbar").hidden = !items.length;
    if (!busy) document.getElementById("submit-status").textContent = good.length
      ? "업로드하면 사진이 저장되고 AI 판정이 시작됩니다." : "사진을 고르면 검사를 시작할 수 있습니다.";
    btn.disabled = busy || !good.length || input.disabled;
    zone.classList.toggle("has-files", items.length > 0);
  }

  function setState(it, cls, text) {
    it.el.className = "photo-card " + cls;
    it.el.querySelector(".pc-state").textContent = text || "";
  }

  function add(files) {
    showError("");
    Array.prototype.forEach.call(files, function (file) {
      if (items.some(function (x) { return x.file.name === file.name && x.file.size === file.size && x.file.lastModified === file.lastModified; })) return;
      if (items.filter(ok).length >= MAX) { showError("한 번에 최대 " + MAX + "장까지 올릴 수 있습니다. 나머지는 다음 업로드로 올려 주세요."); return; }
      var it = { id: ++seq, file: file, url: URL.createObjectURL(file), error: "" };
      // <template> 안의 노드는 별도 문서 소속이므로 importNode 로 이 문서에 만든다
      var el = document.importNode(tpl.content.firstElementChild, true);
      it.el = el;
      var img = el.querySelector("img");
      img.src = it.url; img.alt = file.name;
      el.querySelector(".pc-name").textContent = file.name;
      el.querySelector(".pc-name").title = file.name;
      el.querySelector(".pc-meta").textContent = fmtSize(file.size);
      el.querySelector(".pc-remove").addEventListener("click", function () { remove(it); });
      items.push(it);
      grid.appendChild(el);
      if (["image/jpeg", "image/png"].indexOf(file.type) < 0) it.error = "JPEG·PNG만 올릴 수 있습니다";
      else if (file.size > MAX_BYTES) it.error = "10 MiB를 넘습니다";
      if (it.error) { setState(it, "bad", it.error); return; }
      var probe = new Image();  // 크기 확인용 (화면의 썸네일과 별개)
      probe.src = it.url;
      probe.decode().then(function () {
        el.querySelector(".pc-meta").textContent = fmtSize(file.size) + " · " + probe.naturalWidth + "×" + probe.naturalHeight;
        if (probe.naturalWidth * probe.naturalHeight > MAX_PIXELS) { it.error = "2천만 픽셀을 넘습니다"; setState(it, "bad", it.error); refresh(); }
      }).catch(function () { it.error = "사진을 읽을 수 없습니다"; setState(it, "bad", it.error); refresh(); });
    });
    refresh();
  }

  function remove(it) {
    if (busy || it.sent) return;
    URL.revokeObjectURL(it.url);
    it.el.remove();
    items = items.filter(function (x) { return x !== it; });
    refresh();
  }

  input.addEventListener("change", function () { add(input.files); input.value = ""; });
  ["dragenter", "dragover"].forEach(function (t) {
    zone.addEventListener(t, function (e) { e.preventDefault(); if (!input.disabled && !busy) zone.classList.add("over"); });
  });
  ["dragleave", "drop"].forEach(function (t) { zone.addEventListener(t, function (e) { e.preventDefault(); zone.classList.remove("over"); }); });
  zone.addEventListener("drop", function (e) { if (!input.disabled && !busy && e.dataTransfer) add(e.dataTransfer.files); });
  document.getElementById("clear-all").addEventListener("click", function () { items.slice().forEach(remove); showError(""); });

  // 작업 종류가 용접·혼합일 때만 용접법·자세·용접사
  function syncWork() {
    var w = (form.querySelector("[name=work_type]:checked") || {}).value;
    var weld = w === "WELD" || w === "MIXED";
    form.querySelectorAll(".weld-only").forEach(function (el) { el.hidden = !weld; });
  }
  form.querySelectorAll("[name=work_type]").forEach(function (r) { r.addEventListener("change", syncWork); });
  syncWork();

  function chunks(list) {
    var out = [], cur = [], size = 0;
    list.forEach(function (it) {
      if (cur.length && (size + it.file.size > CHUNK_BYTES || cur.length >= CHUNK_FILES)) { out.push(cur); cur = []; size = 0; }
      cur.push(it); size += it.file.size;
    });
    if (cur.length) out.push(cur);
    return out;
  }

  function send(group, logId, manifest, onProgress) {
    return new Promise(function (resolve, reject) {
      var fd = new FormData();
      ["work_type", "tank_no", "block", "project", "weld_process", "weld_position", "welder"].forEach(function (k) {
        var el = k === "work_type" ? form.querySelector("[name=work_type]:checked") : form.elements[k];
        if (el) fd.append(k, el.value);
      });
      if (logId) fd.append("log_id", logId); else fd.append("manifest", JSON.stringify(manifest));
      group.forEach(function (it) { fd.append("files", it.file, it.file.name); fd.append("joints", it.el.querySelector(".pc-joint").value.trim()); });
      var xhr = new XMLHttpRequest();
      xhr.open("POST", WS.url("/portal/inspections/upload"));
      xhr.setRequestHeader("X-CSRF-Token", WS.csrf);
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.onprogress = function (e) { if (e.lengthComputable) onProgress(e.loaded / e.total); };
      xhr.onload = function () {
        var body = {};
        try { body = JSON.parse(xhr.responseText); } catch (e) { /* 프록시 오류 화면 등 */ }
        if (xhr.status === 401) { location.href = WS.url("/login?next=" + encodeURIComponent(location.pathname)); return; }
        if (xhr.status === 413 && !body.error) body.error = "요청이 너무 큽니다 (서버 앞단 크기 제한). 사진을 줄여 다시 올려 주세요.";
        if (xhr.status >= 200 && xhr.status < 300 && body.ok) resolve(body);
        else reject(new Error(body.error || ("업로드 실패 (HTTP " + xhr.status + ")")));
      };
      xhr.onerror = function () { reject(new Error("네트워크 오류로 업로드하지 못했습니다.")); };
      xhr.send(fd);
    });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (busy) return;
    showError("");
    if (!form.elements.block.value.trim()) { showError("블록을 입력하세요 (예: B-07)."); form.elements.block.focus(); return; }
    var list = items.filter(ok);
    if (!list.length) return;
    busy = true; input.disabled = true; refresh();
    btn.textContent = "업로드 중…";
    var groups = chunks(list), total = list.reduce(function (s, it) { return s + it.file.size; }, 0), sent = 0;
    var manifest = list.map(function (it) { return it.file.name; });
    var bar = document.getElementById("submit-progress"), status = document.getElementById("submit-status");
    bar.hidden = false;
    list.forEach(function (it) { setState(it, "wait", "대기"); it.el.querySelector(".pc-joint").readOnly = true; });
    var chain = Promise.resolve();
    groups.forEach(function (group, gi) {
      chain = chain.then(function () {
        group.forEach(function (it) { setState(it, "sending", "전송 중"); });
        status.textContent = "전송 중 " + (gi + 1) + "/" + groups.length + " 묶음";
        var groupBytes = group.reduce(function (s, it) { return s + it.file.size; }, 0);
        return send(group, logId, manifest, function (r) {
          bar.firstElementChild.style.width = Math.round(((sent + groupBytes * r) / total) * 100) + "%";
        }).then(function (body) {
          logId = body.log_id; review = body.review_url; sent += groupBytes;
          group.forEach(function (it) { it.sent = true; setState(it, "sent", "저장됨"); });
        }, function (err) {
          group.forEach(function (it) { setState(it, "bad", "실패"); });
          throw err;
        });
      });
    });
    chain.then(function () {
      status.textContent = "업로드 완료. 결과 검토 화면으로 이동합니다…";
      bar.firstElementChild.style.width = "100%";
      location.href = review;
    }).catch(function (err) {
      busy = false; input.disabled = false; btn.textContent = "업로드하고 AI 판정 시작";
      showError(err.message + (logId ? " 앞서 보낸 사진은 저장되었습니다. 다시 누르면 나머지만 같은 업로드에 이어서 보냅니다." : ""));
      list.forEach(function (it) { if (!it.sent) it.el.querySelector(".pc-joint").readOnly = false; });
      refresh();
      if (logId) { status.innerHTML = ""; var a = document.createElement("a"); a.href = review; a.textContent = "저장된 사진의 결과 보기 →"; status.appendChild(a); }
    });
  });
  refresh();
})();
