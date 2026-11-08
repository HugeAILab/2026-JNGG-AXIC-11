// 포털 공통 스크립트 (TODO.md 8.1)
// - WS.root: 경로 접두사 (모든 fetch 주소 앞에 붙인다)
// - WS.fetch(): CSRF 헤더를 자동으로 붙이고, 401 → 로그인 화면 / 403 → 안내 문구 처리
(function () {
  function meta(name) { var m = document.querySelector('meta[name="' + name + '"]'); return m ? m.content : ""; }
  var WS = (window.WS = window.WS || {});
  WS.root = meta("root-path");
  WS.csrf = meta("csrf-token");
  WS.url = function (path) { return WS.root + path; };

  WS.fetch = function (path, opts) {
    opts = opts || {};
    var method = (opts.method || "GET").toUpperCase();
    var headers = new Headers(opts.headers || {});
    headers.set("Accept", "application/json");
    if (method !== "GET" && method !== "HEAD") headers.set("X-CSRF-Token", WS.csrf);
    if (opts.json !== undefined) { headers.set("Content-Type", "application/json"); opts.body = JSON.stringify(opts.json); }
    return fetch(WS.url(path), { method: method, headers: headers, body: opts.body, credentials: "same-origin" })
      .then(function (res) {
        if (res.status === 401) {
          location.href = WS.url("/login?next=" + encodeURIComponent(location.pathname + location.search));
          throw new Error("login required");
        }
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok) {
            var msg = (data && data.error) || ("요청 실패 (" + res.status + ")");
            if (!opts.silent) alert(msg);
            var err = new Error(msg); err.status = res.status; err.data = data; throw err;
          }
          return data;
        });
      });
  };

  document.addEventListener("click", function (e) {
    // 좁은 화면에서 사이드바 열고 닫기
    if (e.target.closest("[data-toggle-sidebar]")) { document.body.classList.toggle("side-open"); return; }
    // 로그인 화면: 데모 계정 누르면 입력란 채우기
    var demo = e.target.closest(".demo-fill");
    if (demo) {
      var u = document.getElementById("username"), p = document.getElementById("password");
      if (u && p) { u.value = demo.dataset.user; p.value = demo.dataset.pass; p.focus(); }
      return;
    }
    // 표의 행 전체를 링크처럼 (안쪽 링크·버튼·입력은 그대로)
    var row = e.target.closest("tr[data-href]");
    if (row && !e.target.closest("a, button, input, select, label")) { location.href = row.dataset.href; return; }
    // 사용자 메뉴: 바깥을 누르면 닫기
    document.querySelectorAll("details.usermenu[open]").forEach(function (d) { if (!d.contains(e.target)) d.removeAttribute("open"); });
  });
})();

// 이미지 대체: CSP 때문에 onerror 속성을 쓸 수 없어 data-fallback 으로 처리한다.
// defer 로 늦게 실행되므로, 이미 실패한 이미지(complete && naturalWidth===0)도 함께 본다.
document.querySelectorAll('img[data-fallback]').forEach(function (img) {
  function swap() {
    img.removeEventListener('error', swap);
    var alt = img.getAttribute('data-fallback');
    if (alt && img.src !== alt) { img.src = alt; }
  }
  img.addEventListener('error', swap);
  if (img.complete && img.naturalWidth === 0) { swap(); }
});
