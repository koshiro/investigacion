-- Esquema de la fase 1 (evidencia y verificación). SQL plano; los GRANT mínimos por rol viven en 0002_permisos.sql.
-- Todas las tablas llevan id UUID, created_at y created_by (`daniel`, `agent:<rol>` o `service:<nombre>`).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE investigation (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text NOT NULL,
    titulo      text NOT NULL,
    pregunta    text NOT NULL,
    periodo     text,
    territorio  text,
    estado      text NOT NULL DEFAULT 'activa' CHECK (estado IN ('activa', 'cerrada'))
);

CREATE TABLE source (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   text NOT NULL,
    investigation_id uuid REFERENCES investigation (id),
    organismo    text NOT NULL,
    url          text NOT NULL,
    tipo         text NOT NULL CHECK (tipo IN ('portal', 'dataset', 'documento', 'correo')),
    periodicidad text,
    ultimo_estado text
);

-- Inmutable: la ruta es evidencia/<sha256[0:2]>/<sha256>; misma huella = mismo archivo (UNIQUE sobre sha256).
CREATE TABLE capture (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text NOT NULL,
    source_id   uuid REFERENCES source (id),
    investigation_id uuid REFERENCES investigation (id),
    url_final   text NOT NULL,
    sha256      char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    mime        text NOT NULL,
    tamano      bigint NOT NULL CHECK (tamano >= 0),
    ruta        text NOT NULL,
    estado_http integer,
    fetched_at  timestamptz NOT NULL,
    cabeceras   jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (ruta = 'evidencia/' || substr(sha256, 1, 2) || '/' || sha256)
);
CREATE INDEX capture_sha256_idx ON capture (sha256);
CREATE INDEX capture_investigation_idx ON capture (investigation_id);

-- Varias extracciones por captura (al cambiar el extractor). `paginas` es una lista de {n, texto, inicio, fin} donde
-- inicio/fin son desplazamientos de caracteres dentro del texto completo del documento.
CREATE TABLE extraction (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text NOT NULL,
    capture_id  uuid NOT NULL REFERENCES capture (id),
    extractor   text NOT NULL,
    idioma      text,
    texto       text NOT NULL,
    paginas     jsonb NOT NULL,
    tablas      jsonb NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (capture_id, extractor)
);

-- Versión redactada y mapa de desplazamientos redactado → original. `mapa` es una lista de
-- {red_inicio, red_fin, orig_inicio, orig_fin, tipo, marcador}. Solo el texto redactado sale hacia el lector.
CREATE TABLE pii_scan (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at    timestamptz NOT NULL DEFAULT now(),
    created_by    text NOT NULL,
    extraction_id uuid NOT NULL REFERENCES extraction (id),
    version       text NOT NULL,
    hallazgos     jsonb NOT NULL DEFAULT '{}'::jsonb,
    nivel         text NOT NULL CHECK (nivel IN ('ninguno', 'bajo', 'alto')),
    texto_redactado text NOT NULL,
    mapa          jsonb NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (extraction_id, version)
);

CREATE TABLE card (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text NOT NULL,
    capture_id  uuid NOT NULL REFERENCES capture (id),
    pii_scan_id uuid NOT NULL REFERENCES pii_scan (id),
    agente      text NOT NULL,
    esquema     text NOT NULL,
    contenido   jsonb NOT NULL
);

-- `verified` y `estado` solo los escribe el verificador determinista (ver 0002_permisos.sql).
CREATE TABLE citation (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text NOT NULL,
    card_id     uuid NOT NULL REFERENCES card (id),
    capture_id  uuid NOT NULL REFERENCES capture (id),
    pii_scan_id uuid NOT NULL REFERENCES pii_scan (id),
    pagina      integer,
    texto_citado text NOT NULL,
    inicio      integer,
    fin         integer,
    verified    boolean NOT NULL DEFAULT false,
    estado      text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'verificada', 'revisar', 'fallida')),
    similitud   real,
    verificador text,
    CHECK (NOT verified OR estado = 'verificada')
);

CREATE TABLE llm_call (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at    timestamptz NOT NULL DEFAULT now(),
    created_by    text NOT NULL,
    rol           text NOT NULL,
    proveedor     text NOT NULL,
    modelo        text NOT NULL,
    tokens_entrada integer NOT NULL DEFAULT 0,
    tokens_salida  integer NOT NULL DEFAULT 0,
    costo_usd     numeric(12, 6) NOT NULL DEFAULT 0,
    hash_prompt   char(64) NOT NULL,
    redacciones   integer NOT NULL DEFAULT 0,
    -- Detección de datos personales que quedaron en el prompt justo antes de enviarlo (debe ser 0 al enviar).
    residuos_pii  integer NOT NULL DEFAULT 0,
    bloqueada     boolean NOT NULL DEFAULT false,
    motivo        text,
    latencia_ms   integer
);

-- Usuario único de la interfaz. La contraseña va con argon2 y el secreto TOTP cifrado por la app no se guarda aquí:
-- vive en el Secret de Kubernetes (ver web).
CREATE TABLE app_user (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at    timestamptz NOT NULL DEFAULT now(),
    created_by    text NOT NULL,
    nombre        text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    totp_ultimo_paso bigint NOT NULL DEFAULT 0
);

CREATE TABLE web_session (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text NOT NULL,
    user_id     uuid NOT NULL REFERENCES app_user (id),
    token_hash  char(64) NOT NULL UNIQUE,
    expira_at   timestamptz NOT NULL
);

-- Registro de auditoría: solo inserción, encadenado por hash. `hash` = sha256(prev_hash|seq|ts|actor|accion|objeto|datos)
-- con `datos` en su forma de texto de jsonb. Se agrega únicamente con audit_append() (0002_permisos.sql).
CREATE TABLE audit_log (
    seq        bigint PRIMARY KEY,
    ts         timestamptz NOT NULL DEFAULT clock_timestamp(),
    actor      text NOT NULL,
    accion     text NOT NULL,
    objeto     text NOT NULL,
    datos      jsonb NOT NULL DEFAULT '{}'::jsonb,
    prev_hash  char(64) NOT NULL,
    hash       char(64) NOT NULL UNIQUE
);
