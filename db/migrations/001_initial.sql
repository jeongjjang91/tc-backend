-- Phase 1 필수 테이블 (MySQL 8.0)

CREATE TABLE IF NOT EXISTS chat_sessions (
  session_id     VARCHAR(36) PRIMARY KEY,
  user_id        VARCHAR(50),
  created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_active_at TIMESTAMP NULL,
  metadata       JSON
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS chat_messages (
  message_id  BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id  VARCHAR(36),
  role        VARCHAR(10),
  content     TEXT,
  citations   JSON,
  confidence  DECIMAL(3,2),
  trace_id    VARCHAR(36),
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_msg_session (session_id, created_at),
  FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS feedback_log (
  feedback_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  message_id  BIGINT,
  user_id     VARCHAR(50),
  rating      CHAR(1),
  comment     TEXT,
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (message_id) REFERENCES chat_messages(message_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS query_log (
  query_id       BIGINT AUTO_INCREMENT PRIMARY KEY,
  question_hash  VARCHAR(64),
  question       TEXT,
  agent_used     VARCHAR(50),
  sql_generated  TEXT,
  result_summary TEXT,
  latency_ms     INT,
  cached_until   TIMESTAMP NULL,
  trace_id       VARCHAR(36),
  created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_query_hash (question_hash, cached_until)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS config_version (
  scope      VARCHAR(50) PRIMARY KEY,
  version    INT,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS few_shot_bank (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  question_skeleton TEXT,
  question_original TEXT,
  sql_text          TEXT,
  source            VARCHAR(20),
  hit_count         INT DEFAULT 0,
  success_rate      DECIMAL(3,2),
  enabled           CHAR(1) DEFAULT 'Y',
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO config_version (scope, version) VALUES ('few_shot', 1);
INSERT IGNORE INTO config_version (scope, version) VALUES ('overrides', 1);
