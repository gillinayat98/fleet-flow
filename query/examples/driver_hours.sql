-- Driver labour summary: shifts worked, total hours, and estimated gross pay.
-- Joins completed shifts (time_entries) to the driver roster (profiles).
SELECT
    p.name                                                             AS driver,
    COUNT(*)                                                           AS shifts,
    ROUND(SUM(date_diff('second',
              from_iso8601_timestamp(t.clock_in),
              from_iso8601_timestamp(t.clock_out)) / 3600.0), 1)       AS hours,
    ROUND(SUM(date_diff('second',
              from_iso8601_timestamp(t.clock_in),
              from_iso8601_timestamp(t.clock_out)) / 3600.0)
              * p.hourly_rate, 2)                                      AS est_gross_pay
FROM time_entries t
JOIN profiles p ON t.employee_id = p.id
WHERE t.clock_out IS NOT NULL          -- only completed shifts
GROUP BY p.name, p.hourly_rate
ORDER BY hours DESC
LIMIT 10;
