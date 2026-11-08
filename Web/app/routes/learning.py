"""31-Web 로그인 포털의 교육 과정·퀴즈."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request

from .. import auth, db
from ..services import activity_log, learning as svc
from ..web import render

router = APIRouter()


def _page(request: Request, view: str, ctx: dict, active: str = "learning.classes"):
    return render(request, "portal/learning/page.html", {"view": view, **ctx}, active=active)


@router.get("/portal/learning")
def index(request: Request, user: dict = Depends(auth.require_login)):
    with db.connect() as conn:
        classes = [dict(r) for r in db.all_(conn, "SELECT * FROM edu_classes WHERE slug LIKE 'weld-%' ORDER BY sort_order")]
        articles = [dict(r) for r in db.all_(conn, "SELECT * FROM edu_articles WHERE slug LIKE 'learning-%' ORDER BY class_id, sort_order")]
    for cls in classes:
        cls["articles"] = [a for a in articles if a["class_id"] == cls["id"]]
    return _page(request, "index", {"classes": classes})


@router.get("/portal/learning/material/{slug}")
def material(request: Request, slug: str, user: dict = Depends(auth.require_login)):
    if slug not in {m[0] for m in svc.MATERIALS}:
        raise HTTPException(404, "교육자료가 없습니다.")
    return _article(request, f"learning-{slug}")


def _article(request: Request, slug: str):
    with db.connect() as conn:
        row = db.one(conn, "SELECT * FROM edu_articles WHERE slug=?", (slug,))
        if row:
            user = auth.get_user(request)
            activity_log.log("SYSTEM", "LEARNING_VIEW", f"교육자료 열람 · {row['title']}",
                             {"article_slug": slug}, ("edu_article", row["id"]),
                             user_id=user["id"] if user else None, conn=conn)
    if not row:
        raise HTTPException(404, "학습 단원이 없습니다.")
    article = dict(row)
    return _page(request, "article", {"article": article, "body_html": svc.render_md(article["body_md"]),
                                     "lesson_code": slug.removeprefix("learning-").upper()})


@router.get("/portal/learning/quiz")
def quiz_list(request: Request, user: dict = Depends(auth.require_login)):
    with db.connect() as conn:
        rows = db.all_(conn, """SELECT q.content_key, q.kind, q.question, q.sort_order, a.title AS lesson_title
                               FROM edu_quizzes q JOIN edu_articles a ON a.id=q.article_id
                               WHERE q.content_key IS NOT NULL ORDER BY q.sort_order, q.id""")
    quizzes = [dict(r) for r in rows]
    return _page(request, "quiz_list", {"quizzes": quizzes}, "learning.quiz")


@router.get("/portal/learning/quiz/history")
def history(request: Request, user: dict = Depends(auth.require_login)):
    with db.connect() as conn:
        rows = db.all_(conn, """SELECT t.*, q.content_key, q.kind, q.question, a.title AS lesson_title
                                FROM quiz_attempts t JOIN edu_quizzes q ON q.id=t.quiz_id
                                JOIN edu_articles a ON a.id=q.article_id
                                WHERE t.user_id=? ORDER BY t.id DESC LIMIT 200""", (user["id"],))
    attempts = [dict(r) for r in rows]
    return _page(request, "history", {"attempts": attempts,
                                        "choice_count": sum(x["kind"] == "choice" for x in attempts),
                                        "correct_count": sum(x["kind"] == "choice" and x["is_correct"] for x in attempts)},
                 "learning.quiz_history")


@router.get("/portal/learning/quiz/{quiz_id}")
def quiz_detail(request: Request, quiz_id: str, user: dict = Depends(auth.require_login)):
    with db.connect() as conn:
        quiz = svc.quiz_row(conn, quiz_id)
    if not quiz:
        raise HTTPException(404, "퀴즈가 없습니다.")
    return _page(request, "quiz_detail", {"quiz": quiz}, "learning.quiz")


@router.post("/portal/learning/quiz/{quiz_id}/review", dependencies=[Depends(auth.verify_csrf)])
def quiz_review(request: Request, quiz_id: str, response: str = Form(...),
                user: dict = Depends(auth.require_login)):
    with db.connect() as conn:
        quiz = svc.quiz_row(conn, quiz_id)
    if not quiz or quiz["kind"] != "written":
        raise HTTPException(404, "서술형 퀴즈가 없습니다.")
    response = response.strip()
    if not 1 <= len(response) <= 4000:
        raise HTTPException(400, "답안을 1~4000자로 입력하세요.")
    return _page(request, "review", {"quiz": quiz, "response": response}, "learning.quiz")


@router.post("/portal/learning/quiz/{quiz_id}", dependencies=[Depends(auth.verify_csrf)])
def quiz_submit(request: Request, quiz_id: str, choice: str | None = Form(None),
                response: str | None = Form(None), assessment: str | None = Form(None),
                user: dict = Depends(auth.require_login)):
    with db.connect() as conn:
        quiz = svc.quiz_row(conn, quiz_id)
        if not quiz:
            raise HTTPException(404, "퀴즈가 없습니다.")
        if quiz["kind"] == "choice":
            if choice not in {x["key"] for x in quiz["choices"]}:
                raise HTTPException(400, "선택한 답을 확인하세요.")
            chosen = choice
            response_text = None
        else:
            if assessment not in ("understood", "review") or not response or not 1 <= len(response.strip()) <= 4000:
                raise HTTPException(400, "답안과 자기 평가를 확인하세요.")
            chosen = assessment
            response_text = response.strip()
        correct = int(chosen == quiz["answer_key"])
        db.insert(conn, "quiz_attempts", {"user_id": user["id"], "quiz_id": quiz["id"],
                  "session_key": "portal", "chosen_key": chosen, "is_correct": correct,
                  "response_text": response_text, "created_at": db.now_str()})
        outcome = ("정답" if correct else "오답") if quiz["kind"] == "choice" else \
                  ("이해 완료" if correct else "복습 필요")
        activity_log.log("QUIZ", "QUIZ_FINISH", f"퀴즈 풀이 · {quiz['content_key']} · "
                         f"{outcome}",
                         {"quiz_key": quiz["content_key"], "kind": quiz["kind"],
                          "is_correct": bool(correct)}, ("edu_quiz", quiz["id"]),
                         user_id=user["id"], conn=conn)
    return _page(request, "result", {"quiz": quiz, "chosen": chosen, "correct": correct,
                                     "response": response_text}, "learning.quiz")


@router.get("/portal/learning/{code}")
def lesson(request: Request, code: str, user: dict = Depends(auth.require_login)):
    if code.upper() not in {x["code"] for x in svc.lessons()}:
        raise HTTPException(404, "학습 단원이 없습니다.")
    return _article(request, f"learning-{code.lower()}")
