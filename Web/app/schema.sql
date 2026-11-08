-- QCheck SQLite 스키마 (TODO.md 7.2와 같은 내용. 고칠 때는 두 곳을 함께 고친다)
-- 앱 시작 시마다 실행되므로 모든 문장은 IF NOT EXISTS 로 둔다.

-- 사용자 (데모 계정)
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,           -- pbkdf2_sha256$반복수$salt$hash
  name          TEXT NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('admin','manager','inspector','worker')),
  created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 결함 유형 코드 사전 (AI 모델 클래스와 화면 표시명 매핑)
CREATE TABLE IF NOT EXISTS defect_types (
  code        TEXT PRIMARY KEY,          -- 예: POROSITY
  name_ko     TEXT NOT NULL,             -- 기공
  name_en     TEXT NOT NULL,             -- Porosity
  model_label TEXT,                      -- AIStream 모델이 반환하는 원래 라벨 (당일 /docs 확인 후 채움)
  description TEXT NOT NULL,             -- 외관 특징
  causes      TEXT NOT NULL,             -- 주요 원인
  remedies    TEXT NOT NULL,             -- 대책·보수 방법
  keywords    TEXT NOT NULL DEFAULT '[]', -- 대표 키워드 JSON (규칙 기반 키워드·BM25·RAG 질의에 사용)
  severity    TEXT NOT NULL CHECK (severity IN ('NONE','LOW','MEDIUM','HIGH','CRITICAL'))  -- NORMAL은 NONE
);

-- 활동 로그 (추가 전용). 업로드 묶음도 UPLOAD 로그 1행으로 표현
CREATE TABLE IF NOT EXISTS activity_logs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  log_type    TEXT NOT NULL CHECK (log_type IN ('AUTH','UPLOAD','AI','REPORT','QUIZ','SYSTEM')),
  action      TEXT NOT NULL,              -- LOGIN, LOGIN_FAIL, LOGOUT, UPLOAD_PHOTOS, AI_PREDICT, AI_ERROR,
                                          -- LLM_CALL, LLM_ERROR, VLM_CALL, RAG_CALL, ANALYSIS_DONE,
                                          -- MODEL_SWITCH, RUN_IMPORT, REPORT_CREATE, QUIZ_FINISH, SEED ...
  level       TEXT NOT NULL DEFAULT 'INFO' CHECK (level IN ('INFO','WARN','ERROR')),
  user_id     INTEGER REFERENCES users(id),
  target_type TEXT,                       -- inspection / report / quiz_session / analysis / model_run
  target_id   INTEGER,
  summary     TEXT NOT NULL,              -- 목록에 보이는 한 줄: '사진 12장 업로드 · TANK-2 / B-07'
  detail_json TEXT,                       -- UPLOAD: {"photo_count":12,"files":[...],"common":{...}}
                                          -- AI_PREDICT: {"inspection_id":..,"elapsed_ms":..,"label":..}
  ip          TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_log_type_time ON activity_logs(log_type, created_at);
CREATE INDEX IF NOT EXISTS idx_log_user      ON activity_logs(user_id);

-- 검사 건 = 결함 관리 단위 (사진 1장 = 검사 1건)
CREATE TABLE IF NOT EXISTS inspections (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  code            TEXT NOT NULL UNIQUE,   -- INS-20261107-0001
  upload_log_id   INTEGER REFERENCES activity_logs(id),  -- 어느 업로드로 들어왔는지 (시드 데이터는 NULL 가능)
  project         TEXT NOT NULL,          -- 예: LNG-PJT-A (가상)
  tank_no         TEXT NOT NULL,          -- TANK-1 ~ TANK-4
  block           TEXT NOT NULL,          -- 예: B-12 (가상 블록)
  joint_no        TEXT NOT NULL,          -- 이음부 번호 J-0001
  weld_process    TEXT NOT NULL,          -- SMAW/GTAW/GMAW/FCAW/SAW
  weld_position   TEXT,                   -- 1G~4G
  welder          TEXT,                   -- 가명 (용접사 W-07)
  inspector_id    INTEGER REFERENCES users(id),
  image_path      TEXT,                   -- data/uploads 기준 상대 경로. 비어 있으면 자리표시 이미지
  is_sample       INTEGER NOT NULL DEFAULT 0, -- 1 = 시드 합성 데이터 (목록·상세에 '샘플' 배지)
  -- AI 판정
  ai_label        TEXT,                   -- 모델 원래 라벨
  ai_is_defect    INTEGER,                -- 1/0
  ai_defect_type  TEXT REFERENCES defect_types(code),
  ai_confidence   REAL,                   -- 0~1
  ai_raw          TEXT,                   -- 응답 원문 JSON
  ai_at           TEXT,
  model_key       TEXT,                   -- 판정에 쓴 모델: baseline / improved / mock
  model_version   TEXT,                   -- 서빙 ID 또는 학습 실행 이름(run_name)
  ai_status       TEXT NOT NULL DEFAULT 'NONE'
                  CHECK (ai_status IN ('NONE','QUEUED','RUNNING','DONE','ERROR')),
                                          -- 판정 작업 상태. 업무 상태(status)와 분리: 실패 건과 '아직 안 함'을 구분,
                                          -- 재시작 시 QUEUED·RUNNING만 다시 넣는다(ERROR는 수동 재판정)
  ai_error        TEXT,                   -- 마지막 판정 실패 사유 (화면 'AI 미연결' 배지 문구)
  -- 검사자 확정
  final_is_defect INTEGER,
  defect_type     TEXT REFERENCES defect_types(code),
  severity        TEXT,
  finding         TEXT,                   -- 검사 소견 (BM25 검색 대상)
  ai_agree        INTEGER,                -- AI와 검사자 판정 일치 여부
  -- 조치·재검사
  cause           TEXT,                   -- 추정 원인
  action_plan     TEXT,                   -- 조치 계획
  action_result   TEXT,                   -- 조치 결과
  assignee        TEXT,                   -- 조치 담당
  due_date        TEXT,
  rework_count    INTEGER NOT NULL DEFAULT 0,
  status          TEXT NOT NULL DEFAULT 'PENDING'
                  CHECK (status IN ('PENDING','AI_DONE','DEFECT','REWORK','REINSPECT','PASS','REJECT')),
  inspected_at    TEXT NOT NULL,          -- 최초 검사 일시
  closed_at       TEXT,                   -- PASS/REJECT 일시
  updated_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_insp_status ON inspections(status);
CREATE INDEX IF NOT EXISTS idx_insp_date   ON inspections(inspected_at);
CREATE INDEX IF NOT EXISTS idx_insp_type   ON inspections(defect_type);
CREATE INDEX IF NOT EXISTS idx_insp_upload ON inspections(upload_log_id);

-- 처리 이력 (상태 전이마다 1행)
CREATE TABLE IF NOT EXISTS inspection_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  inspection_id INTEGER NOT NULL REFERENCES inspections(id),
  action        TEXT NOT NULL,            -- AI_INSPECT, CONFIRM_DEFECT, REWORK_PLAN ...
  from_status   TEXT,
  to_status     TEXT NOT NULL,
  comment       TEXT,
  image_path    TEXT,                     -- 수정 후·재검사 사진
  ai_snapshot   TEXT,                     -- 재검사 시 AI 결과 JSON
  actor_id      INTEGER REFERENCES users(id),
  created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_evt_insp ON inspection_events(inspection_id);

-- 보고서 헤더 (여러 결함을 묶는 단위). 생성 즉시 확정, 수정 없음
CREATE TABLE IF NOT EXISTS reports (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  report_no     TEXT NOT NULL UNIQUE,     -- DR-20261107-001 (결함) / CR-20261107-001 (완료)
  type          TEXT NOT NULL CHECK (type IN ('DEFECT','COMPLETION')),
  title         TEXT NOT NULL,
  source        TEXT NOT NULL CHECK (source IN ('UPLOAD','SELECTION')),
  upload_log_id INTEGER REFERENCES activity_logs(id),   -- source=UPLOAD 일 때 해당 업로드 로그
  item_count    INTEGER NOT NULL,
  top_defect_type TEXT REFERENCES defect_types(code),   -- 최다 공통 유형 (목록 표시용)
  analysis_json TEXT NOT NULL,            -- 재발·원인 분석 수치: 유형 분포, 공통 속성, 재발 건수, 유사 사례 id 등
  analysis_text TEXT NOT NULL,            -- 재발·원인 분석 요약 문단 (LLM 또는 규칙 기반)
  llm_used      INTEGER NOT NULL DEFAULT 0,
  body_html     TEXT NOT NULL,            -- 생성 시점 렌더링 결과 스냅샷
  created_by    INTEGER REFERENCES users(id),
  created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_report_type ON reports(type, created_at);

-- 보고서 ↔ 결함 연결 (N:M). 결함 하나가 결함 보고서·완료 보고서 등 여러 보고서에 들어갈 수 있음
CREATE TABLE IF NOT EXISTS report_items (
  report_id     INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
  inspection_id INTEGER NOT NULL REFERENCES inspections(id),
  sort_order    INTEGER NOT NULL DEFAULT 0,
  snapshot_json TEXT NOT NULL,            -- 포함 시점의 결함 정보 (완료 보고서는 조치·재검사 정보까지) — 이후 결함이 바뀌어도 보고서는 유지
  PRIMARY KEY (report_id, inspection_id)
);
CREATE INDEX IF NOT EXISTS idx_ritem_insp ON report_items(inspection_id);

-- 교육 과정 (대주제)
CREATE TABLE IF NOT EXISTS edu_classes (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  slug        TEXT NOT NULL UNIQUE,       -- welding-basics
  title       TEXT NOT NULL,              -- 용접 기초
  description TEXT NOT NULL,
  level       TEXT NOT NULL DEFAULT '기초' CHECK (level IN ('기초','중급','실무')),
  icon        TEXT,                       -- 화면 아이콘 이름
  sort_order  INTEGER NOT NULL DEFAULT 0
);

-- 교육 자료 (과정 안의 문서)
CREATE TABLE IF NOT EXISTS edu_articles (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  class_id   INTEGER NOT NULL REFERENCES edu_classes(id),
  slug       TEXT NOT NULL UNIQUE,        -- defect-causes
  title      TEXT NOT NULL,
  summary    TEXT NOT NULL,
  body_md    TEXT NOT NULL,               -- 마크다운 → 간단 변환 후 표시
  related_defect_types TEXT,              -- 이 자료가 다루는 결함 코드 목록 JSON (상세 화면에서 '관련 교육 자료' 연결용)
  sort_order INTEGER NOT NULL DEFAULT 0
);

-- 퀴즈 (교육 자료에 속함). 불량 정보는 참조로만 사용
CREATE TABLE IF NOT EXISTS edu_quizzes (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  article_id        INTEGER NOT NULL REFERENCES edu_articles(id),
  quiz_type         TEXT NOT NULL CHECK (quiz_type IN ('DEFECT_ID','CONCEPT')),
                                          -- DEFECT_ID: 불량 사례 보고 결함 맞추기 / CONCEPT: 개념 문제
  question          TEXT NOT NULL,        -- 출제 문장 (DEFECT_ID는 참조 결함의 소견에서 유형명을 가린 문장)
  ref_inspection_id INTEGER REFERENCES inspections(id),   -- 참조한 불량 사례 (사진·소견 출처)
  ref_defect_type   TEXT REFERENCES defect_types(code),   -- 참조한 결함 유형 (해설 출처)
  image_path        TEXT,                 -- 비어 있으면 참조 결함의 사진 → 그것도 없으면 텍스트 문제
  choices_json      TEXT NOT NULL,        -- [{"key":"POROSITY","label":"기공"}, ...] 4개
  answer_key        TEXT NOT NULL,        -- 정답 key
  explanation       TEXT NOT NULL,
  difficulty        TEXT NOT NULL DEFAULT 'NORMAL' CHECK (difficulty IN ('EASY','NORMAL','HARD')),
  sort_order        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_quiz_article ON edu_quizzes(article_id);

-- 퀴즈 풀이 기록
CREATE TABLE IF NOT EXISTS quiz_attempts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL REFERENCES users(id),
  quiz_id     INTEGER NOT NULL REFERENCES edu_quizzes(id),
  session_key TEXT NOT NULL,              -- 한 번 풀이 세트 묶음
  chosen_key  TEXT NOT NULL,
  is_correct  INTEGER NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_attempt_user ON quiz_attempts(user_id, session_key);

-- AI 불량 분석 (키워드 추출 → RAG → 불량 설명). 다시 분석하면 행을 추가하고 화면에는 최신 행을 표시
CREATE TABLE IF NOT EXISTS ai_analyses (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  inspection_id   INTEGER NOT NULL REFERENCES inspections(id),
  trigger         TEXT NOT NULL CHECK (trigger IN ('AUTO','MANUAL','REINSPECT','SEED')),
  status          TEXT NOT NULL DEFAULT 'RUNNING'
                  CHECK (status IN ('RUNNING','DONE','PARTIAL','ERROR')),  -- PARTIAL = 일부 단계가 대체 동작
  -- ① 키워드 추출
  kw_source       TEXT CHECK (kw_source IN ('VLM','RULE')),     -- RULE = defect_types.keywords 로 대체
  kw_defect_types TEXT,                    -- 사전 코드 JSON. 예: ["POROSITY"]
  keywords_json   TEXT,                    -- 예: ["기공 군집","비드 중앙부","둥근 구멍"]
  observation     TEXT,                    -- 관찰 요약 한 줄 (위치·형태·크기)
  image_quality   TEXT CHECK (image_quality IN ('OK','BLUR','DARK','NOT_WELD')),
  kw_agree        INTEGER,                 -- 판정 모델 유형과 키워드 유형의 일치 여부
  -- ② RAG 근거 검색
  rag_source      TEXT CHECK (rag_source IN ('AGENTSTREAM','LOCAL','NONE')),
  rag_query       TEXT,
  rag_hits_json   TEXT,                    -- [{"no":1,"doc":"용접 결함 해설서","section":"2. 기공","text":"...","score":3.2}]
  -- ③ 불량 설명
  explain_source  TEXT CHECK (explain_source IN ('LLM','TEMPLATE')),
  explanation     TEXT,                    -- 3~5문장, 문장 끝에 근거 번호 [1][2]
  recommendations TEXT,                    -- 권고 조치·확인 검사 JSON 목록 (3개 이내)
  elapsed_ms      INTEGER,
  error           TEXT,
  created_at      TEXT NOT NULL,
  finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_ana_insp ON ai_analyses(inspection_id, id);

-- RAG 대체 경로용 원천 문서 조각 (seed/rag_docs/*.md 를 절 단위로 분할)
CREATE TABLE IF NOT EXISTS rag_chunks (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_title   TEXT NOT NULL,              -- 용접 결함 해설서
  section     TEXT NOT NULL,              -- 2. 기공 > 2.3 원인
  defect_type TEXT REFERENCES defect_types(code),
  text        TEXT NOT NULL
);

-- 학습 작업 로그 (베이스라인·개선 실험). 학습 코드가 남긴 JSON·CSV를 tools/import_runs.py로 가져옴
CREATE TABLE IF NOT EXISTS model_runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_name      TEXT NOT NULL UNIQUE,      -- 예: baseline-v1, improved-aug-v2
  model_key     TEXT NOT NULL CHECK (model_key IN ('baseline','improved')),
  base_model    TEXT,                      -- 출발 모델·구조
  changes       TEXT,                      -- 베이스라인 대비 바꾼 점 (증강, 클래스 가중치, 해상도 등)
  data_split    TEXT,                      -- 학습/검증/시험 건수와 분할 방법
  status        TEXT NOT NULL CHECK (status IN ('RUNNING','DONE','STOPPED','FAILED')),
  epochs_done   INTEGER,
  epochs_plan   INTEGER,
  metrics_json  TEXT,                      -- 마지막 지표: accuracy, macro_f1, defect_recall, 클래스별 recall
  history_json  TEXT,                      -- 에폭별 loss·지표 (차트용)
  notes         TEXT,                      -- 작업 메모, 중단·실패 원인
  started_at    TEXT,
  ended_at      TEXT,
  imported_at   TEXT NOT NULL
);

-- 실행 중 바꾸는 설정 (재시작해도 유지). 예: detector_active = 'improved'
-- .env 값은 기본값이고, 이 표에 값이 있으면 이 표가 우선한다
CREATE TABLE IF NOT EXISTS app_settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_by INTEGER REFERENCES users(id),
  updated_at TEXT NOT NULL
);
