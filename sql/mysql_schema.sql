-- 商客对话监测 · MySQL 8.0 建表脚本
-- 由当前 SQLite schema 直接翻译。时间字段保持 VARCHAR(32)，便于应用代码零改动迁移；
-- 若要做时间范围查询，建议后续改成 DATETIME 并加索引。

CREATE DATABASE IF NOT EXISTS sentinel
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE sentinel;

CREATE TABLE IF NOT EXISTS judge_logs (
  id           BIGINT       NOT NULL AUTO_INCREMENT,
  ts           VARCHAR(32)  NOT NULL,
  source       VARCHAR(16)  DEFAULT NULL COMMENT 'manual / random / batch / human',
  text         TEXT         NOT NULL,
  truth_type   VARCHAR(32)  DEFAULT NULL,
  rule_hit     TINYINT      DEFAULT NULL,
  rule_type    VARCHAR(32)  DEFAULT NULL,
  rule_score   DOUBLE       DEFAULT NULL,
  llm_hit      TINYINT      DEFAULT NULL,
  llm_type     VARCHAR(32)  DEFAULT NULL,
  llm_conf     DOUBLE       DEFAULT NULL,
  agree        TINYINT      DEFAULT NULL,
  covered      TINYINT      DEFAULT NULL,
  tier         VARCHAR(16)  DEFAULT NULL COMMENT 'rule / small / large / human',
  cost_units   DOUBLE       DEFAULT NULL,
  route_reason VARCHAR(500) DEFAULT NULL,
  lat_ms       INT DEFAULT NULL,
  trace_id     VARCHAR(64) DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_source (source),
  KEY idx_ts (ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS review_queue (
  id           BIGINT       NOT NULL AUTO_INCREMENT,
  ts           VARCHAR(32)  NOT NULL,
  text         TEXT         NOT NULL,
  truth_type   VARCHAR(32)  DEFAULT '',
  rule_hit     TINYINT      DEFAULT NULL,
  rule_type    VARCHAR(32)  DEFAULT NULL,
  tier         VARCHAR(16)  DEFAULT NULL,
  model_type   VARCHAR(32)  DEFAULT NULL,
  model_conf   DOUBLE       DEFAULT NULL,
  reason       VARCHAR(255) DEFAULT NULL,
  status       VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT 'pending / approved / rejected',
  reviewer     VARCHAR(32)  DEFAULT '',
  decided_at   VARCHAR(32)  DEFAULT '',
  note         VARCHAR(255) DEFAULT '',
  session_id   VARCHAR(64)  DEFAULT NULL,
  user_id      VARCHAR(64)  DEFAULT NULL,
  case_no      VARCHAR(32)  DEFAULT NULL,
  preset       VARCHAR(16)  DEFAULT NULL,
  escalated    TINYINT      DEFAULT NULL,
  trace_id     VARCHAR(64)  DEFAULT '',
  PRIMARY KEY (id),
  KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS cases (
  id         BIGINT       NOT NULL AUTO_INCREMENT,
  case_no    VARCHAR(32)  NOT NULL,
  ts         VARCHAR(32)  NOT NULL,
  review_id  BIGINT       DEFAULT NULL,
  text       TEXT         NOT NULL,
  status     VARCHAR(16)  NOT NULL DEFAULT 'accepted',
  evidence   VARCHAR(255) DEFAULT '',
  merchant   VARCHAR(64)  DEFAULT '',
  user_id    VARCHAR(64)  DEFAULT '',
  PRIMARY KEY (id),
  UNIQUE KEY uk_case_no (case_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS notifications (
  id       BIGINT      NOT NULL AUTO_INCREMENT,
  ts       VARCHAR(32) NOT NULL,
  user_id  VARCHAR(64) NOT NULL,
  title    VARCHAR(64) NOT NULL,
  body     TEXT,
  case_no  VARCHAR(32) DEFAULT '',
  `read`   TINYINT     NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS merchants (
  id         VARCHAR(32) NOT NULL,
  name       VARCHAR(64) NOT NULL,
  violations INT         NOT NULL DEFAULT 0,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS rule_candidates (
  id     BIGINT       NOT NULL AUTO_INCREMENT,
  ts     VARCHAR(32)  NOT NULL,
  text   TEXT         NOT NULL,
  kind   VARCHAR(16)  NOT NULL COMMENT 'positive / negative',
  hits   INT          NOT NULL DEFAULT 1,
  status VARCHAR(16)  NOT NULL DEFAULT 'open',
  PRIMARY KEY (id),
  KEY idx_kind_status (kind, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS feedback (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  scenario_id VARCHAR(32)  NOT NULL,
  turn        INT          NOT NULL,
  rule_id     VARCHAR(16)  DEFAULT NULL,
  type        VARCHAR(32)  DEFAULT NULL,
  useful      TINYINT      NOT NULL,
  note        VARCHAR(255) DEFAULT '',
  created_at  VARCHAR(32)  NOT NULL,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS eval_runs (
  id              BIGINT      NOT NULL AUTO_INCREMENT,
  ts              VARCHAR(32) NOT NULL,
  dev_recall      DOUBLE,
  holdout_recall  DOUBLE,
  holdout2_recall DOUBLE,
  holdout3_recall DOUBLE,
  false_positive  DOUBLE,
  type_accuracy   DOUBLE,
  passed          TINYINT,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS events (
  id          BIGINT      NOT NULL AUTO_INCREMENT,
  ts          VARCHAR(32) NOT NULL,
  event       VARCHAR(64) NOT NULL,
  schema_ver  SMALLINT    NOT NULL DEFAULT 1,
  trace_id    VARCHAR(64) NOT NULL,
  session_id  VARCHAR(64) DEFAULT '',
  actor_id    VARCHAR(64) DEFAULT '',
  properties  TEXT        NOT NULL,
  PRIMARY KEY (id),
  KEY idx_events_trace (trace_id),
  KEY idx_events_name_ts (event, ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO merchants(id, name, violations) VALUES ('m-2001', '小家电旗舰店', 12);
