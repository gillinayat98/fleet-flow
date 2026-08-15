-- Shift & payroll anomaly detection.
-- Flags completed shifts whose duration, odometer, or driver pay rate look wrong.
WITH shifts AS (
    SELECT
        p.name AS driver,
        t.unit_no,
        t.clock_in,
        t.clock_out,
        t.odometer_in,
        t.odometer_out,
        p.hourly_rate,
        date_diff('second',
            from_iso8601_timestamp(t.clock_in),
            from_iso8601_timestamp(t.clock_out)) / 3600.0 AS hours
    FROM time_entries t
    JOIN profiles p ON t.employee_id = p.id
    WHERE t.clock_out IS NOT NULL          -- only completed shifts
)
SELECT
    driver,
    unit_no,
    ROUND(hours, 1) AS hours,
    hourly_rate,
    odometer_in,
    odometer_out,
    CASE
        WHEN hours <= 0                                              THEN 'nonpositive_duration'
        WHEN hours > 16                                             THEN 'excessive_shift'
        WHEN odometer_out IS NOT NULL AND odometer_in IS NOT NULL
             AND odometer_out < odometer_in                        THEN 'odometer_backwards'
        WHEN hourly_rate IS NULL OR hourly_rate = 0                THEN 'missing_hourly_rate'
    END AS anomaly
FROM shifts
WHERE hours <= 0
   OR hours > 16
   OR (odometer_out IS NOT NULL AND odometer_in IS NOT NULL AND odometer_out < odometer_in)
   OR hourly_rate IS NULL
   OR hourly_rate = 0
ORDER BY driver;
