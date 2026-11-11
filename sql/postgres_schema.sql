-- 商客对话监测 · PostgreSQL 14+ 建表脚本
-- 由当前 SQLite schema 翻译。时间字段保持 TEXT，便于应用零改动迁移。

CREATE TABLE IF NOT EXISTS judge_logs (
  id           BIGSERIAL    PRIMARY KEY,
  ts           VARCHAR(32)  NOT NULL,
  source       VARCHAR(16),
  "text"       TEXT         NOT NULL,
  truth_type   VARCHAR(32),
  rule_hit     SMALLINT,
  rule_type    VARCHAR(32),
  rule_score   DOUBLE PRECISION,
  llm_hit      SMALLINT,
  llm_type     VARCHAR(32),
  llm_conf     DOUBLE PRECISION,
  agree        SMALLINT,
  covered      SMALLINT,
  tier         VARCHAR(16),
  cost_units   DOUBLE PRECISION,
  route_reason VARCHAR(500),
  lat_ms       INTEGER,
  trace_id     VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS idx_judge_source ON judge_logs(source);
CREATE INDEX IF NOT EXISTS idx_judge_ts ON judge_logs(ts);

CREATE TABLE IF NOT EXISTS review_queue (
  id           BIGSERIAL    PRIMARY KEY,
  ts           VARCHAR(32)  NOT NULL,
  "text"       TEXT         NOT NULL,
  truth_type   VARCHAR(32)  DEFAULT '',
  rule_hit     SMALLINT,
  rule_type    VARCHAR(32),
  tier         VARCHAR(16),
  model_type   VARCHAR(32),
  model_conf   DOUBLE PRECISION,
  reason       VARCHAR(255),
  status       VARCHAR(16)  NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','approved','rejected')),
  reviewer     VARCHAR(32)  DEFAULT '',
  decided_at   VARCHAR(32)  DEFAULT '',
  note         VARCHAR(255) DEFAULT '',
  session_id   VARCHAR(64),
  user_id      VARCHAR(64),
  case_no      VARCHAR(32),
  preset       VARCHAR(16),
  escalated    SMALLINT,
  trace_id     VARCHAR(64) DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue(status);

CREATE TABLE IF NOT EXISTS cases (
  id         BIGSERIAL    PRIMARY KEY,
  case_no    VARCHAR(32)  NOT NULL UNIQUE,
  ts         VARCHAR(32)  NOT NULL,
  review_id  BIGINT,
  "text"     TEXT         NOT NULL,
  status     VARCHAR(16)  NOT NULL DEFAULT 'accepted',
  evidence   VARCHAR(255) DEFAULT '',
  merchant   VARCHAR(64)  DEFAULT '',
  user_id    VARCHAR(64)  DEFAULT ''
);

CREATE TABLE IF NOT EXISTS notifications (
  id       BIGSERIAL    PRIMARY KEY,
  ts       VARCHAR(32)  NOT NULL,
  user_id  VARCHAR(64)  NOT NULL,
  title    VARCHAR(64)  NOT NULL,
  body     TEXT,
  case_no  VARCHAR(32)  DEFAULT '',
  is_read  SMALLINT     NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id);

CREATE TABLE IF NOT EXISTS merchants (
  id         VARCHAR(32) PRIMARY KEY,
  name       VARCHAR(64) NOT NULL,
  violations INT         NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rule_candidates (
  id     BIGSERIAL    PRIMARY KEY,
  ts     VARCHAR(32)  NOT NULL,
  "text" TEXT         NOT NULL,
  kind   VARCHAR(16)  NOT NULL,
  hits   INT          NOT NULL DEFAULT 1,
  status VARCHAR(16)  NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS idx_cand ON rule_candidates(kind, status);

CREATE TABLE IF NOT EXISTS feedback (
  id          BIGSERIAL    PRIMARY KEY,
  scenario_id VARCHAR(32)  NOT NULL,
  turn        INT          NOT NULL,
  rule_id     VARCHAR(16),
  type        VARCHAR(32),
  useful      SMALLINT     NOT NULL,
  note        VARCHAR(255) DEFAULT '',
  created_at  VARCHAR(32)  NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_runs (
  id              BIGSERIAL PRIMARY KEY,
  ts              VARCHAR(32) NOT NULL,
  dev_recall      DOUBLE PRECISION,
  holdout_recall  DOUBLE PRECISION,
  holdout2_recall DOUBLE PRECISION,
  holdout3_recall DOUBLE PRECISION,
  false_positive  DOUBLE PRECISION,
  type_accuracy   DOUBLE PRECISION,
  passed          SMALLINT
);

CREATE TABLE IF NOT EXISTS events (
  id          BIGSERIAL PRIMARY KEY,
  ts          VARCHAR(32) NOT NULL,
  event       VARCHAR(64) NOT NULL,
  schema_ver  SMALLINT NOT NULL DEFAULT 1,
  trace_id    VARCHAR(64) NOT NULL,
  session_id  VARCHAR(64) DEFAULT '',
  actor_id    VARCHAR(64) DEFAULT '',
  properties  TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id);
CREATE INDEX IF NOT EXISTS idx_events_name_ts ON events(event, ts);

INSERT INTO merchants(id, name, violations) VALUES ('m-2001', '小家电旗舰店', 12)
  ON CONFLICT (id) DO NOTHING;
