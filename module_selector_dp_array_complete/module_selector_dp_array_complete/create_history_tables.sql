USE module_db;

CREATE TABLE IF NOT EXISTS selection_history (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    requirement_name VARCHAR(200) NOT NULL,

    req_ai INT UNSIGNED NOT NULL,
    req_ao INT UNSIGNED NOT NULL,
    req_di INT UNSIGNED NOT NULL,
    req_do INT UNSIGNED NOT NULL,

    actual_ai INT UNSIGNED NOT NULL,
    actual_ao INT UNSIGNED NOT NULL,
    actual_di INT UNSIGNED NOT NULL,
    actual_do INT UNSIGNED NOT NULL,

    raw_dio INT UNSIGNED NOT NULL DEFAULT 0,
    dio_to_di INT UNSIGNED NOT NULL DEFAULT 0,
    dio_to_do INT UNSIGNED NOT NULL DEFAULT 0,
    dio_spare INT UNSIGNED NOT NULL DEFAULT 0,

    analog_price DECIMAL(14, 2) NOT NULL,
    digital_price DECIMAL(14, 2) NOT NULL,
    total_price DECIMAL(14, 2) NOT NULL,
    module_count INT UNSIGNED NOT NULL,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    INDEX idx_requirement_name (requirement_name),
    INDEX idx_requirement_vector (req_ai, req_ao, req_di, req_do),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS selection_history_module (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    history_id BIGINT UNSIGNED NOT NULL,
    module_id INT NOT NULL,

    category VARCHAR(50) NOT NULL,
    module_name VARCHAR(100) NOT NULL,
    module_cname VARCHAR(200) NOT NULL,
    quantity INT UNSIGNED NOT NULL,

    unit_price DECIMAL(14, 2) NOT NULL,
    subtotal DECIMAL(14, 2) NOT NULL,

    ai_count INT UNSIGNED NOT NULL DEFAULT 0,
    ao_count INT UNSIGNED NOT NULL DEFAULT 0,
    fixed_di INT UNSIGNED NOT NULL DEFAULT 0,
    fixed_do INT UNSIGNED NOT NULL DEFAULT 0,
    raw_dio INT UNSIGNED NOT NULL DEFAULT 0,
    dio_to_di INT UNSIGNED NOT NULL DEFAULT 0,
    dio_to_do INT UNSIGNED NOT NULL DEFAULT 0,
    dio_spare INT UNSIGNED NOT NULL DEFAULT 0,
    effective_di INT UNSIGNED NOT NULL DEFAULT 0,
    effective_do INT UNSIGNED NOT NULL DEFAULT 0,

    PRIMARY KEY (id),
    INDEX idx_history_id (history_id),
    INDEX idx_module_name (module_name),

    CONSTRAINT fk_selection_history_module
        FOREIGN KEY (history_id)
        REFERENCES selection_history (id)
        ON DELETE CASCADE
        ON UPDATE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


CREATE OR REPLACE VIEW selection_history_overview AS
SELECT
    id,
    requirement_name AS name,
    req_ai,
    req_ao,
    req_di,
    req_do,
    actual_ai,
    actual_ao,
    actual_di,
    actual_do,
    total_price,
    module_count,
    created_at
FROM selection_history;


CREATE OR REPLACE VIEW selection_history_module_overview AS
SELECT
    h.id AS history_id,
    h.requirement_name,
    m.category,
    m.module_name,
    m.module_cname,
    m.quantity,
    m.unit_price,
    m.subtotal,
    m.ai_count,
    m.ao_count,
    m.fixed_di,
    m.fixed_do,
    m.raw_dio,
    m.dio_to_di,
    m.dio_to_do,
    m.dio_spare,
    m.effective_di,
    m.effective_do
FROM selection_history AS h
INNER JOIN selection_history_module AS m
    ON h.id = m.history_id;
