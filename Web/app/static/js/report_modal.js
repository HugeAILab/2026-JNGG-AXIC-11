/* 검사 목록과 업로드 결과가 함께 쓰는 보고서 생성 대화상자. */
(function () {
  "use strict";
  var dlg = document.getElementById("report-dialog");
  if (!dlg) return;
  var KEY = "qcheck-report-selected";
  var selected = [];
  try { selected = JSON.parse(sessionStorage.getItem(KEY) || "[]").filter(Number.isInteger).slice(0, 200); } catch (_) {}
  var source = "SELECTION", logId = null, edited = false;
  var title = document.getElementById("report-title"), error = document.getElementById("report-error");
  var previewText = document.getElementById("report-preview"), submit = document.getElementById("report-submit");
  var confirmStep = document.getElementById("report-confirm-step"), confirmSubmit = document.getElementById("report-confirm-submit");
  var onConfirmed = null, confirmComplete = false;
  var kind = function () { return dlg.querySelector('input[name="report-type"]:checked').value; };
  var scope = function () { return dlg.querySelector('input[name="report-scope"]:checked').value; };
  function payload() { return {type:kind(), source:source, scope:scope(), inspection_ids:source === "SELECTION" ? selected : undefined, upload_log_id:logId}; }
  function refresh() {
    var rows = document.querySelectorAll(".report-row");
    rows.forEach(function (box) { box.checked = selected.includes(Number(box.value)); });
    var counter = document.getElementById("report-selected-count");
    if (counter) counter.textContent = selected.length + "건 선택됨";
    var opener = document.getElementById("report-open");
    if (opener) opener.disabled = selected.length === 0;
    sessionStorage.setItem(KEY, JSON.stringify(selected));
  }
  function show() { if (dlg.open) return; if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", ""); }
  function close() { if (dlg.close) dlg.close(); else dlg.removeAttribute("open"); }
  function setConfirmStep(active) {
    document.getElementById("report-dialog-eyebrow").textContent = active ? "품질검사 확정" : "품질검사 보고서";
    confirmStep.hidden = !active;
    document.getElementById("report-title-field").hidden = active;
    document.getElementById("report-type-field").hidden = active || source === "UPLOAD";
    document.getElementById("report-scope-field").hidden = active || source !== "UPLOAD";
    previewText.hidden = active; confirmSubmit.hidden = !active; submit.hidden = active;
    document.getElementById("report-cancel").textContent = active ? "취소" : "나중에";
    error.textContent = "";
  }
  function preview() {
    error.textContent = ""; previewText.textContent = "포함 조건 확인 중…";
    submit.disabled = true;
    WS.fetch("/portal/api/reports/preview", {method:"POST", json:payload()}).then(function (r) {
      previewText.textContent = "포함 " + r.eligible + "건 · 제외 " + r.excluded.length + "건" +
        (r.excluded.length ? " (" + r.excluded.map(function (x) { return x.code + ": " + x.reason; }).slice(0, 5).join(", ") + ")" : "");
      if (!edited) title.value = r.default_title;
      submit.disabled = r.eligible === 0;
    }).catch(function (e) { previewText.textContent = ""; error.textContent = e.message || "포함 조건을 확인하지 못했습니다."; });
  }
  title.addEventListener("input", function () { edited = true; });
  dlg.querySelectorAll('input[name="report-type"],input[name="report-scope"]').forEach(function (input) { input.addEventListener("change", preview); });
  document.getElementById("report-cancel").addEventListener("click", close);
  document.getElementById("report-close").addEventListener("click", close);
  confirmSubmit.addEventListener("click", function () {
    if (confirmComplete) { close(); return; }
    confirmSubmit.disabled = true; confirmSubmit.textContent = "확정 중…"; error.textContent = "";
    WS.fetch("/portal/inspections/uploads/" + logId + "/confirm-all", {method:"POST", json:{}}).then(function (result) {
      if (onConfirmed) onConfirmed();
      if (result.defect > 0) { window.ReportModal.openUpload(logId, result); return; }
      document.getElementById("report-dialog-title").textContent = "확정 완료";
      document.getElementById("report-dialog-count").textContent = "정상 확정 " + result.ok + "건 · 건너뜀 " + result.skipped + "건";
      document.getElementById("report-confirm-description").textContent = "결함 보고서에 포함할 불량 항목이 없습니다.";
      confirmStep.querySelector(".confirm-dialog-rule").hidden = true;
      document.getElementById("report-cancel").hidden = true;
      confirmComplete = true;
      confirmSubmit.textContent = "닫기";
    }).catch(function (e) { error.textContent = e.message || "확정하지 못했습니다. 다시 시도하세요."; })
      .finally(function () { confirmSubmit.disabled = false; if (!confirmComplete) confirmSubmit.textContent = "AI 결과대로 확정"; });
  });
  submit.addEventListener("click", function () {
    if (!title.value.trim()) { error.textContent = "제목을 입력하세요."; return; }
    submit.disabled = true; submit.textContent = "생성 중…"; error.textContent = "";
    WS.fetch("/portal/reports", {method:"POST", json:Object.assign(payload(), {title:title.value.trim()})}).then(function (r) {
      selected = []; refresh(); window.location.href = r.url;
    }).catch(function (e) { error.textContent = e.message || "보고서를 생성하지 못했습니다."; submit.disabled = false; submit.textContent = "보고서 생성"; });
  });
  document.querySelectorAll(".report-row").forEach(function (box) { box.addEventListener("click", function (e) { e.stopPropagation(); }); box.addEventListener("change", function () {
    var id = Number(box.value); selected = selected.filter(function (x) { return x !== id; });
    if (box.checked) { if (selected.length >= 200) { box.checked = false; alert("최대 200건까지 선택할 수 있습니다."); return; } selected.push(id); }
    refresh();
  }); });
  var all = document.getElementById("report-page-all");
  if (all) all.addEventListener("change", function () { document.querySelectorAll(".report-row").forEach(function (box) { if (box.checked !== all.checked) { box.checked = all.checked; box.dispatchEvent(new Event("change")); } }); });
  var clear = document.getElementById("report-clear");
  if (clear) clear.addEventListener("click", function () { selected = []; refresh(); });
  var open = document.getElementById("report-open");
  if (open) open.addEventListener("click", function () { source = "SELECTION"; logId = null; edited = false;
    setConfirmStep(false);
    document.getElementById("report-dialog-title").textContent = "보고서 생성";
    document.getElementById("report-dialog-count").textContent = "선택 " + selected.length + "건";
    document.getElementById("report-type-field").hidden = false;
    document.getElementById("report-scope-field").hidden = true; show(); preview(); });
  window.ReportModal = {openConfirm:function (id, count, done) { source = "UPLOAD"; logId = Number(id); onConfirmed = done;
    confirmComplete = false; confirmSubmit.textContent = "AI 결과대로 확정";
    confirmStep.querySelector(".confirm-dialog-rule").hidden = false;
    document.getElementById("report-cancel").hidden = false;
    document.getElementById("report-dialog-title").textContent = "AI 결과대로 확정";
    document.getElementById("report-dialog-count").textContent = "판정 완료 " + count + "건";
    document.getElementById("report-confirm-description").textContent = "AI 판정이 완료된 " + count + "건을 확정합니다.";
    setConfirmStep(true); show();
  }, openUpload:function (id, result) { source = "UPLOAD"; logId = Number(id); edited = false;
    setConfirmStep(false);
    dlg.querySelector('input[name="report-type"][value="DEFECT"]').checked = true;
    document.getElementById("report-dialog-title").textContent = "결함 보고서로 생성할까요?";
    document.getElementById("report-dialog-count").textContent = "불량 확정 " + result.defect + "건 · 정상 확정 " + result.ok + "건 · 건너뜀 " + result.skipped + "건";
    document.getElementById("report-type-field").hidden = true;
    document.getElementById("report-scope-field").hidden = false; show(); preview(); }};
  refresh();
})();
