-- Phase 1 필수 테이블

CREATE TABLE chat_sessions (
  session_id     VARCHAR2(36) PRIMARY KEY,
  user_id        VARCHAR2(50),
  created_at     TIMESTAMP DEFAULT SYSTIMESTAMP,
  last_active_at TIMESTAMP,
  metadata       CLOB CHECK (metadata IS JSON)
);

CREATE TABLE chat_messages (
  message_id     NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id     VARCHAR2(36) REFERENCES chat_sessions(session_id),
  role           VARCHAR2(10),
  content        CLOB,
  citations      CLOB CHECK (citations IS JSON),
  confidence     NUMBER(3,2),
  trace_id       VARCHAR2(36),
  created_at     TIMESTAMP DEFAULT SYSTIMESTAMP
);
CREATE INDEX idx_msg_session ON chat_messages(session_id, created_at);

CREATE TABLE feedback_log (
  feedback_id    NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  message_id     NUMBER REFERENCES chat_messages(message_id),
  user_id        VARCHAR2(50),
  rating         CHAR(1),
  comment        CLOB,
  created_at     TIMESTAMP DEFAULT SYSTIMESTAMP
);

CREATE TABLE query_log (
  query_id       NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  question_hash  VARCHAR2(64),
  question       CLOB,
  agent_used     VARCHAR2(50),
  sql_generated  CLOB,
  result_summary CLOB,
  latency_ms     NUMBER,
  cached_until   TIMESTAMP,
  trace_id       VARCHAR2(36),
  created_at     TIMESTAMP DEFAULT SYSTIMESTAMP
);
CREATE INDEX idx_query_hash ON query_log(question_hash, cached_until);

CREATE TABLE config_version (
  scope          VARCHAR2(50) PRIMARY KEY,
  version        NUMBER,
  updated_at     TIMESTAMP DEFAULT SYSTIMESTAMP
);

CREATE TABLE few_shot_bank (
  id                 NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  question_skeleton  CLOB,
  question_original  CLOB,
  sql_text           CLOB,
  source             VARCHAR2(20),
  hit_count          NUMBER DEFAULT 0,
  success_rate       NUMBER(3,2),
  enabled            CHAR(1) DEFAULT 'Y',
  created_at         TIMESTAMP DEFAULT SYSTIMESTAMP
);

INSERT INTO config_version (scope, version) VALUES ('few_shot', 1);
INSERT INTO config_version (scope, version) VALUES ('overrides', 1);
COMMIT;
