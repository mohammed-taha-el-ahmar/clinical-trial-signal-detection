-- ============================================================
-- PharmaSight — Stream Analytics Signal Detection Query
-- ============================================================
-- Input:  adverse-events   (Event Hub, JSON)
-- Output: signal-alerts    (Event Hub → Synapse via ADF)
--         raw-events       (ADLS Gen2 — bronze landing)
--
-- Detection logic mirrors agent/detector.py (local fallback).
-- Note: Incidence rate is computed downstream in Synapse/API.
-- ASA emits candidate signals based on count thresholds.
-- ============================================================

-- 1. Land all raw events to ADLS Gen2 bronze layer
SELECT
    event_id,
    trial_id,
    site_id,
    patient_id,
    arm,
    symptom_code,
    symptom_label,
    severity,
    reported_at,
    age_group,
    is_serious,
    System.Timestamp() AS ingested_at
INTO [raw-events]
FROM [adverse-events]
TIMESTAMP BY reported_at;


-- 2. Detect potential safety signals using a 10-minute tumbling window.
--    Groups by (trial, arm, symptom) and emits when event count breaches
--    the minimum threshold. Full incidence rate computation happens in
--    the Synapse gold layer for auditability.
SELECT
    CONCAT(trial_id, '-', arm, '-', symptom_code, '-',
           CAST(System.Timestamp() AS nvarchar(max))) AS signal_id,
    DATEADD(minute, -10, System.Timestamp()) AS window_start,
    System.Timestamp()   AS window_end,
    trial_id,
    arm,
    symptom_code,
    symptom_label,
    COUNT(*)             AS event_count,
    SUM(CASE WHEN severity = 'severe' THEN 1 ELSE 0 END) AS severe_count,
    SUM(CASE WHEN severity = 'life_threatening' THEN 1 ELSE 0 END) AS life_threatening_count,
    CASE
        WHEN COUNT(*) >= 10 THEN 'high'
        WHEN COUNT(*) >= 5  THEN 'medium'
        ELSE 'low'
    END AS confidence,
    System.Timestamp()   AS detected_at
INTO [signal-alerts]
FROM [adverse-events]
TIMESTAMP BY reported_at
GROUP BY
    trial_id,
    arm,
    symptom_code,
    symptom_label,
    TumblingWindow(minute, 10)
HAVING
    COUNT(*) >= 3;
