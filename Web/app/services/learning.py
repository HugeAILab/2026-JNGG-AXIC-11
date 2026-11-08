"""팀 교육자료와 퀴즈를 포털 DB에 적재하고 조회한다."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import markdown

from .. import config, db

CONTENT = config.APP_DIR / "seed" / "learning"
LESSON_CODES = ("CRACK", "POROSITY", "UNDERCUT", "LACK_OF_FUSION", "INCOMPLETE_PENETRATION",
                "SLAG_INCLUSION", "OVERLAP", "SPATTER", "BEAD_IRREGULAR")
MATERIALS = (
    ("basics", "용접부 검수 기초교육", "검수 흐름과 기본 기록 방법"),
    ("glossary", "검수자 결함사전", "결함 이름과 관찰 용어"),
    ("cases", "결함 발생·보수 사례", "상황별 검토와 후속 확인"),
    ("checklist", "작업 전 품질점검표", "작업 전 확인 항목"),
)


def _load_json(name: str) -> list[dict]:
    return json.loads((CONTENT / name).read_text(encoding="utf-8"))["items"]


def lessons() -> list[dict]:
    source = (CONTENT / "lessons.md").read_text(encoding="utf-8")
    blocks = re.split(r"(?=^## L\d{2} )", source, flags=re.M)[1:]
    result = []
    for index, block in enumerate(blocks):
        match = re.match(r"## (L\d{2}) (.+?) \((.+?)\)\n", block)
        if not match or index >= len(LESSON_CODES):
            continue
        code, title, english = match.groups()
        image = re.search(r"!\[[^]]*\]\(images/([^)]*)\)", block)
        summary = re.search(r"### .*?정의와 위치\n\n([^\n]+)", block)
        result.append({"code": code, "defect_type": LESSON_CODES[index], "title": title,
                       "english": english, "image": image.group(1) if image else None,
                       "summary": summary.group(1) if summary else "", "body_md": block})
    return result


def render_md(source: str) -> str:
    # 번들 문서만 변환한다. 이미지 경로에 프록시 접두사를 붙인다.
    source = re.sub(r"\]\(images/", f"]({config.ROOT_PATH}/static/learning/images/", source)
    return markdown.markdown(source, extensions=["tables", "fenced_code"])


def _upsert_class(conn: sqlite3.Connection, slug: str, title: str, description: str, order: int) -> int:
    row = db.one(conn, "SELECT id FROM edu_classes WHERE slug=?", (slug,))
    if row:
        conn.execute("UPDATE edu_classes SET title=?, description=?, sort_order=? WHERE id=?",
                     (title, description, order, row["id"]))
        return row["id"]
    return db.insert(conn, "edu_classes", {"slug": slug, "title": title, "description": description,
                                          "sort_order": order})


def _upsert_article(conn: sqlite3.Connection, class_id: int, slug: str, title: str, summary: str,
                    body: str, types: list[str], order: int) -> int:
    row = db.one(conn, "SELECT id FROM edu_articles WHERE slug=?", (slug,))
    data = (class_id, title, summary, body, json.dumps(types), order)
    if row:
        conn.execute("""UPDATE edu_articles SET class_id=?, title=?, summary=?, body_md=?,
                    related_defect_types=?, sort_order=? WHERE id=?""", (*data, row["id"]))
        return row["id"]
    return db.insert(conn, "edu_articles", {"class_id": class_id, "slug": slug, "title": title,
                     "summary": summary, "body_md": body, "related_defect_types": json.dumps(types),
                     "sort_order": order})


def _upsert_quiz(conn: sqlite3.Connection, article_id: int, key: str, item: dict,
                 choices: list[dict], answer: str, kind: str, order: int) -> None:
    data = {"article_id": article_id, "content_key": key, "kind": kind, "quiz_type": "CONCEPT",
            "question": item["question"], "ref_defect_type": item.get("defect_type"),
            "choices_json": json.dumps(choices, ensure_ascii=False), "answer_key": answer,
            "answer_text": item.get("answer") or "",
            "explanation": item.get("explanation") or item.get("answer") or "",
            "sort_order": order}
    row = db.one(conn, "SELECT id FROM edu_quizzes WHERE content_key=?", (key,))
    if row:
        fields = ", ".join(f"{k}=?" for k in data)
        conn.execute(f"UPDATE edu_quizzes SET {fields} WHERE id=?", (*data.values(), row["id"]))
    else:
        db.insert(conn, "edu_quizzes", data)


def ensure_content(conn: sqlite3.Connection) -> None:
    """35-Web 팀 자료를 기존 edu_* 테이블에 멱등 반영한다."""
    foundation = _upsert_class(conn, "weld-foundations", "용접부 검수 기초", "검수 절차와 결함사전", 1)
    defects = _upsert_class(conn, "weld-defects", "용접 결함 9개 단원", "형상·원인·확인 항목", 2)
    material_ids = {}
    for order, (slug, title, summary) in enumerate(MATERIALS, 1):
        body = (CONTENT / f"{slug}.md").read_text(encoding="utf-8")
        material_ids[slug] = _upsert_article(conn, foundation, f"learning-{slug}", title, summary, body, [], order)
    by_code = {}
    lesson_list = lessons()
    for order, lesson in enumerate(lesson_list, 1):
        by_code[lesson["code"]] = lesson
        lesson["article_id"] = _upsert_article(conn, defects, f"learning-{lesson['code'].lower()}",
              f"{lesson['title']} ({lesson['english']})", lesson["summary"], lesson["body_md"],
              [lesson["defect_type"]], order)
    for order, lesson in enumerate(lesson_list, 1):
        options = [lesson_list[(order - 1 + step) % len(lesson_list)] for step in (0, 2, 4, 6)]
        _upsert_quiz(conn, lesson["article_id"], lesson["code"],
                     {"question": lesson["summary"], "explanation": lesson["summary"],
                      "defect_type": lesson["defect_type"]},
                     [{"key": x["code"], "label": f"{x['title']} ({x['english']})"} for x in options],
                     lesson["code"], "choice", order)
    written = _load_json("training_quiz_bank.json")
    scenarios = _load_json("work_scenario_quiz_bank.json")
    for order, item in enumerate(written + scenarios, 10):
        code = item.get("lesson") or item.get("related_lesson")
        lesson = by_code.get(code)
        _upsert_quiz(conn, lesson["article_id"] if lesson else material_ids["cases"], item["id"],
                     {**item, "defect_type": lesson["defect_type"] if lesson else None},
                     [{"key": "understood", "label": "이해 완료"},
                      {"key": "review", "label": "복습 필요"}], "understood", "written", order)


def quiz_row(conn: sqlite3.Connection, key: str) -> dict | None:
    row = db.one(conn, """SELECT q.*, a.title AS lesson_title, a.slug AS lesson_slug
                         FROM edu_quizzes q JOIN edu_articles a ON a.id=q.article_id
                         WHERE q.content_key=?""", (key.upper(),))
    if not row:
        return None
    result = dict(row)
    result["choices"] = json.loads(result["choices_json"])
    return result
