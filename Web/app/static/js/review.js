// 업로드 결과 검토 (TODO.md 6.4 ⑤⑥): 1초마다 진행률을 조회해 사진 카드를 채우고, 장별 확정·일괄 확정을 한다.
(function () {
  var root = document.getElementById("review-root");
  if (!root) return;
  var logId = root.dataset.log, canConfirm = root.dataset.confirm === "1";
  var grid = document.getElementById("review-grid"), cards = {}, timer = null, last = null;
  var typeOptions = document.getElementById("type-options").innerHTML;
  var DOMAIN = { WELD: "용접", SURFACE: "도장·표면", INSULATION: "보온재", CABLE: "케이블", BINDING: "바인딩", CUTTING: "절단" };
  var STATUS = { PENDING: "검사 대기", AI_DONE: "AI 판정 완료", DEFECT: "불량 · 조치 대기", REWORK: "수정 중",
                 REINSPECT: "재검사 대기", PASS: "합격", REJECT: "폐기·재제작" };
  var STAGE = { A_WAIT: ["wait", "A 대기"], A_RUN: ["run", "A 실행 중"], B_RUN: ["run", "B 실행 중"], ANALYZE: ["run", "설명 작성 중"], DONE: ["done", "판정 완료"], ERROR: ["fail", "판정 실패"] };

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function f2(v) { return v == null ? "-" : Number(v).toFixed(2); }

  function draft(it) {
    var parts = [];
    if (it.ai_is_defect) parts.push("AI 판정 " + (it.type_name || "-") + "(" + f2(it.ai_confidence) + ", 모델 " + it.ai_source + ")");
    var t = (it.findings || []).map(function (f) { return (f.name || f["class"] || f.code) + "(" + f2(f.score) + ")"; });
    if (t.length) parts.push("함께 발견: " + t.join(", "));
    return parts.join(" · ") || "검사자 확인";
  }

  function card(it) {
    var li = el("li", "rv-card");
    li.innerHTML = '<div class="rv-img"><img alt="" loading="lazy"><span class="rv-box" hidden></span><span class="rv-stage"></span></div>' +
      '<div class="rv-body"><div class="rv-top"><a class="rv-code"></a><span class="rv-st"></span></div>' +
      '<div class="rv-verdict"></div><div class="rv-chips"></div><div class="rv-flags"></div><div class="rv-extra small"></div>' +
      '<div class="rv-actions"></div></div>';
    li.querySelector("img").src = it.thumb || it.image;
    li.querySelector("img").alt = it.code + " 사진";
    li.querySelector(".rv-code").href = WS.url("/portal/inspections/" + it.id);
    li.querySelector(".rv-code").textContent = it.code;
    li.querySelector(".rv-img").addEventListener("click", function () { if (cards[it.id].data.has_mask) openViewer(cards[it.id].data); });
    grid.appendChild(li);
    return li;
  }

  function render(it) {
    var c = cards[it.id] || (cards[it.id] = { li: card(it) });
    var key = JSON.stringify(it);
    if (c.key === key) return;
    c.key = key; c.data = it;
    var li = c.li, st = STAGE[it.stage] || STAGE.A_WAIT;
    li.className = "rv-card" + (it.review && it.ai_status === "DONE" ? " review" : "") + (it.ai_is_defect ? " defect" : "");
    var stage = li.querySelector(".rv-stage");
    stage.className = "rv-stage s-" + st[0]; stage.textContent = st[1];
    li.querySelector(".rv-img").classList.toggle("clickable", !!it.has_mask);
    li.querySelector(".rv-img").title = it.has_mask ? "눌러서 분할 결과(마스크) 보기" : "";
    var box = li.querySelector(".rv-box");
    if (it.top_box && it.ai_status === "DONE") {
      box.hidden = false;
      box.style.left = it.top_box[0] * 100 + "%"; box.style.top = it.top_box[1] * 100 + "%";
      box.style.width = (it.top_box[2] - it.top_box[0]) * 100 + "%"; box.style.height = (it.top_box[3] - it.top_box[1]) * 100 + "%";
      box.classList.toggle("ok", !it.ai_is_defect);
    } else box.hidden = true;
    var s = li.querySelector(".rv-st"); s.className = "st st-" + it.status; s.textContent = STATUS[it.status] || it.status;

    var v = li.querySelector(".rv-verdict"); v.replaceChildren();
    if (it.ai_status === "DONE") {
      v.appendChild(it.ai_is_defect ? el("b", "txt-red", it.type_name) : (it.ai_defect_type ? el("b", null, "정상") : el("b", "muted", "검출 없음")));
      if (it.domain) v.appendChild(el("span", "dm dm-" + it.domain, DOMAIN[it.domain] || it.domain));
      if (it.ai_confidence != null) {
        var conf = el("span", "conf"), bar = el("i"); bar.style.width = Math.round(it.ai_confidence * 100) + "%"; conf.appendChild(bar);
        v.appendChild(conf); v.appendChild(el("span", "conf-n", f2(it.ai_confidence)));
      }
      if (it.ai_source) v.appendChild(el("span", "src src-" + it.ai_source, it.ai_source));
      if (it.is_mock) v.appendChild(el("span", "badge sample sm", "모의"));
    } else if (it.ai_status === "ERROR") v.appendChild(el("span", "txt-red", "AI 판정 실패 — " + (it.ai_error || "수동으로 판정하세요")));
    else v.appendChild(el("span", "muted", it.stage === "B_RUN" ? "용접 상세 분석 중…" : "AI 판정 중…"));

    var chips = li.querySelector(".rv-chips"); chips.replaceChildren();
    if (it.a) chips.appendChild(el("span", "chip step", "A " + (it.a.status === "OK" ? (it.a["class"] || "후보 없음") + (it.a.score != null ? " " + f2(it.a.score) : "") : "실패")));
    if (it.b) chips.appendChild(el("span", "chip step b", "→ B " + (it.b.status === "OK" ? (it.b.name || "-") + " " + f2(it.b.score) : "실패")));
    else if (it.stage === "B_RUN") chips.appendChild(el("span", "chip step b run", "→ B 실행 중"));

    var flags = li.querySelector(".rv-flags"); flags.replaceChildren();
    (it.flag_labels || []).forEach(function (t) { flags.appendChild(el("span", "flag", t)); });

    var extra = li.querySelector(".rv-extra"); extra.replaceChildren();
    if (it.findings && it.findings.length) extra.appendChild(el("div", null, "함께 발견: " + it.findings.map(function (f) { return f.name + " " + f2(f.score); }).join(", ")));
    if (it.b && it.b.keywords && it.b.keywords.length) {
      var kw = el("div", "kw"); it.b.keywords.forEach(function (k) { kw.appendChild(el("span", "chip", k)); }); extra.appendChild(kw);
    }
    if (it.analysis && it.analysis.description) extra.appendChild(el("div", "rv-analysis", "AI 초안: " + it.analysis.description));
    extra.appendChild(el("div", "muted", "위치 " + it.joint_no));

    var act = li.querySelector(".rv-actions"); act.replaceChildren();
    if (it.status === "PENDING" || it.status === "AI_DONE") {
      if (canConfirm && it.ai_status !== "QUEUED" && it.ai_status !== "RUNNING") {
        var sel = el("select", "rv-type"); sel.innerHTML = typeOptions; sel.setAttribute("aria-label", "결함 유형");
        if (it.ai_defect_type && it.ai_defect_type !== "NORMAL") sel.value = it.ai_defect_type;
        var okBtn = el("button", "btn btn-secondary sm", "정상 확정"); okBtn.type = "button";
        var ngBtn = el("button", "btn btn-danger sm", "불량 확정"); ngBtn.type = "button";
        okBtn.addEventListener("click", function () { confirmOne(it, { decision: "ok", finding: "검토 화면에서 정상 확정" }, okBtn); });
        ngBtn.addEventListener("click", function () { confirmOne(it, { decision: "defect", defect_type: sel.value, finding: draft(it) }, ngBtn); });
        act.appendChild(sel); act.appendChild(okBtn); act.appendChild(ngBtn);
      } else if (!canConfirm) act.appendChild(el("span", "muted small", "확정은 검사자 권한"));
    } else {
      act.appendChild(el("span", "small " + (it.final_is_defect ? "txt-red" : ""), "확정: " + (it.final_is_defect ? (it.final_type_name || "불량") : "정상")));
    }
    if (it.has_mask) {
      var mb = el("button", "link-btn", "분할 결과 보기"); mb.type = "button";
      mb.addEventListener("click", function () { openViewer(it); }); act.appendChild(mb);
    }
  }

  function confirmOne(it, body, btn) {
    btn.disabled = true;
    WS.fetch("/portal/inspections/" + it.id + "/confirm", { method: "POST", json: body })
      .then(poll).catch(function () { btn.disabled = false; });
  }

  function summary(d) {
    var pct = d.planned ? Math.round((d.done / Math.max(d.planned, d.total)) * 100) : 0;
    document.getElementById("rp-bar").style.width = pct + "%";
    document.getElementById("rp-text").textContent = d.finished
      ? "AI 판정 완료 · " + d.total + "장 중 불량 추정 " + d.defects + "건"
      : "AI 판정 중 " + d.done + " / " + Math.max(d.planned, d.total) + "장" + (d.total < d.planned ? " (전송 " + d.total + "장)" : "");
    var review = d.items.filter(function (x) { return x.review && x.ai_status === "DONE"; }).length;
    var errors = d.items.filter(function (x) { return x.ai_status === "ERROR"; }).length;
    document.getElementById("rp-sub").textContent = "검토 필요 " + review + "건" + (errors ? " · 실패 " + errors + "건" : "") +
      " · 확정 대기 " + d.confirmable + "건";
    var all = document.getElementById("confirm-all");
    if (all) all.disabled = !d.confirmable;
  }

  function poll() {
    return WS.fetch("/portal/inspections/uploads/" + logId + "/status", { silent: true }).then(function (d) {
      last = d;
      d.items.forEach(render);
      summary(d);
      clearTimeout(timer);
      var running = d.items.some(function (x) { return x.ai_status === "QUEUED" || x.ai_status === "RUNNING"; });
      timer = setTimeout(poll, running || !d.finished ? 1000 : 10000);
    }).catch(function () { clearTimeout(timer); timer = setTimeout(poll, 3000); });
  }

  var all = document.getElementById("confirm-all");
  if (all) all.addEventListener("click", function () {
    if (last && last.confirmable && window.ReportModal) window.ReportModal.openConfirm(logId, last.confirmable, poll);
  });

  // 분할 결과 뷰어 (대화상자)
  var dlg = document.getElementById("seg-dialog");
  function openViewer(it) {
    document.getElementById("sd-title").textContent = it.code + " · 모델 A 분할 결과";
    document.getElementById("sd-status").textContent = "불러오는 중…";
    window.SegViewer.hide();
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", "");
    window.SegViewer.load(WS.root, it.id).then(function () {
      document.getElementById("sd-status").textContent = "후보를 선택해 원본 사진과 마스크를 대조하세요.";
    }).catch(function (e) { document.getElementById("sd-status").textContent = e.message; });
  }
  dlg.addEventListener("click", function (e) { if (e.target === dlg || e.target.closest("[data-close]")) dlg.close(); });

  poll();
})();
