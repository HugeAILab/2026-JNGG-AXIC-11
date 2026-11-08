"""기준 데이터 (TODO.md 7.4, 8.11): 결함 유형 사전 · 모델 A 클래스 대응표 · AI 모델 등록부.

스키마 버전 2 마이그레이션과 시드가 함께 쓴다. 여러 번 실행해도 안전하다.
- defect_types : 코드 기준으로 덮어쓴다 (문구의 원본은 seed/defect_catalog.py)
- a_class_map  : 없을 때만 넣는다 (관리자가 고친 점수 기준·B 실행 여부를 지키기 위해)
- ai_models    : 없을 때만 넣는다 (관리자가 바꾼 활성 모델을 지키기 위해)
"""
from __future__ import annotations

import json
import sqlite3

from . import config
from .seed import defect_catalog as cat

DOMAINS = ("WELD", "SURFACE", "INSULATION", "CABLE", "BINDING", "CUTTING")
DOMAIN_LABELS = cat.DOMAIN_LABELS
B_DICT_CODES = [t["code"] for t in cat.DEFECT_TYPES]  # 모델 B 용접 결함 사전 10종 (NORMAL 포함)

# 모델 A 분류명 → (영역, 불량 여부, 내부 코드, B 실행 여부)  (TODO.md 7.4 ③)
A_CLASS_MAP: list[tuple[str, str, str, int, str, int]] = [
    # a_class, source_name, domain, is_defect, maps_to, triggers_b
    ("용접불량", "poorweld", "WELD", 1, "WELD_DEFECT", 1),
    ("용접블로우홀", "blowhole", "WELD", 1, "POROSITY", 1),
    ("균열", "crack", "WELD", 1, "CRACK", 1),
    ("용접양품", "goodweld", "WELD", 0, "NORMAL", 0),
    ("도장흐름", "", "SURFACE", 1, "COATING_RUN", 0),
    ("도막떨어짐", "", "SURFACE", 1, "COATING_PEEL", 0),
    ("도막분리", "", "SURFACE", 1, "COATING_DELAMINATION", 0),
    ("스크래치", "", "SURFACE", 1, "SCRATCH", 0),
    ("보온재손상", "", "INSULATION", 1, "INSULATION_DAMAGE", 0),
    ("케이블손상", "", "CABLE", 1, "CABLE_DAMAGE", 0),
    ("케이블양품", "", "CABLE", 0, "NORMAL", 0),
    ("바인딩불량", "", "BINDING", 1, "BINDING_DEFECT", 0),
    ("바인딩양품", "", "BINDING", 0, "NORMAL", 0),
    ("절단불량", "", "CUTTING", 1, "CUTTING_DEFECT", 0),
    ("절단양품", "", "CUTTING", 0, "NORMAL", 0),
]
# 영역별 양품 클래스 (도장·보온재는 분할 모델에 양품 클래스가 없음)
GOOD_CLASS = {"WELD": "용접양품", "CABLE": "케이블양품", "BINDING": "바인딩양품", "CUTTING": "절단양품"}


def all_defect_types() -> list[dict]:
    out = []
    for t in cat.DEFECT_TYPES:
        out.append({**t, "domain": "WELD", "in_b_dict": 1})
    for t in cat.EXTRA_TYPES:
        out.append({**t, "in_b_dict": 0})
    for t in out:
        aliases = {t["name_ko"], t["name_ko"].replace(" ", ""), t["name_en"].lower(), t["code"].lower()}
        aliases.update(cat.EXTRA_ALIASES.get(t["code"], []))
        t["aliases"] = sorted(a for a in aliases if a)
    return out


def default_ai_models() -> list[dict]:
    live = config.AI_MODE != "mock"
    has_b = bool(config.MODEL_B_URL)
    return [
        {"key": "model_a", "name": "MaskDINO 영역 분할 (AIStream)", "stage": "A", "provider": "aistream_seg",
         "endpoint_env": "SEGMENTATION_BASE_URL", "api_key_env": None, "version": "LNG Tank Quality Inspection v0.4.0",
         "enabled": int(live), "timeout_s": int(config.SEGMENTATION_TIMEOUT), "concurrency": 2},
        {"key": "model_b", "name": "용접 상세 분석 API", "stage": "B", "provider": "weld_api",
         "endpoint_env": "MODEL_B_URL", "api_key_env": "MODEL_B_API_KEY", "version": None,
         "enabled": int(live and has_b), "timeout_s": int(config.MODEL_B_TIMEOUT), "concurrency": 2},
        {"key": "mock_a", "name": "모의 모델 A (시드·개발용)", "stage": "A", "provider": "mock",
         "endpoint_env": None, "api_key_env": None, "version": "mock-seg-0", "enabled": int(not live),
         "timeout_s": 5, "concurrency": 4},
        {"key": "mock_b", "name": "모의 모델 B (시드·개발용)", "stage": "B", "provider": "mock",
         "endpoint_env": None, "api_key_env": None, "version": "mock-weld-0", "enabled": int(not live),
         "timeout_s": 5, "concurrency": 4},
    ]


def ensure_reference_data(conn: sqlite3.Connection, now: str) -> None:
    for t in all_defect_types():
        conn.execute("""
            INSERT INTO defect_types (code, name_ko, name_en, domain, in_b_dict, aliases, description, causes,
                                      remedies, keywords, severity)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(code) DO UPDATE SET
              name_ko=excluded.name_ko, name_en=excluded.name_en, domain=excluded.domain,
              in_b_dict=excluded.in_b_dict, aliases=excluded.aliases, description=excluded.description,
              causes=excluded.causes, remedies=excluded.remedies, keywords=excluded.keywords,
              severity=excluded.severity""",
            (t["code"], t["name_ko"], t["name_en"], t["domain"], t["in_b_dict"],
             json.dumps(t["aliases"], ensure_ascii=False), t["description"], t["causes"], t["remedies"],
             json.dumps(t["keywords"], ensure_ascii=False), t["severity"]))
    for a_class, src, domain, is_def, maps_to, trig in A_CLASS_MAP:
        conn.execute("""INSERT OR IGNORE INTO a_class_map
                        (a_class, source_name, domain, is_defect, maps_to, threshold, triggers_b, updated_at)
                        VALUES (?,?,?,?,?,0.5,?,?)""", (a_class, src or None, domain, is_def, maps_to, trig, now))
    for m in default_ai_models():
        conn.execute("""INSERT OR IGNORE INTO ai_models
                        (key, name, stage, provider, endpoint_env, api_key_env, version, enabled, timeout_s,
                         concurrency, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                     (m["key"], m["name"], m["stage"], m["provider"], m["endpoint_env"], m["api_key_env"],
                      m["version"], m["enabled"], m["timeout_s"], m["concurrency"], now))
