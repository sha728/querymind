-- Shift every date in Northwind forward by a whole number of months so that the latest order
-- falls in the calendar month before the seed date (design §5.2, A3). Northwind's orders end in
-- 1998, so without this, questions like "last month" (user story U1) would return nothing.
-- Runs once, as the database owner, when the container initialises an empty volume.
DO $$
DECLARE
    latest date;
    months integer;
BEGIN
    SELECT max(order_date) INTO latest FROM orders;
    months := (EXTRACT(YEAR FROM current_date)::int * 12 + EXTRACT(MONTH FROM current_date)::int - 1)
            - (EXTRACT(YEAR FROM latest)::int * 12 + EXTRACT(MONTH FROM latest)::int);

    UPDATE orders SET
        order_date    = (order_date    + make_interval(months => months))::date,
        required_date = (required_date + make_interval(months => months))::date,
        shipped_date  = (shipped_date  + make_interval(months => months))::date;

    UPDATE employees SET
        birth_date = (birth_date + make_interval(months => months))::date,
        hire_date  = (hire_date  + make_interval(months => months))::date;

    RAISE NOTICE '03_date_shift: shifted all dates by % months (latest order was %)', months, latest;
END
$$;
