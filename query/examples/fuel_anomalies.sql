-- Fuel anomaly detection.
-- For each unit, compare a fuel stop to the previous one (chronologically) to
-- derive distance travelled and fuel economy, then flag implausible values.
WITH fuel AS (
    SELECT
        unit_no,
        report_date,
        odometer,
        fuel_litres,
        LAG(odometer) OVER (
            PARTITION BY unit_no ORDER BY report_date, created_at
        ) AS prev_odometer
    FROM fuel_reports
),
eff AS (
    SELECT
        unit_no,
        report_date,
        odometer,
        fuel_litres,
        (odometer - prev_odometer) AS distance_km,
        CASE
            WHEN fuel_litres > 0 AND prev_odometer IS NOT NULL
            THEN ROUND((odometer - prev_odometer) / fuel_litres, 2)
        END AS km_per_litre
    FROM fuel
)
SELECT
    unit_no,
    report_date,
    odometer,
    fuel_litres,
    distance_km,
    km_per_litre,
    CASE
        WHEN fuel_litres <= 0                       THEN 'zero_or_missing_litres'
        WHEN distance_km < 0                        THEN 'odometer_backwards'
        WHEN km_per_litre < 1.0                     THEN 'implausible_low_economy'
        WHEN km_per_litre > 6.0                     THEN 'implausible_high_economy'
    END AS anomaly
FROM eff
WHERE fuel_litres <= 0
   OR distance_km < 0
   OR km_per_litre < 1.0
   OR km_per_litre > 6.0
ORDER BY unit_no, report_date;
