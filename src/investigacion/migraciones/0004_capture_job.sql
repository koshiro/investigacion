-- Cola de capturas: `web` pide, `capture` ejecuta. Sin red entre ellos: solo Postgres.
CREATE TABLE capture_job (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text NOT NULL,
    investigation_id uuid NOT NULL REFERENCES investigation (id),
    source_id   uuid REFERENCES source (id),
    url         text NOT NULL CHECK (url ~ '^https?://'),
    estado      text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'en_curso', 'hecho', 'error')),
    capture_id  uuid REFERENCES capture (id),
    error       text,
    actualizado_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX capture_job_pendientes_idx ON capture_job (created_at) WHERE estado = 'pendiente';

GRANT SELECT, INSERT ON capture_job TO inv_web;
GRANT SELECT ON capture_job TO inv_capture;
GRANT UPDATE (estado, capture_id, error, actualizado_at) ON capture_job TO inv_capture;
