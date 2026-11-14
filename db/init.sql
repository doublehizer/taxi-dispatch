-- Схема базы данных системы распределения заказов такси

-- Учётные записи пользователей панели управления
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'dispatcher')),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS drivers (
    id               SERIAL PRIMARY KEY,
    name             TEXT NOT NULL,
    car              TEXT NOT NULL,
    plate            TEXT NOT NULL UNIQUE,
    car_class        TEXT NOT NULL CHECK (car_class IN ('economy', 'comfort', 'business')),
    status           TEXT NOT NULL DEFAULT 'free' CHECK (status IN ('offline', 'free', 'busy')),
    lat              DOUBLE PRECISION NOT NULL,
    lon              DOUBLE PRECISION NOT NULL,
    current_order_id INTEGER,
    rating           NUMERIC(2, 1) NOT NULL DEFAULT 5.0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orders (
    id                 SERIAL PRIMARY KEY,
    passenger_name     TEXT NOT NULL,
    passenger_phone    TEXT,
    tariff             TEXT NOT NULL CHECK (tariff IN ('economy', 'comfort', 'business')),
    pickup_lat         DOUBLE PRECISION NOT NULL,
    pickup_lon         DOUBLE PRECISION NOT NULL,
    dropoff_lat        DOUBLE PRECISION NOT NULL,
    dropoff_lon        DOUBLE PRECISION NOT NULL,
    distance_km        NUMERIC(6, 2) NOT NULL,
    price              NUMERIC(8, 2) NOT NULL,
    status             TEXT NOT NULL DEFAULT 'new' CHECK (status IN (
                           'new', 'searching', 'assigned', 'in_progress',
                           'completed', 'cancelled', 'no_drivers')),
    driver_id          INTEGER REFERENCES drivers (id) ON DELETE SET NULL,
    pickup_distance_km NUMERIC(6, 2),
    attempts           INTEGER NOT NULL DEFAULT 0,
    assigned_by        TEXT,
    created_by         TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_at        TIMESTAMPTZ,
    started_at         TIMESTAMPTZ,
    finished_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS orders_status_idx ON orders (status);
CREATE INDEX IF NOT EXISTS orders_created_idx ON orders (created_at DESC);
CREATE INDEX IF NOT EXISTS drivers_status_idx ON drivers (status);

-- Журнал смены статусов заказа (заполняется триггером)
CREATE TABLE IF NOT EXISTS order_status_log (
    id         BIGSERIAL PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION log_order_status() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO order_status_log (order_id, old_status, new_status)
        VALUES (NEW.id, NULL, NEW.status);
    ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO order_status_log (order_id, old_status, new_status)
        VALUES (NEW.id, OLD.status, NEW.status);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS orders_status_trg ON orders;
CREATE TRIGGER orders_status_trg
    AFTER INSERT OR UPDATE OF status ON orders
    FOR EACH ROW EXECUTE FUNCTION log_order_status();
