"""고정 스냅샷에서 한글 PDF를 생성한다."""
from __future__ import annotations

import json
from pathlib import Path

from fpdf import FPDF
from PIL import Image

from .. import config
from . import storage
from .inspections import EVENT_LABELS


class ReportPDF(FPDF):
    def __init__(self, report_no: str):
        super().__init__(format="A4")
        self.report_no = report_no
        self.set_auto_page_break(True, margin=19)
        self.add_font("Nanum", fname=str(config.APP_DIR / "assets/fonts/NotoSansKR.ttf"))
        self.set_font("Nanum", size=10)

    def footer(self):
        self.set_y(-13)
        self.set_font("Nanum", size=8)
        self.cell(0, 7, f"QCheck · {self.report_no} · {self.page_no()}/{{nb}}", align="C")


def _text(pdf: ReportPDF, text: object, *, size: int = 10, gap: int = 5):
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Nanum", size=size)
    pdf.multi_cell(0, gap, str(text if text is not None else "-"))


def _heading(pdf: ReportPDF, text: str):
    pdf.ln(3)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Nanum", size=12)
    pdf.multi_cell(0, 7, text)


def generate(report: dict, items: list[dict]) -> bytes:
    pdf = ReportPDF(report["report_no"])
    pdf.alias_nb_pages()
    pdf.add_page()
    _text(pdf, report["title"], size=18, gap=10)
    for label, value in (("번호", report["report_no"]), ("종류", "결함" if report["type"] == "DEFECT" else "완료"),
                         ("생성 경로", report["source"]), ("항목 수", report["item_count"]),
                         ("작성자", report["author_name"]), ("작성일", report["created_at"])):
        _text(pdf, f"{label}: {value}")
    _text(pdf, "검토: ________________    승인: ________________")
    _heading(pdf, "재발 · 원인 분석")
    _text(pdf, report["analysis_text"])
    _heading(pdf, "항목 목록")
    for i, item in enumerate(items, 1):
        s = json.loads(item["snapshot_json"])
        _text(pdf, f'{i}. {s["header"]["code"]} · {s["header"]["defect_type"]} · {s["header"]["status"]}')
    for i, item in enumerate(items, 1):
        s = json.loads(item["snapshot_json"])
        h, b, ai, a = s["header"], s["basic"], s["ai_basic"], s["analysis"]
        pdf.add_page()  # 검사 항목은 언제나 새 쪽에서 시작
        _text(pdf, f'{report["report_no"]} · 항목 {i}/{len(items)} · {h["code"]} · {h["defect_type"]} · {h["status"]}', size=13, gap=8)
        _heading(pdf, "① 사진")
        for photo in s["photos"]:
            path = storage.thumb_path(storage.thumb_rel(photo["image_path"])) if photo["image_path"] else None
            if not path or not path.is_file():
                path = storage.photo_path(photo["image_path"]) if photo["image_path"] else None
            if path and path.is_file():
                if pdf.get_y() > 215: pdf.add_page()
                try:
                    with Image.open(path) as im:
                        iw, ih = im.size
                    width, height = (65, 65 * ih / iw) if iw / ih >= 65 / 48 else (48 * iw / ih, 48)
                    x, y = pdf.l_margin, pdf.get_y()
                    pdf.image(str(path), x=x, y=y, w=width, h=height)
                    if photo.get("top_box"):
                        x0, y0, x1, y1 = [max(0, min(1, float(v))) for v in photo["top_box"]]
                        pdf.set_draw_color(220, 45, 45)
                        pdf.rect(x + x0 * width, y + y0 * height, (x1-x0) * width, (y1-y0) * height)
                    pdf.set_y(y + height + 2)
                except Exception:
                    _text(pdf, "사진 파일을 읽을 수 없습니다.")
            _text(pdf, photo["label"])
        if not s["photos"]: _text(pdf, "사진 없음")
        _heading(pdf, "② 기본 정보")
        for key, label in (("project", "프로젝트"), ("tank_no", "탱크"), ("block", "블록"), ("joint_no", "위치"),
                           ("work_type", "작업 종류"), ("weld_process", "용접법"), ("weld_position", "자세"),
                           ("welder", "용접사"), ("inspector_name", "검사자"), ("inspected_at", "검사 일시"),
                           ("upload_log_id", "업로드 번호")):
            _text(pdf, f'{label}: {b.get(key) or "-"}')
        _heading(pdf, "③-1 AI 판정 (기본만)")
        _text(pdf, f'판정: {"불량" if ai["ai_is_defect"] else "정상 / 검출 없음"} · 유형: {h["defect_type"]} · 영역: {ai["ai_domain"] or "-"}')
        _text(pdf, f'모델: {ai["ai_source"] or "-"} · 모델 점수(참고용): {ai["ai_confidence"] or "-"}')
        if ai["flags"]: _text(pdf, "검토 필요: " + ", ".join(ai["flags"]))
        if ai["final_is_defect"] is None: _text(pdf, "AI 추정 · 미확정")
        _heading(pdf, "③-2 불량 설명 · 원인 · 방지")
        if a:
            _text(pdf, "AI 초안 · " + ("근거 검색" if a.get("explain_source") == "LLM" else "템플릿"))
            _text(pdf, a.get("description") or "AI 분석 없음")
            for key, label in (("causes", "원인"), ("prevention", "방지"), ("checks", "추가 확인")):
                for v in a.get(key) or []: _text(pdf, f"{label}: {v}")
            for hit in a.get("rag_hits") or []:
                _text(pdf, f'근거 [{hit.get("no") or "유형 가이드"}] {hit.get("doc") or "-"} · {hit.get("section") or "-"}')
        else: _text(pdf, "AI 분석 없음")
        _heading(pdf, "⑥ 처리 이력")
        for event in s["events"]:
            _text(pdf, f'{event["created_at"]} · {EVENT_LABELS.get(event["action"], event["action"])} · {event["from_status"] or "-"} → {event["to_status"] or "-"} · {event["actor_name"] or "AI"}')
            if event["comment"]: _text(pdf, event["comment"])
    return bytes(pdf.output())
