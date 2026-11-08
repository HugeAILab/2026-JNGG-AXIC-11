"""결함 유형 사전 (TODO.md 7.4) + 시드용 문장 템플릿 (TODO.md 7.5).

model_label 은 해커톤 당일 AIStream 서빙 /docs 와 실제 응답으로 확인한 뒤 채운다 (14장 #1).
모델 클래스가 아래와 다르면 이 표를 모델 기준으로 맞춘다.

템플릿 자리표시: {block} {joint} {n}(mm 수치) {k}(개수) {len}(길이 mm) {pos}(위치) {proc}(용접법)
"""
from __future__ import annotations

DEFECT_TYPES: list[dict] = [
    {
        "code": "NORMAL", "name_ko": "정상", "name_en": "Normal", "severity": "NONE",
        "description": "비드 폭·높이 균일, 표면 결함 없음",
        "causes": "—", "remedies": "—",
        "keywords": ["비드 균일", "표면 양호", "결함 없음"],
    },
    {
        "code": "CRACK", "name_ko": "균열", "name_en": "Crack", "severity": "CRITICAL",
        "description": "용접부·열영향부의 선형 갈라짐",
        "causes": "급랭, 수소, 구속 응력, 재료 성분",
        "remedies": "균열 전체 제거(가우징) 후 재용접, 예열·층간온도 관리",
        "keywords": ["선형 갈라짐", "열영향부", "크레이터 균열", "급랭", "수소"],
    },
    {
        "code": "POROSITY", "name_ko": "기공", "name_en": "Porosity", "severity": "MEDIUM",
        "description": "표면·내부의 둥근 구멍",
        "causes": "습기·오염, 보호가스 부족·바람, 아크 길이 과다",
        "remedies": "표면 청정, 가스 유량·차폐 점검, 결함부 제거 후 재용접",
        "keywords": ["둥근 구멍", "군집", "보호가스", "습기", "표면 기공"],
    },
    {
        "code": "UNDERCUT", "name_ko": "언더컷", "name_en": "Undercut", "severity": "MEDIUM",
        "description": "비드 가장자리 모재가 파인 홈",
        "causes": "전류 과다, 운봉 속도 과다, 토치 각도 부적정",
        "remedies": "조건 조정, 홈 부위 보수 용접 후 그라인딩",
        "keywords": ["가장자리 홈", "토우", "전류 과다", "운봉 속도", "토치 각도"],
    },
    {
        "code": "LACK_OF_FUSION", "name_ko": "융합 불량", "name_en": "Lack of Fusion", "severity": "HIGH",
        "description": "용착금속과 모재·층간이 녹아 붙지 않음",
        "causes": "입열 부족, 개선 각도 부족, 슬래그 잔류",
        "remedies": "결함부 제거, 입열·개선 형상 조정 후 재용접",
        "keywords": ["미융합", "층간", "입열 부족", "개선 각도", "경계선"],
    },
    {
        "code": "INCOMPLETE_PENETRATION", "name_ko": "용입 불량", "name_en": "Incomplete Penetration",
        "severity": "HIGH",
        "description": "이음 루트까지 녹아 들어가지 않음",
        "causes": "루트 간격 부족, 전류 부족, 속도 과다",
        "remedies": "이면 가우징 후 재용접, 개선 형상 확인",
        "keywords": ["루트", "이면", "용입 부족", "루트 간격", "전류 부족"],
    },
    {
        "code": "SLAG_INCLUSION", "name_ko": "슬래그 혼입", "name_en": "Slag Inclusion", "severity": "MEDIUM",
        "description": "용접부 내부에 비금속 이물",
        "causes": "층간 슬래그 제거 미흡",
        "remedies": "층간 청소 철저, 결함부 제거 후 재용접",
        "keywords": ["비금속 이물", "슬래그", "층간 청소", "검은 띠"],
    },
    {
        "code": "OVERLAP", "name_ko": "오버랩", "name_en": "Overlap", "severity": "LOW",
        "description": "용착금속이 모재 위로 넘쳐 덮임",
        "causes": "전류 부족, 속도 과소",
        "remedies": "넘친 부위 그라인딩, 조건 조정",
        "keywords": ["넘침", "토우 덮임", "전류 부족", "속도 과소"],
    },
    {
        "code": "SPATTER", "name_ko": "스패터", "name_en": "Spatter", "severity": "LOW",
        "description": "비드 주변에 튄 금속 알갱이",
        "causes": "전류·전압 부적정, 아크 불안정",
        "remedies": "제거(그라인딩), 조건 조정",
        "keywords": ["금속 알갱이", "비산", "아크 불안정", "전압"],
    },
    {
        "code": "BEAD_IRREGULAR", "name_ko": "비드 불균일", "name_en": "Irregular Bead", "severity": "LOW",
        "description": "비드 폭·높이가 일정하지 않음",
        "causes": "운봉 불균일, 숙련도",
        "remedies": "표면 정리, 작업자 교육",
        "keywords": ["폭 불균일", "높이 편차", "운봉", "숙련도"],
    },
]

# 시드 불량 유형 분포 (TODO.md 7.5)
DEFECT_WEIGHTS: dict[str, float] = {
    "POROSITY": 25, "UNDERCUT": 18, "BEAD_IRREGULAR": 14, "SPATTER": 12, "SLAG_INCLUSION": 10,
    "LACK_OF_FUSION": 8, "INCOMPLETE_PENETRATION": 7, "OVERLAP": 4, "CRACK": 2,
}

# AI 가 헷갈리기 쉬운 짝 (AI-검사자 불일치·퀴즈 오답 후보)
CONFUSABLE: dict[str, list[str]] = {
    "LACK_OF_FUSION": ["INCOMPLETE_PENETRATION", "SLAG_INCLUSION"],
    "INCOMPLETE_PENETRATION": ["LACK_OF_FUSION"],
    "UNDERCUT": ["OVERLAP", "BEAD_IRREGULAR"],
    "OVERLAP": ["UNDERCUT", "BEAD_IRREGULAR"],
    "POROSITY": ["SLAG_INCLUSION", "SPATTER"],
    "SLAG_INCLUSION": ["POROSITY", "LACK_OF_FUSION"],
    "SPATTER": ["POROSITY", "BEAD_IRREGULAR"],
    "BEAD_IRREGULAR": ["UNDERCUT", "OVERLAP"],
    "CRACK": ["LACK_OF_FUSION", "UNDERCUT"],
}

POSITIONS = ["비드 중앙부", "비드 시작부", "비드 종단부", "크레이터 부근", "상부 토우", "하부 토우", "루트 측"]

NORMAL_FINDINGS = [
    "{block} {joint} 비드 폭·높이 균일, 표면 결함 없음.",
    "{block} {joint} 외관 양호. 언더컷·기공 등 표면 결함 확인되지 않음.",
    "{block} {joint} {proc} 비드 형상 균일하며 스패터 없음. 합격.",
    "{block} {joint} 토우부 매끄럽게 이어짐, 이상 없음.",
]

FINDINGS: dict[str, list[str]] = {
    "CRACK": [
        "{block} {joint} {pos} 열영향부를 따라 길이 약 {len}mm 선형 균열 확인. 급랭에 의한 균열 의심.",
        "{block} {joint} 크레이터 끝단에 길이 약 {len}mm 미세 균열. 아크 종료 처리 미흡 의심.",
        "{block} {joint} 비드 중앙을 따라 종방향 갈라짐 약 {len}mm. 구속 응력 및 수소 영향 의심.",
    ],
    "POROSITY": [
        "{block} {joint} {pos} 표면에 직경 약 {n}mm 기공 {k}개 군집. 보호가스 차폐 불량 의심.",
        "{block} {joint} 비드 표면에 둥근 구멍 {k}개 산재, 최대 직경 약 {n}mm. 모재 표면 습기·오염 의심.",
        "{block} {joint} {pos}에 기공 {k}개가 길이 약 {len}mm 구간에 모여 있음. 작업 중 바람 영향 의심.",
        "{block} {joint} {proc} 비드 시작부 기공 {k}개(직경 {n}mm 내외). 아크 길이 과다 의심.",
    ],
    "UNDERCUT": [
        "{block} {joint} 비드 가장자리를 따라 모재가 길이 약 {len}mm 파여 있음. 전류 과다 의심.",
        "{block} {joint} {pos} 언더컷 깊이 약 0.{k}mm, 길이 {len}mm. 운봉 속도 과다 의심.",
        "{block} {joint} 상부 토우에 연속 홈 약 {len}mm. 토치 각도 부적정 의심.",
    ],
    "LACK_OF_FUSION": [
        "{block} {joint} {pos} 층간 경계선이 녹아 붙지 않은 부분 약 {len}mm. 입열 부족 의심.",
        "{block} {joint} 개선면과 용착금속 사이 미융합 흔적 약 {len}mm. 개선 각도 부족 의심.",
        "{block} {joint} 측벽 융합 불충분 구간 {len}mm. 슬래그 잔류로 융합 방해 의심.",
    ],
    "INCOMPLETE_PENETRATION": [
        "{block} {joint} 루트 측 용입 부족 구간 약 {len}mm. 루트 간격 부족 의심.",
        "{block} {joint} 이면 비드 형성 안 됨, 길이 약 {len}mm. 전류 부족 의심.",
        "{block} {joint} 루트부 미용입 {len}mm, {proc} 속도 과다 의심.",
    ],
    "SLAG_INCLUSION": [
        "{block} {joint} {pos} 비드 표면에 검은 띠 모양 비금속 이물 약 {len}mm. 층간 슬래그 제거 미흡 의심.",
        "{block} {joint} 층간 슬래그 잔류 흔적 {k}곳. 층간 청소 미흡 의심.",
        "{block} {joint} 비드 사이 홈에 슬래그 혼입 약 {len}mm. 운봉 각도 부적정 의심.",
    ],
    "OVERLAP": [
        "{block} {joint} 하부 토우에서 용착금속이 모재 위로 넘쳐 덮임 약 {len}mm. 전류 부족 의심.",
        "{block} {joint} {pos} 오버랩 {len}mm, 토우 각도 급함. 운봉 속도 과소 의심.",
    ],
    "SPATTER": [
        "{block} {joint} 비드 주변에 금속 알갱이 {k}개 이상 비산 부착. 전압 부적정 의심.",
        "{block} {joint} {pos} 주변 스패터 다수 부착. 아크 불안정 의심.",
        "{block} {joint} {proc} 비드 양옆 약 {len}mm 범위에 스패터. 전류·전압 조건 확인 필요.",
    ],
    "BEAD_IRREGULAR": [
        "{block} {joint} 비드 폭이 {n}~{k}mm로 일정하지 않음. 운봉 불균일 의심.",
        "{block} {joint} {pos} 비드 높이 편차 약 {n}mm. 작업자 숙련도 영향 의심.",
        "{block} {joint} 비드 파형 불규칙, 길이 {len}mm 구간 폭 편차 큼.",
    ],
}

CAUSES: dict[str, list[str]] = {
    "CRACK": ["예열 부족 상태에서 급랭", "용접봉 건조 미흡으로 인한 수소 유입", "구속이 큰 이음부의 잔류 응력"],
    "POROSITY": ["보호가스 유량 부족", "작업장 바람으로 차폐 불량", "모재 표면 습기·유분 잔류", "아크 길이 과다"],
    "UNDERCUT": ["용접 전류 과다", "운봉 속도 과다", "토치 각도 부적정"],
    "LACK_OF_FUSION": ["입열 부족", "개선 각도 부족", "층간 슬래그 잔류"],
    "INCOMPLETE_PENETRATION": ["루트 간격 부족", "용접 전류 부족", "용접 속도 과다"],
    "SLAG_INCLUSION": ["층간 슬래그 제거 미흡", "운봉 각도 부적정"],
    "OVERLAP": ["용접 전류 부족", "운봉 속도 과소"],
    "SPATTER": ["전압 부적정", "아크 불안정", "용접 전류 과다"],
    "BEAD_IRREGULAR": ["운봉 속도 불균일", "작업자 숙련도 부족"],
}

PLANS: dict[str, list[str]] = {
    "CRACK": ["균열 전체를 아크 가우징으로 제거 후 PT로 잔존 여부 확인, 예열 후 재용접",
              "결함부 그라인딩 제거, 층간온도 관리 기준 재교육 후 재용접"],
    "POROSITY": ["기공부 그라인딩 제거 후 보호가스 유량 재설정(15→20 L/min)하고 재용접",
                 "방풍막 설치, 모재 청정 후 결함부 재용접",
                 "결함부 제거, 가스 호스 누설 점검 후 재용접"],
    "UNDERCUT": ["홈 부위 보수 용접 후 그라인딩으로 형상 정리, 전류 10% 하향",
                 "운봉 속도 조정 후 토우부 보수 용접"],
    "LACK_OF_FUSION": ["결함부 가우징 제거, 입열 상향 및 개선 각도 확인 후 재용접",
                       "층간 청소 후 결함 구간 재용접, UT로 확인"],
    "INCOMPLETE_PENETRATION": ["이면 가우징 후 이면 재용접, 루트 간격 재확인",
                               "개선 형상 수정 후 루트 패스 재용접"],
    "SLAG_INCLUSION": ["결함부 그라인딩 제거, 층간 청소 철저 후 재용접",
                       "슬래그 혼입 구간 가우징 후 재용접, 운봉 각도 교정"],
    "OVERLAP": ["넘친 부위 그라인딩 제거, 전류 상향 후 토우부 정리",
                "오버랩 부위 제거 후 운봉 속도 조정"],
    "SPATTER": ["스패터 그라인딩 제거, 전압 조건 재설정",
                "스패터 제거 후 와이어 송급 상태 점검"],
    "BEAD_IRREGULAR": ["비드 표면 그라인딩 정리, 해당 용접사 운봉 재교육",
                       "불균일 구간 덧살 보수 후 표면 정리"],
}

RESULTS: dict[str, list[str]] = {
    "CRACK": ["가우징 후 PT 결과 잔존 균열 없음 확인, 예열 120℃ 적용하여 재용접 완료"],
    "POROSITY": ["결함부 제거 후 재용접 완료, 표면 기공 없음 확인", "방풍막 설치 후 재용접, 외관 양호"],
    "UNDERCUT": ["보수 용접 및 그라인딩 완료, 홈 깊이 기준 이내", "토우부 보수 완료, 외관 양호"],
    "LACK_OF_FUSION": ["가우징 후 재용접 완료, UT 지시 없음"],
    "INCOMPLETE_PENETRATION": ["이면 가우징 및 재용접 완료, 이면 비드 형성 확인"],
    "SLAG_INCLUSION": ["슬래그 제거 후 재용접 완료, 외관 이상 없음"],
    "OVERLAP": ["넘친 부위 제거 및 토우 정리 완료"],
    "SPATTER": ["스패터 제거 완료, 조건 재설정 후 시험 비드 양호"],
    "BEAD_IRREGULAR": ["표면 정리 완료, 비드 폭 편차 기준 이내"],
}

REINSPECT_NG = [
    "재검사 결과 동일 부위에 결함 일부 잔존. 추가 보수 필요.",
    "보수 부위 인접 구간에서 같은 유형 결함 재발견.",
]


# ── 2단계: 용접 세부 미분류 + 용접 외 작업 불량 (TODO.md 7.4 ②) ─────────────────
# 외관·원인·대책 문구는 팀 작성 초안이며 현장 지식이 있는 팀원이 검수한다.
# domain: WELD / SURFACE / INSULATION / CABLE / BINDING / CUTTING
EXTRA_TYPES: list[dict] = [
    {
        "code": "WELD_DEFECT", "name_ko": "용접 불량(세부 미분류)", "name_en": "Weld Defect (unclassified)",
        "domain": "WELD", "severity": "MEDIUM",
        "description": "모델 A가 찾은 용접부 이상 영역 (용접 상세 분석 전)",
        "causes": "용접 상세 분석 필요", "remedies": "용접 상세 분석·육안 확인 후 유형 확정",
        "keywords": ["용접부 이상", "용접 불량"],
    },
    {
        "code": "COATING_RUN", "name_ko": "도장흐름", "name_en": "Coating Run", "domain": "SURFACE", "severity": "LOW",
        "description": "도료가 흘러내려 두껍게 맺힘",
        "causes": "과도한 도막 두께, 희석 과다, 저온", "remedies": "도막 두께·희석비 관리, 연마 후 재도장",
        "keywords": ["흘러내림", "맺힘", "도막 두께", "희석"],
    },
    {
        "code": "COATING_PEEL", "name_ko": "도막떨어짐", "name_en": "Coating Peel-off", "domain": "SURFACE",
        "severity": "MEDIUM",
        "description": "도막이 떨어져 모재가 드러남",
        "causes": "표면 처리 불량, 오염, 충격", "remedies": "표면 처리 후 부분 재도장, 취급 주의",
        "keywords": ["모재 노출", "박리", "표면 처리", "충격"],
    },
    {
        "code": "COATING_DELAMINATION", "name_ko": "도막분리", "name_en": "Coating Delamination", "domain": "SURFACE",
        "severity": "MEDIUM",
        "description": "도막 층 사이가 들뜸",
        "causes": "층간 오염, 도장 간격 초과, 습기", "remedies": "들뜬 부위 제거 후 재도장, 도장 간격 관리",
        "keywords": ["들뜸", "층간", "도장 간격", "습기"],
    },
    {
        "code": "SCRATCH", "name_ko": "스크래치", "name_en": "Scratch", "domain": "SURFACE", "severity": "LOW",
        "description": "표면의 선형 긁힘",
        "causes": "운반·작업 중 접촉", "remedies": "부분 보수 도장, 보호재 사용",
        "keywords": ["긁힘", "선형", "접촉"],
    },
    {
        "code": "INSULATION_DAMAGE", "name_ko": "보온재손상", "name_en": "Insulation Damage", "domain": "INSULATION",
        "severity": "HIGH",
        "description": "보온재(폼) 파손·들뜸·누락",
        "causes": "충격, 시공 불량, 접착 불량", "remedies": "손상부 교체·보수, 시공 기준 점검",
        "keywords": ["보온재 파손", "폼 들뜸", "누락", "접착"],
    },
    {
        "code": "CABLE_DAMAGE", "name_ko": "케이블손상", "name_en": "Cable Damage", "domain": "CABLE", "severity": "HIGH",
        "description": "케이블 피복 손상·꺾임",
        "causes": "설치 중 마찰, 고정 불량", "remedies": "케이블 교체·보호관 설치",
        "keywords": ["피복 손상", "꺾임", "마찰", "고정"],
    },
    {
        "code": "BINDING_DEFECT", "name_ko": "바인딩불량", "name_en": "Binding Defect", "domain": "BINDING",
        "severity": "MEDIUM",
        "description": "결속 풀림·간격 불량",
        "causes": "결속 누락, 장력 부족", "remedies": "재결속, 간격·장력 기준 점검",
        "keywords": ["결속 풀림", "간격", "장력"],
    },
    {
        "code": "CUTTING_DEFECT", "name_ko": "절단불량", "name_en": "Cutting Defect", "domain": "CUTTING",
        "severity": "MEDIUM",
        "description": "절단면 거침·치수 불량",
        "causes": "절단 조건 부적정, 공구 마모", "remedies": "절단면 정리·재절단, 공구 교체",
        "keywords": ["절단면", "거침", "치수", "공구 마모"],
    },
]

# 별칭: 모델이 코드 대신 쓴 표기 → 코드 (한글명·영문명·소문자 코드는 reference.py 가 자동 추가)
EXTRA_ALIASES: dict[str, list[str]] = {
    "POROSITY": ["blowhole", "blow hole", "pore", "pores", "용접블로우홀", "블로우홀"],
    "CRACK": ["cracks", "갈라짐"],
    "LACK_OF_FUSION": ["lof", "미융합", "융합불량"],
    "INCOMPLETE_PENETRATION": ["lop", "incomplete penetration", "용입불량", "미용입"],
    "SLAG_INCLUSION": ["slag", "슬래그"],
    "BEAD_IRREGULAR": ["irregular bead", "비드불균일"],
    "WELD_DEFECT": ["용접불량", "poorweld", "poor weld"],
}

# 위치 코드 접두어 (용접은 이음부 J-)
LOCATION_PREFIX = {"WELD": "J", "SURFACE": "S", "INSULATION": "I", "CABLE": "C", "BINDING": "BD", "CUTTING": "CT"}

DOMAIN_LABELS = {"WELD": "용접", "SURFACE": "도장·표면", "INSULATION": "보온재", "CABLE": "케이블",
                 "BINDING": "바인딩", "CUTTING": "절단"}

NONWELD_FINDINGS: dict[str, list[str]] = {
    "COATING_RUN": ["{block} {joint} 도막이 흘러내려 길이 약 {len}mm로 맺힘.", "{block} {joint} 하단부 도장흐름 {k}곳, 두께 과다 의심."],
    "COATING_PEEL": ["{block} {joint} 도막이 떨어져 약 {len}mm 범위 모재 노출.", "{block} {joint} 모서리부 도막 박리 {k}곳."],
    "COATING_DELAMINATION": ["{block} {joint} 도막 층간 들뜸 약 {len}mm.", "{block} {joint} 상도·중도 사이 분리 흔적 {k}곳."],
    "SCRATCH": ["{block} {joint} 표면 선형 긁힘 길이 약 {len}mm.", "{block} {joint} 운반 중 긁힘 {k}줄."],
    "INSULATION_DAMAGE": ["{block} {joint} 보온재 표면 파손 약 {len}mm, 내부 폼 노출.", "{block} {joint} 보온재 이음부 들뜸 {k}곳."],
    "CABLE_DAMAGE": ["{block} {joint} 케이블 피복 손상 약 {len}mm.", "{block} {joint} 케이블 꺾임 {k}곳, 고정 클램프 이탈."],
    "BINDING_DEFECT": ["{block} {joint} 결속 밴드 풀림 {k}곳.", "{block} {joint} 바인딩 간격 기준 초과 약 {len}mm."],
    "CUTTING_DEFECT": ["{block} {joint} 절단면 거칠고 버(burr) 잔존 약 {len}mm.", "{block} {joint} 절단 치수 편차 {k}mm."],
}
NONWELD_NORMAL_FINDINGS = ["{block} {joint} 외관 이상 없음.", "{block} {joint} 시공 상태 양호."]

for _t in EXTRA_TYPES:
    _c = _t["code"]
    if _c in NONWELD_FINDINGS:
        FINDINGS[_c] = NONWELD_FINDINGS[_c]
        CAUSES[_c] = [x.strip() for x in _t["causes"].split(",")]
        PLANS[_c] = [_t["remedies"], f"손상부 정리 후 {_t['remedies'].split(',')[0]}"]
        RESULTS[_c] = [f"{_t['name_ko']} 보수 완료, 외관 양호", "보수 후 재점검 이상 없음"]
