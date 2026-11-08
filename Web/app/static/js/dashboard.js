// 대시보드 (TODO.md 8.2): /portal/api/stats 를 한 번 받아 view(종합 현황·결함 유형·구역·작업자)에 맞는 카드만 그린다.
(function () {
  var root = document.getElementById("dash");
  if (!root) return;
  var view = root.dataset.view, days = root.dataset.days;
  var STATUS = { DEFECT: "조치 대기", REWORK: "수정 중", REINSPECT: "재검사 대기", PASS: "완료(합격)", REJECT: "폐기·재제작" };
  var STATUS_COLOR = { DEFECT: "#DC2626", REWORK: "#EA580C", REINSPECT: "#7C3AED", PASS: "#16A34A", REJECT: "#374151" };
  var DOMAIN_COLOR = { WELD: "#2459C9", SURFACE: "#B45309", INSULATION: "#047857", CABLE: "#6D28D9", BINDING: "#BE185D", CUTTING: "#334155" };
  var BLUE = "#2F6FED", CYAN = "#22C3E6", RED = "#DC2626";

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function pct(v) { return v == null ? "-" : v.toFixed(1) + "%"; }
  function set(id, text) { var e = $(id); if (e) e.textContent = text; }
  function row(tbody, cells, href) {
    var tr = el("tr");
    cells.forEach(function (c) {
      var td = el("td", c.cls || "", c.text);
      if (c.node) { td.textContent = ""; td.appendChild(c.node); }
      tr.appendChild(td);
    });
    if (href) { tr.dataset.href = href; tr.className = "rowlink"; }
    tbody.appendChild(tr);
  }
  function empty(tbody, cols, text) { var tr = el("tr"), td = el("td", "empty", text || "해당 건이 없습니다."); td.colSpan = cols; tr.appendChild(td); tbody.appendChild(tr); }

  function chart(id, cfg) {
    var c = $(id);
    if (!c) return;
    if (!window.Chart) { c.parentNode.replaceChild(el("p", "muted small", "차트 라이브러리를 불러오지 못했습니다 (인터넷 연결 확인)."), c); return; }
    var o = cfg.options || {};
    o.plugins = Object.assign({ legend: { display: false } }, o.plugins || {});
    cfg.options = Object.assign({ responsive: true, maintainAspectRatio: false, animation: false }, o);
    new Chart(c, cfg);
  }

  function overview(d) {
    var k = d.kpi;
    set("k-total", k.total.toLocaleString()); set("k-total-sub", "확정 " + k.confirmed + "건");
    set("k-rate", pct(k.defect_rate)); set("k-rate-sub", "불량 확정 " + k.defects + " / 확정 " + k.confirmed);
    set("k-open", k.open);
    set("k-re", pct(k.reinspect_rate)); set("k-re-sub", "합격 " + k.reinspect_ok + " · 불합격 " + k.reinspect_ng);
    set("k-lead", k.lead_days == null ? "-" : k.lead_days + "일"); set("k-lead-sub", "불량 확정 → 재검사 합격 · " + k.lead_n + "건");
    set("k-agree", pct(k.agree)); set("k-agree-sub", "최초 확정 기준 · " + k.agree_n + "건");
    chart("c-daily", {
      data: { labels: d.daily.map(function (x) { return x.date.slice(5); }), datasets: [
        { type: "line", label: "불량률(%)", data: d.daily.map(function (x) { return x.rate; }), borderColor: RED, backgroundColor: RED,
          yAxisID: "y1", tension: .3, spanGaps: true, pointRadius: 2 },
        { type: "bar", label: "검사 건수", data: d.daily.map(function (x) { return x.n; }), backgroundColor: "rgba(47,111,237,.75)", borderRadius: 3, yAxisID: "y" }] },
      options: { plugins: { legend: { display: true, position: "bottom" } }, interaction: { mode: "index", intersect: false },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: "건" } },
                  y1: { position: "right", beginAtZero: true, suggestedMax: 100, grid: { drawOnChartArea: false }, title: { display: true, text: "%" } } } }
    });
    set("waiting-text", "확정 대기(검사 대기·AI 판정 완료) " + d.waiting_confirm + "건");
    chart("c-stages", {
      type: "bar",
      data: { labels: d.stages.map(function (s) { return STATUS[s.key]; }),
              datasets: [{ data: d.stages.map(function (s) { return s.n; }), backgroundColor: d.stages.map(function (s) { return STATUS_COLOR[s.key]; }), borderRadius: 4 }] },
      options: { indexAxis: "y", scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } }
    });
    var tb = $("t-due"); tb.replaceChildren();
    d.due.forEach(function (x) {
      var st = el("span", "st st-" + x.status, STATUS[x.status]);
      row(tb, [{ text: x.code + " · " + x.block }, { text: x.type_name }, { node: st }, { text: x.assignee || "-" },
               { text: (x.due_date || "미정") + (x.overdue ? " (초과)" : x.soon ? " (임박)" : ""), cls: x.overdue ? "txt-red nowrap" : x.soon ? "txt-warn nowrap" : "nowrap" }],
          WS.url("/portal/inspections/" + x.id));
    });
    if (!d.due.length) empty(tb, 5, "미결 불량이 없습니다.");
  }

  function defects(d) {
    chart("c-domain", {
      type: "bar",
      data: { labels: d.by_domain.map(function (x) { return x.label; }), datasets: [
        { label: "불량 확정", data: d.by_domain.map(function (x) { return x.defects; }), backgroundColor: d.by_domain.map(function (x) { return DOMAIN_COLOR[x.domain] || BLUE; }), borderRadius: 4 }] },
      options: { scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
        plugins: { tooltip: { callbacks: { afterLabel: function (c) { var x = d.by_domain[c.dataIndex]; return "검사 " + x.n + "건 · 불량률 " + pct(x.rate); } } } } }
    });
    var types = d.types.slice(0, 12);
    chart("c-types", {
      type: "bar",
      data: { labels: types.map(function (x) { return x.name; }), datasets: [
        { data: types.map(function (x) { return x.n; }), backgroundColor: types.map(function (x) { return DOMAIN_COLOR[x.domain] || BLUE; }), borderRadius: 4 }] },
      options: { indexAxis: "y", scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } }
    });
    var tb = $("t-types"); tb.replaceChildren();
    d.types.forEach(function (x) {
      row(tb, [{ text: x.name }, { text: x.n, cls: "num" }, { text: x.lead == null ? "-" : x.lead, cls: "num" }, { text: x.rework.toFixed(2), cls: "num" }],
          WS.url("/portal/inspections?dtype=" + encodeURIComponent(x.code)));
    });
    if (!d.types.length) empty(tb, 4);
    tb = $("t-mismatch"); tb.replaceChildren();
    d.mismatch.forEach(function (x) { row(tb, [{ text: x.name }, { text: x.n, cls: "num" }, { text: x.bad, cls: "num txt-red" }, { text: pct(x.rate), cls: "num" }]); });
    if (!d.mismatch.length) empty(tb, 4, "불일치 건이 없습니다.");
    chart("c-review", {
      type: "bar",
      data: { labels: d.review.map(function (x) { return x.label; }), datasets: [{ data: d.review.map(function (x) { return x.n; }), backgroundColor: "#CA8A04", borderRadius: 4 }] },
      options: { indexAxis: "y", scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } }
    });
  }

  function areas(d) {
    var t = $("t-heat"); t.replaceChildren();
    var h = d.heat, max = 0;
    Object.keys(h.cells).forEach(function (k) { if (h.cells[k].rate != null) max = Math.max(max, h.cells[k].rate); });
    set("heat-max", (max || 0).toFixed(0) + "%");
    var head = el("tr"); head.appendChild(el("th", null, "탱크 \\ 블록"));
    h.blocks.forEach(function (b) { head.appendChild(el("th", null, b)); });
    var thead = el("thead"); thead.appendChild(head); t.appendChild(thead);
    var tbody = el("tbody");
    h.tanks.forEach(function (tk) {
      var tr = el("tr"); tr.appendChild(el("th", null, tk));
      h.blocks.forEach(function (b) {
        var c = h.cells[tk + "|" + b], td = el("td");
        if (c && c.c) {
          var a = max ? (c.rate || 0) / max : 0;
          td.style.background = "rgba(220,38,38," + (0.08 + a * 0.72).toFixed(2) + ")";
          if (a > 0.55) td.style.color = "#fff";
          td.appendChild(el("b", null, pct(c.rate))); td.appendChild(el("small", null, c.f + "/" + c.c));
          td.title = tk + " " + b + " · 검사 " + c.n + " · 불량 확정 " + c.f + " / 확정 " + c.c;
          td.className = "hot"; td.dataset.href = WS.url("/portal/inspections?tank=" + encodeURIComponent(tk) + "&block=" + encodeURIComponent(b));
        } else { td.className = "none"; td.textContent = c ? "확정 없음" : ""; }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    t.appendChild(tbody);
    t.addEventListener("click", function (e) { var td = e.target.closest("td.hot"); if (td) location.href = td.dataset.href; });
    chart("c-process", {
      type: "bar",
      data: { labels: d.process.map(function (x) { return x.process; }), datasets: [
        { data: d.process.map(function (x) { return x.rate; }), backgroundColor: "rgba(47,111,237,.8)", borderRadius: 4 }] },
      options: { scales: { y: { beginAtZero: true, suggestedMax: 60, title: { display: true, text: "불량률 %" } } },
        plugins: { tooltip: { callbacks: { label: function (c) { var x = d.process[c.dataIndex]; return "불량률 " + pct(x.rate) + " (" + x.defects + "/" + x.c + ")"; } } } } }
    });
    var tb = $("t-welders"); tb.replaceChildren();
    d.welders.forEach(function (x) {
      row(tb, [{ text: x.welder }, { text: x.defects, cls: "num txt-red" }, { text: x.c, cls: "num" }, { text: pct(x.rate), cls: "num" }, { text: x.top_type }]);
    });
    if (!d.welders.length) empty(tb, 5);
  }

  WS.fetch("/portal/api/stats?days=" + encodeURIComponent(days), { silent: true }).then(function (d) {
    var k = d.kpi;
    set("span-text", (days ? "최근 " + days + "일" : "전체 기간") + (k.from ? " (" + k.from + " ~ " + k.to + ", 검사 " + k.total + "건)" : " — 검사 건이 없습니다"));
    function go() { ({ overview: overview, defects: defects, areas: areas })[view](d); }
    if (window.Chart || document.readyState === "complete") go(); else window.addEventListener("load", go);
  }).catch(function (e) { set("span-text", "집계를 불러오지 못했습니다: " + e.message); });
})();
