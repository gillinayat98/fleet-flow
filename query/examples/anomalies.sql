-- The consolidated anomaly feed produced by the detector Lambda.
-- Reads the queryable table the crawler built from s3://.../anomalies/.
SELECT
    category,
    anomaly_type,
    entity,
    detail
FROM anomalies
ORDER BY category, anomaly_type, entity;
