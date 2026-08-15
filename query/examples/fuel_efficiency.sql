-- Fuel economy per fill-up (diagnostic + analytics).
-- Shows every fuel stop with distance since the unit's previous stop and the
-- resulting km/L. Rows with NULL km_per_litre are a unit's first-ever fill
-- (no prior odometer to measure distance against).
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
)
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
ORDER BY unit_no, report_date;
