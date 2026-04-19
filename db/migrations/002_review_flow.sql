-- Phase 3: 검토 흐름 (semi-automatic review)
CREATE TABLE IF NOT EXISTS pending_reviews (
    review_id    BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id   VARCHAR(36)      NOT NULL,
    trace_id     VARCHAR(36)      NOT NULL,
    question     TEXT             NOT NULL,
    draft_answer TEXT             NOT NULL,
    log_context  JSON,
    confidence   DECIMAL(4,3)     DEFAULT 0.000,
    status       VARCHAR(20)      NOT NULL DEFAULT 'pending',  -- pending / approved / rejected / edited
    reviewer_id  VARCHAR(50),
    final_answer TEXT,
    reviewed_at  DATETIME,
    created_at   DATETIME         DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_pr_status  (status, created_at),
    INDEX idx_pr_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
