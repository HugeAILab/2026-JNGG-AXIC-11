-- QCheck 스키마 버전 2 — AI 파이프라인 (모델 A 선별 → 모델 B 용접 상세) (TODO.md 7.2)
-- db.py 의 마이그레이션이 버전 1 DB 에 한 번 적용한다. 칸 추가(ALTER)는 db.py 에서 처리한다.

-- AI 모델 등록부. 단계(A/B)마다 활성 모델 1개. 비밀값은 넣지 않는다
CREATE TABLE IF NOT EXISTS ai_models (
  key           TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  stage         TEXT NOT NULL CHECK (stage IN ('A','B')),
  provider      TEXT NOT NULL CHECK (provider IN ('aistream_seg','weld_api','mock')),
  endpoint_env  TEXT,
  api_key_env   TEXT,
  version       TEXT,
  enabled       INTEGER NOT NULL DEFAULT 1,
  timeout_s     INTEGER NOT NULL DEFAULT 60,
  concurrency   INTEGER NOT NULL DEFAULT 2,
  run_name      TEXT,
  updated_at    TEXT NOT NULL
);

-- 모델 A 클래스 대응표
CREATE TABLE IF NOT EXISTS a_class_map (
  a_class       TEXT PRIMARY KEY,
  source_name   TEXT,
  domain        TEXT NOT NULL CHECK (domain IN ('WELD','SURFACE','INSULATION','CABLE','BINDING','CUTTING')),
  is_defect     INTEGER NOT NULL,
  maps_to       TEXT REFERENCES defect_types(code),
  threshold     REAL NOT NULL DEFAULT 0.5,
  triggers_b    INTEGER NOT NULL DEFAULT 0,
  updated_at    TEXT NOT NULL
);

-- 모델별 판정 결과 (사진 1장 × 모델 1개 × 실행 1회 = 1행)
CREATE TABLE IF NOT EXISTS ai_model_results (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  inspection_id   INTEGER NOT NULL REFERENCES inspections(id),
  model_key       TEXT NOT NULL REFERENCES ai_models(key),
  stage           TEXT NOT NULL CHECK (stage IN ('A','B')),
  trigger         TEXT NOT NULL CHECK (trigger IN ('AUTO','MANUAL','REINSPECT','SEED')),
  status          TEXT NOT NULL CHECK (status IN ('OK','INVALID','ERROR','TIMEOUT','BUSY')),
  top_class       TEXT,
  top_code        TEXT REFERENCES defect_types(code),
  top_score       REAL,
  is_defect       INTEGER,
  candidates_json TEXT,
  class_summary   TEXT,
  raw_path        TEXT,
  image_w         INTEGER,
  image_h         INTEGER,
  scores_json     TEXT,
  keywords_json   TEXT,
  observation     TEXT,
  location        TEXT,
  image_quality   TEXT CHECK (image_quality IN ('OK','BLUR','DARK','NOT_WELD')),
  raw             TEXT,
  model_version   TEXT,
  elapsed_ms      INTEGER,
  error           TEXT,
  created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mres_insp ON ai_model_results(inspection_id, stage, id);

-- AI 불량 분석 = 불량 설명·원인·방지 (버전 1 의 ai_analyses 를 대체)
CREATE TABLE IF NOT EXISTS ai_analyses (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  inspection_id   INTEGER NOT NULL REFERENCES inspections(id),
  trigger         TEXT NOT NULL CHECK (trigger IN ('AUTO','MANUAL','REINSPECT','SEED')),
  status          TEXT NOT NULL DEFAULT 'RUNNING'
                  CHECK (status IN ('RUNNING','DONE','PARTIAL','ERROR')),
  path            TEXT NOT NULL CHECK (path IN ('WELD_B_RAG','A_GUIDE')),
  model_result_ids TEXT,
  keywords_json   TEXT,
  rag_source      TEXT CHECK (rag_source IN ('AGENTSTREAM','LOCAL','NONE')),
  rag_query       TEXT,
  rag_hits_json   TEXT,
  explain_source  TEXT CHECK (explain_source IN ('LLM','TEMPLATE')),
  description     TEXT,
  causes_json     TEXT,
  prevention_json TEXT,
  checks_json     TEXT,
  elapsed_ms      INTEGER,
  error           TEXT,
  created_at      TEXT NOT NULL,
  finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_ana_insp ON ai_analyses(inspection_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_rag_case ON rag_chunks(inspection_id) WHERE source = 'CASE';
CREATE INDEX IF NOT EXISTS idx_insp_domain ON inspections(ai_domain);
