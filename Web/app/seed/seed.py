"""샘플(합성) 데이터 생성 (TODO.md 7.5).

    python -m app.seed.seed                  # DB 가 비어 있을 때만 생성
    python -m app.seed.seed --force          # 기존 DB 를 지우고 다시 생성 (서버를 먼저 멈출 것)
    python -m app.seed.seed --force --base-date 2026-11-07

- random.Random(2026) 고정 → 같은 기준일이면 항상 같은 데이터
- 모든 일시는 기준일(SEED_BASE_DATE, 기본 = 실행 시각)에서 거꾸로 계산 → 심사가 며칠 뒤여도 '최근 30일'이 비지 않음
- 모두 가상의 합성 데이터. 실제 기업·인물 정보 없음. 검사 건은 is_sample=1
- 1단계 범위: 계정·결함 사전·검사 120건·처리 이력·활동 로그(업로드 묶음 포함)
  (보고서·교육·퀴즈·AI 분석·RAG 조각은 해당 단계에서 이 파일에 추가한다)
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .. import auth, config, db, reference
from ..services import activity_log, sample_ai
from . import defect_catalog as cat
from .accounts import DEMO_ACCOUNTS, DEMO_PASSWORD

SEED = 2026
N_INSPECTIONS = 120
DAYS_BACK = 45
PROJECT = "LNG-PJT-A"
PROCESS_DECK = ["GTAW"] * 4 + ["SMAW"] * 3 + ["FCAW"] * 2 + ["GMAW"] * 2 + ["SAW"]  # 약 35/25/20/15/5 %
POSITIONS_G = ["1G", "2G", "3G", "4G"]
WELDERS = [f"W-{i:02d}" for i in range(1, 16)]
RISKY_WELDERS = {"W-03", "W-11"}  # 용접사별 불량 Top 5 가 의미 있게 보이도록 약간 높은 불량률
ASSIGNEES = ["박용접", "박용접", "용접1팀 반장", "용접2팀 반장"]
DEFECT_STATUS_SHARE = {"PASS": .55, "REINSPECT": .15, "REWORK": .15, "DEFECT": .12}  # 나머지 REJECT
FMT = db.TS_FMT


@dataclass
class Batch:
    """업로드 1회 = UPLOAD 로그 1행"""
    idx: int
    at: datetime
    tank: str
    block: str
    process: str
    inspector_id: int
    welders: list[str]
    size: int
    log_id: int | None = None
    insp_ids: list[int] = field(default_factory=list)
    work_type: str = "WELD"


def _ts(d: datetime) -> str:
    return d.strftime(FMT)


def _pick_weighted(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def _fill(rng: random.Random, tpl: str, b: Batch, joint: str) -> str:
    return tpl.format(block=b.block, joint=joint, proc=b.process, pos=rng.choice(cat.POSITIONS),
                      n=rng.randint(1, 4), k=rng.randint(2, 7), len=rng.randint(5, 45))


def _base_now(base_date: str | None) -> datetime:
    real_now = db.now_kst()
    if base_date:
        d = date.fromisoformat(base_date)
        if d < real_now.date():  # 지난 날짜를 기준일로 주면 그날 18:00 기준
            return datetime(d.year, d.month, d.day, 18, 0, 0, tzinfo=db.KST)
    return real_now  # 오늘(또는 미래) 기준이면 실제 현재 시각 — 미래 시각 데이터가 생기지 않게


# ── 생성 ─────────────────────────────────────────────────────

def _make_batches(rng: random.Random, now: datetime, ids: dict[str, int]) -> list[Batch]:
    sizes: list[int] = []
    while sum(sizes) < N_INSPECTIONS:
        sizes.append(min(rng.randint(8, 12), N_INSPECTIONS - sum(sizes)))
    if sizes[-1] < 4 and len(sizes) > 1:  # 마지막 묶음이 너무 작으면 앞 묶음에 합침
        last = sizes.pop()
        sizes[-1] += last

    # 날짜: 1~45일 전 중 평일 가중(주말 0.25)으로 묶음 수만큼 서로 다른 날
    days = list(range(1, DAYS_BACK + 1))
    chosen: list[int] = []
    while len(chosen) < len(sizes):
        w = [(0.25 if (now - timedelta(days=d)).weekday() >= 5 else 1.0) for d in days]
        d = rng.choices(days, weights=w, k=1)[0]
        days.remove(d)
        chosen.append(d)
    chosen.sort(reverse=True)  # 오래된 날부터

    # 용접법은 묶음마다 1개. 재발 분석 시연용 묶음(TANK-2 / B-07 / GTAW 기공 반복)에
    # 덱의 GTAW 를 먼저 배정해 전체 비율(약 35/25/20/15/5 %)이 치우치지 않게 한다
    n = len(sizes)
    repeat_idx = [i for i in (1, 5, 9) if i < n]
    deck = PROCESS_DECK[:]
    rng.shuffle(deck)
    deck = deck[:n] if n <= len(deck) else deck + rng.choices(PROCESS_DECK, k=n - len(deck))
    for _ in repeat_idx:  # 시연용 묶음 수만큼 덱에서 GTAW 를 빼고(없으면 아무거나) 그 자리에 끼운다
        deck.remove("GTAW") if "GTAW" in deck else deck.pop()
    for i in repeat_idx:
        deck.insert(i, "GTAW")
    batches = []
    for i, (d, size) in enumerate(zip(chosen, sizes)):
        day = (now - timedelta(days=d)).replace(hour=rng.randint(8, 15), minute=rng.randint(0, 59), second=0)
        tank, block, proc = f"TANK-{rng.randint(1, 4)}", f"B-{rng.randint(1, 24):02d}", deck[i]
        if i in repeat_idx:
            tank, block = "TANK-2", "B-07"
        inspector = ids["manager"] if rng.random() < 0.2 else ids["inspector"]
        batches.append(Batch(i, day, tank, block, proc, inspector, rng.sample(WELDERS, rng.randint(2, 4)), size))
    return batches


def _ai_result(rng: random.Random, final_defect: bool, true_type: str | None) -> dict:
    """시드용 모의 AI 결과. 약 92% 는 검사자 확정과 일치."""
    agree = rng.random() < 0.92
    if final_defect:
        if agree:
            t, conf = true_type, rng.uniform(0.72, 0.99)
        elif rng.random() < 0.5:
            t, conf = "NORMAL", rng.uniform(0.55, 0.72)            # 놓침
        else:
            t, conf = rng.choice(cat.CONFUSABLE[true_type]), rng.uniform(0.55, 0.75)  # 유형 혼동
    else:
        if agree:
            t, conf = "NORMAL", rng.uniform(0.80, 0.99)
        else:
            t, conf = rng.choice(["SPATTER", "BEAD_IRREGULAR", "POROSITY"]), rng.uniform(0.55, 0.70)  # 과검출
    is_def = t != "NORMAL"
    return {"label": t, "is_defect": int(is_def), "defect_type": t, "confidence": round(conf, 3),
            "agree": int(is_def == final_defect and (not final_defect or t == true_type))}


def _reinspect_ai(rng: random.Random, ok: bool, true_type: str) -> dict:
    t, conf = ("NORMAL", rng.uniform(0.82, 0.98)) if ok else (true_type, rng.uniform(0.70, 0.95))
    return {"label": t, "is_defect": int(t != "NORMAL"), "defect_type": t, "confidence": round(conf, 3)}


def _snapshot(ai: dict, *, reinspect: bool = False) -> str:
    return json.dumps({"label": ai["label"], "is_defect": bool(ai["is_defect"]), "defect_type": ai["defect_type"],
                       "confidence": ai["confidence"], "model_key": "mock", "model_version": "seed-synthetic",
                       "reinspect": reinspect, "source": "seed"}, ensure_ascii=False)


def _assign_defect_statuses(rng: random.Random, defect_items: list[dict]) -> None:
    """오래된 불량일수록 종결(PASS/REJECT), 최근일수록 미결이 되도록 배정."""
    n = len(defect_items)
    quota = {k: round(v * n) for k, v in DEFECT_STATUS_SHARE.items()}
    quota["REJECT"] = max(1, n - sum(quota.values()))
    quota["PASS"] = n - quota["REJECT"] - quota["REINSPECT"] - quota["REWORK"] - quota["DEFECT"]
    ordered = sorted(defect_items, key=lambda it: it["t0"] + timedelta(days=rng.uniform(-10, 10)))
    n_closed = quota["PASS"] + quota["REJECT"]
    closed, open_ = ordered[:n_closed], ordered[n_closed:]
    rng.shuffle(closed)
    rng.shuffle(open_)
    for i, it in enumerate(closed):
        it["final_status"] = "REJECT" if i < quota["REJECT"] else "PASS"
    labels = ["REINSPECT"] * quota["REINSPECT"] + ["REWORK"] * quota["REWORK"] + ["DEFECT"] * quota["DEFECT"]
    for it, s in zip(open_, labels):
        it["final_status"] = s


def _rework_rounds(rng: random.Random, status: str) -> int:
    """완료한 수정 횟수(rework_count). PASS 는 1회 85% · 2회 12% · 3회 3%"""
    if status == "PASS":
        return rng.choices([1, 2, 3], weights=[85, 12, 3])[0]
    if status == "REJECT":
        return 3
    if status == "REINSPECT":
        return rng.choices([1, 2], weights=[85, 15])[0]
    return rng.choices([0, 1], weights=[85, 15])[0]  # DEFECT·REWORK: 이전 재검사 불합격이 있었을 수 있음


def _timeline(rng: random.Random, it: dict, ids: dict[str, int]) -> list[dict]:
    """상태에 맞는 이벤트 목록 (시각은 t0 기준 시간 오프셋 → 나중에 now 이전으로 압축)"""
    insp_actor = it["inspector_id"]
    T = it["true_type"]
    ev: list[dict] = []
    h = 0.0

    def add(action, frm, to, dh, actor, comment, snap=None, extra=None):
        nonlocal h
        h += dh
        ev.append({"action": action, "from": frm, "to": to, "h": h, "actor": actor,
                   "comment": comment, "snap": snap, **(extra or {})})

    ai = it["ai"]
    name = cat_name(ai["defect_type"])
    add("AI_INSPECT", "PENDING", "AI_DONE", rng.uniform(0.01, 0.03), None,
        f"AI 판정: {name} (신뢰도 {ai['confidence']:.0%})", _snapshot(ai))

    if not it["final_defect"]:
        add("CONFIRM_OK", "AI_DONE", "PASS", rng.uniform(0.2, 3), insp_actor, "정상 확정")
        return ev

    add("CONFIRM_DEFECT", "AI_DONE", "DEFECT", rng.uniform(0.2, 3), insp_actor, it["finding"])
    status, r = it["final_status"], it["rework_count"]
    worker = ids["worker"]
    for rnd in range(1, r + 1):
        add("REWORK_PLAN", "DEFECT", "REWORK", rng.uniform(4, 30), worker, None, extra={"plan_round": rnd})
        add("REWORK_DONE", "REWORK", "REINSPECT", rng.uniform(12, 60), worker, None, extra={"done_round": rnd})
        last = rnd == r
        if last and status == "REINSPECT":
            return ev
        ok = last and status == "PASS"
        rai = _reinspect_ai(rng, ok, T)
        if ok:
            add("REINSPECT_OK", "REINSPECT", "PASS", rng.uniform(2, 24), insp_actor,
                "재검사 합격 — 결함 제거 확인", _snapshot(rai, reinspect=True), {"rai": rai})
            return ev
        add("REINSPECT_NG", "REINSPECT", "DEFECT", rng.uniform(2, 24), insp_actor,
            rng.choice(cat.REINSPECT_NG), _snapshot(rai, reinspect=True), {"rai": rai})
    if status == "REJECT":
        add("REJECT", "DEFECT", "REJECT", rng.uniform(2, 24), ids["admin"],
            f"재작업 {r}회 후에도 결함 잔존 — 폐기·재제작 결정")
    elif status == "REWORK":
        add("REWORK_PLAN", "DEFECT", "REWORK", rng.uniform(4, 30), worker, None, extra={"plan_round": r + 1})
    return ev


NONWELD_BATCHES = [  # (탱크, 블록, 작업 종류, [(영역, 정답 유형 또는 None=정상)])
    ("TANK-1", "B-21", "SURFACE", [("SURFACE", "COATING_PEEL"), ("SURFACE", "COATING_PEEL"), ("SURFACE", "COATING_RUN"),
                                  ("SURFACE", "COATING_DELAMINATION"), ("SURFACE", "SCRATCH"), ("SURFACE", None)]),
    ("TANK-3", "B-22", "MIXED", [("INSULATION", "INSULATION_DAMAGE"), ("INSULATION", "INSULATION_DAMAGE"),
                                ("INSULATION", "INSULATION_DAMAGE"), ("CABLE", "CABLE_DAMAGE"), ("CABLE", "CABLE_DAMAGE"),
                                ("BINDING", "BINDING_DEFECT"), ("CUTTING", "CUTTING_DEFECT"), ("CABLE", None),
                                ("BINDING", None), ("CUTTING", None)]),
]


def _make_nonweld(rng: random.Random, now: datetime, ids: dict[str, int], batches: list, items: list,
                  day_seq: dict[str, int]) -> int:
    """용접 외 작업 샘플 16건 (TODO.md 7.5). 상태는 간단한 비율로 정하고 이력은 용접과 같은 규칙으로 만든다."""
    used_days = {b.at.date() for b in batches}
    loc_seq: dict[str, int] = {}
    n = 0
    for tank, block, work, specs in NONWELD_BATCHES:
        while True:
            day = now - timedelta(days=rng.randint(2, 30))
            if day.date() not in used_days and day.weekday() < 5:
                used_days.add(day.date())
                break
        at = day.replace(hour=rng.randint(9, 15), minute=rng.randint(0, 59), second=0)
        b = Batch(len(batches), at, tank, block, "-", ids["inspector"], [], len(specs), work_type=work)
        batches.append(b)
        for k, (domain, true_type) in enumerate(specs):
            prefix = cat.LOCATION_PREFIX[domain]
            loc_seq[prefix] = loc_seq.get(prefix, 0) + 1
            joint = f"{prefix}-{loc_seq[prefix]:04d}"
            t0 = at + timedelta(seconds=20 * k)
            dkey = t0.strftime("%Y%m%d")
            day_seq[dkey] = day_seq.get(dkey, 0) + 1
            final_defect = true_type is not None
            tpl = rng.choice(cat.FINDINGS[true_type] if final_defect else cat.NONWELD_NORMAL_FINDINGS)
            finding = tpl.format(block=block, joint=joint, k=rng.randint(2, 5), len=rng.randint(10, 120))
            if final_defect and rng.random() > 0.9:  # 약 10% 는 AI 가 놓침
                ai = {"label": "NORMAL", "is_defect": 0, "defect_type": "NORMAL",
                      "confidence": round(rng.uniform(0.55, 0.7), 3), "agree": 0}
            elif final_defect:
                ai = {"label": true_type, "is_defect": 1, "defect_type": true_type,
                      "confidence": round(rng.uniform(0.7, 0.97), 3), "agree": 1}
            else:
                ai = {"label": "NORMAL", "is_defect": 0, "defect_type": "NORMAL",
                      "confidence": round(rng.uniform(0.8, 0.97), 3), "agree": 1}
            it = {"id": len(items) + 1, "batch": b, "joint": joint, "welder": None, "t0": t0,
                  "code": f"INS-{dkey}-{day_seq[dkey]:04d}", "inspector_id": b.inspector_id,
                  "final_defect": final_defect, "true_type": true_type, "finding": finding, "ai": ai,
                  "position": None, "work_type": domain}
            if final_defect:
                it["final_status"] = rng.choices(["PASS", "DEFECT", "REWORK", "REINSPECT"], weights=[50, 20, 15, 15])[0]
            b.insp_ids.append(it["id"])
            items.append(it)
            n += 1
    return n


_TYPE_NAMES = {t["code"]: t["name_ko"] for t in cat.DEFECT_TYPES + cat.EXTRA_TYPES}


def cat_name(code: str | None) -> str:
    return _TYPE_NAMES.get(code or "", code or "-")


# ── 실행 ─────────────────────────────────────────────────────

def _delete_db() -> None:
    for suffix in ("", "-wal", "-shm"):
        p = config.DB_PATH.with_name(config.DB_PATH.name + suffix)
        if p.exists():
            try:
                p.unlink()
            except PermissionError:
                sys.exit(f"[seed] {p} 를 지울 수 없습니다. 실행 중인 서버를 먼저 멈추세요 (./run.sh stop).")


def run(force: bool = False, base_date: str | None = None) -> dict:
    config.ensure_dirs()
    if db.db_exists():
        with db.connect() as conn:
            db.init_schema(conn)
            has_data = (db.scalar(conn, "SELECT COUNT(*) FROM users") or 0) + \
                       (db.scalar(conn, "SELECT COUNT(*) FROM inspections") or 0) > 0
        if has_data and not force:
            sys.exit("[seed] DB에 이미 데이터가 있습니다. 지우고 다시 만들려면 --force (서버를 먼저 멈출 것).")
        _delete_db()

    rng = random.Random(SEED)
    now = _base_now(base_date or config.SEED_BASE_DATE or None)
    limit = now - timedelta(hours=1)

    with db.connect() as conn:
        db.init_schema(conn)

        # 1) 사용자
        ids: dict[str, int] = {}
        created = _ts(now - timedelta(days=DAYS_BACK + 1))
        for a in DEMO_ACCOUNTS:
            ids[a["username"]] = db.insert(conn, "users", {
                "username": a["username"], "password_hash": auth.hash_password(DEMO_PASSWORD),
                "name": a["name"], "role": a["role"], "created_at": created})

        # 2) 기준 데이터: 결함 유형 사전(용접 10종 + 용접 외) · A 클래스 대응표 · 모델 등록부 (reference.py)
        reference.ensure_reference_data(conn, _ts(now))
        sev = {t["code"]: t["severity"] for t in reference.all_defect_types()}

        # 3) 업로드 묶음 → 검사 건 (id 는 1부터 순서대로 미리 정한다)
        batches = _make_batches(rng, now, ids)
        items: list[dict] = []
        joint_no = 0
        day_seq: dict[str, int] = {}
        for b in batches:
            for k in range(b.size):
                joint_no += 1
                joint = f"J-{joint_no:04d}"
                welder = rng.choice(b.welders)
                p_def = 0.35 + (0.22 if welder in RISKY_WELDERS else 0)  # 전체 약 38%
                final_defect = rng.random() < p_def
                true_type = None
                if final_defect:
                    w = dict(cat.DEFECT_WEIGHTS)
                    if b.block == "B-07" and b.process == "GTAW":
                        w["POROSITY"] *= 4
                    true_type = _pick_weighted(rng, w)
                t0 = b.at + timedelta(seconds=20 * k)
                dkey = t0.strftime("%Y%m%d")
                day_seq[dkey] = day_seq.get(dkey, 0) + 1
                it = {
                    "id": len(items) + 1, "batch": b, "joint": joint, "welder": welder, "t0": t0,
                    "code": f"INS-{dkey}-{day_seq[dkey]:04d}", "inspector_id": b.inspector_id,
                    "final_defect": final_defect, "true_type": true_type,
                    "finding": _fill(rng, rng.choice(cat.FINDINGS[true_type] if final_defect else cat.NORMAL_FINDINGS), b, joint),
                    "ai": _ai_result(rng, final_defect, true_type),
                    "position": rng.choice(POSITIONS_G),
                }
                b.insp_ids.append(it["id"])
                items.append(it)

        defect_items = [it for it in items if it["final_defect"]]
        _assign_defect_statuses(rng, defect_items)
        # 3-2) 용접 외 작업 샘플 (도장·보온재·케이블·바인딩·절단) — 별도 난수로 만들어 용접 샘플은 그대로 둔다
        nonweld = _make_nonweld(random.Random(SEED + 1), now, ids, batches, items, day_seq)
        for it in items:
            it.setdefault("final_status", "PASS")
            it["rework_count"] = _rework_rounds(rng, it["final_status"]) if it["final_defect"] else 0
            it["events"] = _timeline(rng, it, ids)
            # now 이전에 끝나도록 압축
            total_h = it["events"][-1]["h"]
            max_h = max(0.05, (limit - it["t0"]).total_seconds() / 3600)
            scale = min(1.0, max_h / total_h) if total_h > 0 else 1.0
            for e in it["events"]:
                e["at"] = it["t0"] + timedelta(hours=e["h"] * scale)

        # 4) 활동 로그 (시간순으로 넣기 위해 먼저 모은다)
        logs: list[dict] = []

        def L(at, log_type, action, summary, detail=None, target=None, user=None, level="INFO", ip=None, key=None):
            logs.append({"at": at, "type": log_type, "action": action, "summary": summary, "detail": detail,
                         "target": target, "user": user, "level": level, "ip": ip, "key": key})

        ipmap = {uid: f"10.20.{i}.{30 + i}" for i, uid in enumerate(ids.values(), start=1)}
        login_days: dict[tuple[int, str], datetime] = {}

        def touch(uid, at):
            k = (uid, at.strftime("%Y-%m-%d"))
            if k not in login_days or at < login_days[k]:
                login_days[k] = at

        for b in batches:
            files = [f"IMG_{b.at.strftime('%Y%m%d')}_{i + 1:03d}.jpg" for i in range(b.size)]
            wt = b.work_type
            L(b.at, "UPLOAD", "UPLOAD_PHOTOS",
              f"사진 {b.size}장 업로드 · {b.tank} / {b.block}" + ("" if wt == "WELD" else f" · {cat.DOMAIN_LABELS.get(wt, '혼합')}"),
              {"photo_count": b.size, "files": files, "source": "seed",
               "common": {"project": PROJECT, "tank_no": b.tank, "block": b.block, "weld_process": b.process,
                          "work_type": wt, "inspector_id": b.inspector_id}},
              user=b.inspector_id, ip=ipmap[b.inspector_id], key=("batch", b.idx))
            touch(b.inspector_id, b.at)

        err_targets = set(rng.sample([it["id"] for it in items], 2))
        for it in items:
            ai_ev = it["events"][0]
            if it["id"] in err_targets:
                L(ai_ev["at"] - timedelta(seconds=25), "AI", "AI_ERROR",
                  f"AI 판정 실패 · {it['code']} · 응답 시간 초과(20초), 재시도",
                  {"inspection_id": it["id"], "error": "ReadTimeout", "retry": 1}, ("inspection", it["id"]),
                  level="WARN")
            L(ai_ev["at"], "AI", "AI_PREDICT", f"AI 판정 · {it['code']} · {cat_name(it['ai']['defect_type'])}",
              {"inspection_id": it["id"], "elapsed_ms": rng.randint(320, 1450), "label": it["ai"]["label"],
               "confidence": it["ai"]["confidence"], "model_key": "mock", "source": "seed"},
              ("inspection", it["id"]))
            for e in it["events"][1:]:
                if e["actor"]:
                    touch(e["actor"], e["at"])
                if "rai" in e:
                    L(e["at"] - timedelta(minutes=rng.randint(2, 15)), "AI", "AI_PREDICT",
                      f"AI 재검사 판정 · {it['code']} · {cat_name(e['rai']['defect_type'])}",
                      {"inspection_id": it["id"], "elapsed_ms": rng.randint(320, 1450),
                       "label": e["rai"]["label"], "confidence": e["rai"]["confidence"],
                       "model_key": "mock", "reinspect": True, "source": "seed"},
                      ("inspection", it["id"]))

        for d in rng.sample(range(1, DAYS_BACK), 10):  # 품질 관리자 대시보드 확인
            touch(ids["manager"], (now - timedelta(days=d)).replace(hour=rng.randint(9, 17), minute=rng.randint(0, 59)))
        names = {v: k for k, v in ids.items()}
        disp = {a["username"]: a["name"] for a in DEMO_ACCOUNTS}
        for (uid, _), at in login_days.items():
            if at > limit:
                continue
            un = names[uid]
            L(at - timedelta(minutes=rng.randint(2, 20)), "AUTH", "LOGIN", f"로그인 · {disp[un]} ({un})",
              user=uid, ip=ipmap[uid])
        for d in rng.sample(range(2, DAYS_BACK), 3):
            at = (now - timedelta(days=d)).replace(hour=8, minute=rng.randint(30, 59))
            un = rng.choice(["inspector", "worker"])
            L(at, "AUTH", "LOGIN_FAIL", f"로그인 실패 · {un}", {"username": un}, level="WARN", ip=ipmap[ids[un]])

        logs.sort(key=lambda x: x["at"])
        batch_log: dict[int, int] = {}
        for x in logs:
            lid = activity_log.log(x["type"], x["action"], x["summary"], x["detail"], x["target"],
                                   user_id=x["user"], level=x["level"], ip=x["ip"], conn=conn,
                                   created_at=_ts(x["at"]))
            if x["key"]:
                batch_log[x["key"][1]] = lid

        # 5) 검사 건 + 처리 이력
        n_events = 0
        for it in items:
            b: Batch = it["batch"]
            T = it["true_type"]
            ai = it["ai"]
            last_ai, last_ai_at = ai, it["events"][0]["at"]
            row = {
                "id": it["id"], "code": it["code"], "upload_log_id": batch_log[b.idx], "project": PROJECT,
                "tank_no": b.tank, "block": b.block, "joint_no": it["joint"], "weld_process": b.process,
                "weld_position": it["position"], "welder": it["welder"], "inspector_id": it["inspector_id"],
                "work_type": it.get("work_type", "WELD"),
                "image_path": None, "is_sample": 1,
                "final_is_defect": int(it["final_defect"]), "defect_type": T, "severity": sev[T] if T else None,
                "finding": it["finding"], "ai_agree": ai["agree"], "rework_count": it["rework_count"],
                "status": it["events"][-1]["to"], "inspected_at": _ts(it["t0"]),
                "updated_at": _ts(it["events"][-1]["at"]),
            }
            ev_rows = []
            for e in it["events"]:
                comment = e["comment"]
                if e["action"] == "REWORK_PLAN":
                    cause = rng.choice(cat.CAUSES[T])
                    plan = rng.choice(cat.PLANS[T])
                    assignee = rng.choice(ASSIGNEES)
                    due = (e["at"] + timedelta(days=rng.randint(3, 7))).strftime("%Y-%m-%d")
                    row.update(cause=cause, action_plan=plan, assignee=assignee, due_date=due, action_result=None)
                    comment = f"추정 원인: {cause} / 조치: {plan} / 담당: {assignee} / 예정일: {due}"
                elif e["action"] == "REWORK_DONE":
                    res = rng.choice(cat.RESULTS[T])
                    row["action_result"] = res
                    comment = res
                if "rai" in e:
                    last_ai, last_ai_at = e["rai"], e["at"]
                ev_rows.append({
                    "inspection_id": it["id"], "action": e["action"], "from_status": e["from"],
                    "to_status": e["to"], "comment": comment, "image_path": None, "ai_snapshot": e["snap"],
                    "actor_id": e["actor"], "created_at": _ts(e["at"])})
            row.update(
                ai_label=last_ai["label"], ai_is_defect=last_ai["is_defect"], ai_defect_type=last_ai["defect_type"],
                ai_confidence=last_ai["confidence"], ai_raw=_snapshot(last_ai), ai_at=_ts(last_ai_at),
                model_key="mock", model_version="seed-synthetic", ai_status="DONE",
                closed_at=_ts(it["events"][-1]["at"]) if row["status"] in ("PASS", "REJECT") else None,
            )
            db.insert(conn, "inspections", row)
            for er in ev_rows:
                db.insert(conn, "inspection_events", er)
            n_events += len(ev_rows)

        # 6) 모의 AI 파이프라인 결과 (모델 A + 용접 불량이면 모델 B) — services/sample_ai.py
        n_ai = sample_ai.backfill(conn)

        summary = {
            "base_time": _ts(now), "users": len(ids),
            "defect_types": db.scalar(conn, "SELECT COUNT(*) FROM defect_types"),
            "uploads": len(batches), "inspections": len(items), "weld_defects": len(defect_items),
            "nonweld": nonweld, "events": n_events, "activity_logs": len(logs) + 1,
            "ai_model_results": db.scalar(conn, "SELECT COUNT(*) FROM ai_model_results"), "ai_filled": n_ai,
            "schema_version": db.schema_version(conn),
        }
        activity_log.log("SYSTEM", "SEED", f"샘플 데이터 생성 · 검사 {len(items)}건 · 업로드 {len(batches)}건",
                         summary, conn=conn, created_at=_ts(now))
    return summary


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m app.seed.seed", description="QCheck 샘플 데이터 생성")
    p.add_argument("--force", action="store_true", help="기존 DB 를 지우고 다시 생성")
    p.add_argument("--base-date", help="기준일 YYYY-MM-DD (기본: SEED_BASE_DATE 또는 오늘)")
    args = p.parse_args()
    s = run(force=args.force, base_date=args.base_date)
    print("[seed] 완료:", json.dumps(s, ensure_ascii=False))
    print(f"[seed] 데모 계정: {', '.join(a['username'] for a in DEMO_ACCOUNTS)} / 비밀번호 {DEMO_PASSWORD}")


if __name__ == "__main__":
    main()
