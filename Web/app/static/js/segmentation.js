/* 모델 A 분할 결과 뷰어 (32-Web 에서 옮김, 3단계에서 재사용 가능한 형태로 정리).
   templates/portal/inspections/_seg_viewer.html 의 id 들을 채운다.
   사용: SegViewer.show({image: HTMLImageElement, result: 모델 A 원본 응답, elapsed: 초, name: 파일명})
   COCO compressed RLE uses column-major pixels and signed delta-coded runs. */
function decodeCocoRle(counts, total) {
  const runs = [];
  let p = 0, sum = 0;
  while (p < counts.length) {
    let value = 0, shift = 0, c;
    do {
      if (p >= counts.length || shift > 50) throw new Error('잘못된 RLE 문자열');
      c = counts.charCodeAt(p++) - 48;
      if (c < 0 || c > 63) throw new Error('잘못된 RLE 문자');
      value += (c & 31) * 2 ** shift;
      shift += 5;
    } while (c & 32);
    if (c & 16) value -= 2 ** shift;
    if (runs.length > 2) value += runs[runs.length - 2];
    if (!Number.isSafeInteger(value) || value < 0 || sum + value > total) throw new Error('잘못된 RLE 길이');
    runs.push(value); sum += value;
  }
  if (sum !== total) throw new Error('RLE 픽셀 수 불일치');
  return runs;
}

if (typeof document !== 'undefined') window.SegViewer = (function () {
  const $ = id => document.getElementById(id);
  const state = {result: null, entries: [], selected: null, image: null, elapsed: 0, name: 'segmentation-result', bound: false};
  const percent = score => (score * 100).toFixed(2) + '%';
  const visible = () => state.entries.filter(item => item.score >= Number($('score-threshold').value) &&
    (!$('class-filter').value || item.class.name === $('class-filter').value));
  const cell = (row, value) => { const td = document.createElement('td'); td.textContent = value; row.append(td); return td; };
  const updateSummary = items => {
    const groups = new Map();
    for (const item of state.entries) {
      const name = item.class.name;
      if (!groups.has(name)) groups.set(name, {count: 0, max: 0, shown: 0});
      const group = groups.get(name); group.count++; group.max = Math.max(group.max, item.score);
    }
    for (const item of items) groups.get(item.class.name).shown++;
    $('class-summary').replaceChildren();
    for (const [name, group] of [...groups].sort((a, b) => b[1].max - a[1].max)) {
      const tr = document.createElement('tr');
      [name, group.count, percent(group.max), group.shown].forEach(value => cell(tr, value));
      $('class-summary').append(tr);
    }
  };
  function draw(items) {
    const canvas = $('inspection-canvas'), ctx = canvas.getContext('2d');
    canvas.width = state.image.naturalWidth; canvas.height = state.image.naturalHeight;
    ctx.drawImage(state.image, 0, 0);
    const chosen = items.find(item => item.id === state.selected);
    $('selected-info').textContent = chosen ? `선택 #${chosen.id + 1} · ${chosen.class.name} · ${percent(chosen.score)}` : '표시 기준을 만족하는 후보가 없습니다. 양품 확정을 의미하지 않습니다.';
    if (chosen && $('show-mask').checked) {
      try {
        const [height, width] = chosen.mask_rle.size;
        if (width !== canvas.width || height !== canvas.height) throw new Error('이미지와 마스크 크기가 다릅니다.');
        const runs = decodeCocoRle(chosen.mask_rle.counts, width * height);
        const mask = ctx.createImageData(width, height);
        let position = 0;
        runs.forEach((length, index) => {
          if (index % 2) for (let end = position + length; position < end; position++) {
            const x = Math.floor(position / height), y = position % height;
            const offset = (y * width + x) * 4;
            mask.data[offset] = 255; mask.data[offset + 1] = 120; mask.data[offset + 2] = 30; mask.data[offset + 3] = 110;
          } else position += length;
        });
        const layer = document.createElement('canvas'); layer.width = width; layer.height = height;
        layer.getContext('2d').putImageData(mask, 0, 0); ctx.drawImage(layer, 0, 0);
      } catch (e) { $('selected-info').textContent += ` · 마스크 표시 오류: ${e.message}`; }
    }
    if ($('show-boxes').checked) {
      const scale = Math.max(canvas.width / 800, 1);
      ctx.lineWidth = 2 * scale; ctx.font = `${14 * scale}px sans-serif`;
      for (const item of items) {
        const [a, b, c, d] = item.bbox_xyxy;
        const x = Math.max(0, Math.min(a, canvas.width)), y = Math.max(0, Math.min(b, canvas.height));
        const right = Math.max(x, Math.min(c, canvas.width)), bottom = Math.max(y, Math.min(d, canvas.height));
        ctx.strokeStyle = item.id === state.selected ? '#ff781e' : '#37dce7';
        ctx.strokeRect(x, y, right - x, bottom - y);
        ctx.fillStyle = '#112235'; ctx.fillRect(x, y, 130 * scale, 20 * scale);
        ctx.fillStyle = 'white'; ctx.fillText(`#${item.id + 1} ${percent(item.score)}`, x + 4 * scale, y + 15 * scale);
      }
    }
  }
  function refresh() {
    if (!state.result) return;
    const items = visible();
    if (!items.some(item => item.id === state.selected)) state.selected = items[0]?.id ?? null;
    $('threshold-value').textContent = Number($('score-threshold').value).toFixed(2);
    $('result-meta').textContent = `전체 후보 ${state.entries.length}개 · 표시 후보 ${items.length}개 · ${state.image.naturalWidth} × ${state.image.naturalHeight}px · API 응답 ${state.elapsed}초`;
    updateSummary(items);
    $('candidate-list').replaceChildren();
    for (const item of items) {
      const tr = document.createElement('tr'); tr.classList.toggle('selected', item.id === state.selected);
      const button = document.createElement('button'); button.type = 'button'; button.className = 'candidate-pick';
      button.textContent = item.id === state.selected ? '선택됨' : '선택';
      button.setAttribute('aria-pressed', String(item.id === state.selected));
      button.addEventListener('click', () => { state.selected = item.id; refresh(); });
      const td = document.createElement('td'); td.append(button); tr.append(td);
      [item.id + 1, item.class.name, percent(item.score), item.bbox_xyxy.map(v => v.toFixed(1)).join(', ')].forEach(v => cell(tr, v));
      $('candidate-list').append(tr);
    }
    if (!items.length) { const tr = document.createElement('tr'); const td = cell(tr, '표시 기준에 맞는 후보가 없습니다.'); td.colSpan = 5; $('candidate-list').append(tr); }
    draw(items);
  }
  function bind() {
    if (state.bound || !$('inspection-results')) return;
    state.bound = true;
    ['score-threshold', 'class-filter', 'show-boxes', 'show-mask'].forEach(id => $(id).addEventListener('input', refresh));
    $('download-json').addEventListener('click', () => {
      if (!state.result) return;
      const url = URL.createObjectURL(new Blob([JSON.stringify(state.result, null, 2)], {type: 'application/json'}));
      const link = document.createElement('a'); link.href = url; link.download = state.name + '.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  return {
    /* image 크기와 API 좌표(EXIF 방향 반영)가 다르면 오류 */
    show({image, result, elapsed, name, width, height}) {
      bind();
      if (width && height && (width !== image.naturalWidth || height !== image.naturalHeight))
        throw new Error('이미지 방향과 API 좌표가 일치하지 않습니다.');
      state.image = image; state.result = result; state.elapsed = elapsed ?? 0; state.name = name || 'segmentation-result';
      state.entries = result.segmentation.instances.map((item, id) => ({...item, id})).sort((a, b) => b.score - a.score);
      state.selected = null;
      $('class-filter').replaceChildren(new Option('전체 분류', ''));
      [...new Set(state.entries.map(item => item.class.name))].forEach(n => $('class-filter').add(new Option(n, n)));
      $('inspection-results').hidden = false; refresh();
    },
    /* 저장된 결과 불러오기: /portal/api/inspections/{id}/a-result */
    async load(root, inspectionId) {
      const res = await fetch(`${root}/portal/api/inspections/${inspectionId}/a-result`, {headers: {Accept: 'application/json'}});
      const body = await res.json().catch(() => ({}));
      if (!res.ok || !body.ok) throw new Error(body.error || `결과를 불러오지 못했습니다 (HTTP ${res.status}).`);
      const image = new Image(); image.src = body.image_url; await image.decode();
      this.show({image, result: body.result, elapsed: body.elapsed_seconds, name: `inspection-${inspectionId}-A-${body.result_id}`,
                 width: body.image.width, height: body.image.height});
      return body;
    },
    hide() { if ($('inspection-results')) $('inspection-results').hidden = true; state.result = null; },
  };
})();
if (typeof module !== 'undefined') module.exports = {decodeCocoRle};
