-- Phase 4: Knowledge Agent 저장소
CREATE TABLE IF NOT EXISTS knowledge_items (
    item_id      BIGINT AUTO_INCREMENT PRIMARY KEY,
    category     VARCHAR(50)  NOT NULL,
    title        VARCHAR(200) NOT NULL,
    content      TEXT         NOT NULL,
    keywords     JSON,
    source       VARCHAR(200),
    created_by   VARCHAR(50),
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_active    TINYINT(1) DEFAULT 1,
    FULLTEXT INDEX ft_knowledge (title, content),
    INDEX idx_kn_category (category, is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
