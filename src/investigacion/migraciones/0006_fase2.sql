-- Fase 2 (investigación documental): aprobaciones, organismos y contactos, hipótesis, planes y tareas, hallazgos,
-- borradores de solicitudes, feriados, inventario de dominios y esquema propio de DBOS para el worker.
--
-- Reglas que se imponen aquí (y no solo en el código), cada una con su prueba en tests/test_fase2_esquema.py:
-- - `approval` solo la inserta `web` (tras TOTP); nadie la modifica salvo marcar `used_at` UNA vez, y eso solo por
--   approval_usar(), que comprueba tipo, objeto y hash. El worker no tiene ningún permiso de escritura sobre ella.
-- - Un plan, un contacto o un hallazgo no avanza sin una aprobación usada cuyo hash es el de su contenido actual.
-- - El hash de lo aprobado lo calcula la base (columna generada): quien propone no puede declarar otro.

-- Hash de texto en UTF-8. La base es UTF-8 (CNPG), así que es determinista; IMMUTABLE para usarla en columnas generadas.
CREATE FUNCTION hash_texto(t text) RETURNS char(64) LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT encode(sha256(convert_to(t, 'UTF8')), 'hex')
$$;

-- --- organismos y contactos ----------------------------------------------------------------------------------------

CREATE TABLE organization (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at        timestamptz NOT NULL DEFAULT now(),
    created_by        text NOT NULL,
    nombre            text NOT NULL UNIQUE,
    tipo              text NOT NULL CHECK (tipo IN ('municipalidad', 'servicio', 'gobierno_regional', 'ministerio', 'otro')),
    -- Dominios oficiales (sin subdominio cuando cubre todos): la validación de contactos exige uno de estos o uno estatal.
    dominios          text[] NOT NULL CHECK (cardinality(dominios) > 0),
    url_transparencia text,
    canal_preferido   text CHECK (canal_preferido IN ('correo', 'portal'))
);

-- Canal de transparencia de un organismo. Lo propone el flujo discover_contact (worker) y solo pasa a `confirmado` con
-- una aprobación de Daniel ligada al hash de (organismo, correo, tipo, url, captura, cita).
CREATE TABLE organization_contact (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       timestamptz NOT NULL DEFAULT now(),
    created_by       text NOT NULL,
    organization_id  uuid NOT NULL REFERENCES organization (id),
    canal            text NOT NULL CHECK (canal IN ('correo', 'portal')),
    correo           text CHECK (correo IS NULL OR correo ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    url_portal       text CHECK (url_portal IS NULL OR url_portal ~ '^https://'),
    tipo             text NOT NULL CHECK (tipo IN ('transparencia', 'oirs', 'contacto_general', 'portal')),
    capture_id       uuid NOT NULL REFERENCES capture (id),
    cita             text NOT NULL CHECK (length(cita) > 0),
    validacion       jsonb NOT NULL DEFAULT '{}'::jsonb,
    estado           text NOT NULL DEFAULT 'candidato'
                     CHECK (estado IN ('candidato', 'revisar', 'confirmado', 'obsoleto', 'descartado')),
    confirmed_at     timestamptz,
    last_verified_at timestamptz,
    approval_id      uuid,
    content_hash     char(64) GENERATED ALWAYS AS (hash_texto(
        organization_id::text || '|' || canal || '|' || coalesce(correo, '') || '|' || coalesce(url_portal, '') || '|'
        || tipo || '|' || capture_id::text || '|' || cita)) STORED,
    CHECK ((canal = 'correo' AND correo IS NOT NULL) OR (canal = 'portal' AND url_portal IS NOT NULL)),
    CHECK (estado <> 'confirmado' OR (approval_id IS NOT NULL AND confirmed_at IS NOT NULL))
);
CREATE INDEX organization_contact_org_idx ON organization_contact (organization_id);

-- --- hipótesis, planes y tareas -----------------------------------------------------------------------------------

CREATE TABLE hypothesis (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       timestamptz NOT NULL DEFAULT now(),
    created_by       text NOT NULL,
    investigation_id uuid NOT NULL REFERENCES investigation (id),
    codigo           text NOT NULL CHECK (codigo ~ '^H[0-9]+$'),
    enunciado        text NOT NULL,
    la_sostiene      text NOT NULL,
    la_descarta      text NOT NULL,
    donde_mirar      text,
    estado           text NOT NULL DEFAULT 'abierta' CHECK (estado IN ('abierta', 'sostenida', 'debilitada', 'descartada')),
    UNIQUE (investigation_id, codigo)
);

-- Plan de tareas que propone el investigador. Inmutable: una corrección es una fila nueva con `anterior_id` (la bandeja
-- muestra el diff). Lo que se aprueba es `content_hash`, calculado por la base sobre el JSON exacto.
CREATE TABLE plan (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       timestamptz NOT NULL DEFAULT now(),
    created_by       text NOT NULL,
    investigation_id uuid NOT NULL REFERENCES investigation (id),
    anterior_id      uuid REFERENCES plan (id),
    workflow_id      text,
    contenido        jsonb NOT NULL,
    content_hash     char(64) GENERATED ALWAYS AS (hash_texto(contenido::text)) STORED
);

CREATE TABLE task (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       timestamptz NOT NULL DEFAULT now(),
    created_by       text NOT NULL,
    plan_id          uuid NOT NULL REFERENCES plan (id),
    orden            integer NOT NULL,
    rol              text NOT NULL CHECK (rol IN ('lector', 'investigador', 'redactor', 'verificador')),
    objetivo         text NOT NULL,
    entradas         jsonb NOT NULL DEFAULT '{}'::jsonb,
    salida           jsonb,
    estado           text NOT NULL DEFAULT 'propuesta'
                     CHECK (estado IN ('propuesta', 'en_curso', 'hecha', 'fallida', 'cancelada')),
    costo_usd        numeric(12, 6) NOT NULL DEFAULT 0,
    error            text,
    actualizado_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (plan_id, orden)
);

-- --- hallazgos y citas --------------------------------------------------------------------------------------------

CREATE TABLE finding (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       timestamptz NOT NULL DEFAULT now(),
    created_by       text NOT NULL,
    investigation_id uuid NOT NULL REFERENCES investigation (id),
    hypothesis_id    uuid REFERENCES hypothesis (id),
    task_id          uuid REFERENCES task (id),
    enunciado        text NOT NULL CHECK (length(enunciado) BETWEEN 1 AND 1000),
    tipo             text NOT NULL CHECK (tipo IN ('hecho', 'inferencia', 'hipotesis')),
    estado           text NOT NULL DEFAULT 'borrador'
                     CHECK (estado IN ('borrador', 'pendiente', 'verificado', 'rechazado')),
    limitaciones     jsonb NOT NULL DEFAULT '[]'::jsonb,
    content_hash     char(64) GENERATED ALWAYS AS (hash_texto(
        investigation_id::text || '|' || coalesce(hypothesis_id::text, '') || '|' || tipo || '|' || enunciado)) STORED
);

-- Una cita respalda una ficha (lector) o un hallazgo (investigador), nunca ambas.
ALTER TABLE citation ALTER COLUMN card_id DROP NOT NULL;
ALTER TABLE citation ADD COLUMN finding_id uuid REFERENCES finding (id);
ALTER TABLE citation ADD CONSTRAINT citation_un_dueno CHECK (num_nonnulls(card_id, finding_id) = 1);
CREATE INDEX citation_finding_idx ON citation (finding_id) WHERE finding_id IS NOT NULL;

-- --- aprobaciones -------------------------------------------------------------------------------------------------

-- Lista cerrada de lo que se aprueba (especificación, «Puntos de aprobación obligatoria»). `totp_paso` UNIQUE: un
-- código TOTP sirve para UNA aprobación. `vence_at`: una aprobación vieja no se usa.
CREATE TABLE approval (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   text NOT NULL CHECK (created_by = 'daniel'),
    objeto_tipo  text NOT NULL CHECK (objeto_tipo IN
                 ('plan', 'organization_contact', 'request', 'capture_release', 'finding', 'export', 'proxy_domain')),
    objeto_id    uuid NOT NULL,
    content_hash char(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    decision     text NOT NULL CHECK (decision IN ('aprobada', 'rechazada')),
    comentario   text,
    totp_paso    bigint NOT NULL UNIQUE CHECK (totp_paso > 0),
    decided_at   timestamptz NOT NULL DEFAULT now(),
    vence_at     timestamptz NOT NULL DEFAULT now() + interval '7 days',
    used_at      timestamptz,
    used_by      text
);
CREATE INDEX approval_objeto_idx ON approval (objeto_tipo, objeto_id);

-- Inmutable salvo el paso único used_at: NULL -> valor (y used_by). Ni el dueño puede reescribirla ni borrarla.
CREATE FUNCTION approval_inmutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.used_at IS NOT NULL OR NEW.used_by IS NOT NULL THEN
            RAISE EXCEPTION 'una aprobación nace sin usar' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'approval es inmutable (% bloqueado)', TG_OP USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.used_at IS NOT NULL OR NEW.used_at IS NULL
       OR (to_jsonb(NEW) - 'used_at' - 'used_by') IS DISTINCT FROM (to_jsonb(OLD) - 'used_at' - 'used_by') THEN
        RAISE EXCEPTION 'approval solo admite marcar used_at una vez' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER approval_inmutable BEFORE INSERT OR UPDATE OR DELETE ON approval
    FOR EACH ROW EXECUTE FUNCTION approval_inmutable();
CREATE TRIGGER approval_sin_truncate BEFORE TRUNCATE ON approval
    FOR EACH STATEMENT EXECUTE FUNCTION bloquear_modificacion();

-- Única vía para consumir una aprobación. Devuelve NULL si se usó ahora o el motivo del rechazo (y lo audita en ambos
-- casos). SECURITY DEFINER: los roles de servicio no tienen UPDATE sobre approval.
CREATE FUNCTION approval_usar(p_id uuid, p_tipo text, p_objeto uuid, p_hash text, p_actor text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    a approval%ROWTYPE;
    motivo text;
BEGIN
    SELECT * INTO a FROM approval WHERE id = p_id FOR UPDATE;
    IF NOT FOUND THEN motivo := 'no existe';
    ELSIF a.decision <> 'aprobada' THEN motivo := 'rechazada por Daniel';
    ELSIF a.used_at IS NOT NULL THEN motivo := 'ya usada';
    ELSIF a.vence_at < now() THEN motivo := 'vencida';
    ELSIF a.objeto_tipo <> p_tipo OR a.objeto_id <> p_objeto THEN motivo := 'otro objeto';
    ELSIF a.content_hash <> p_hash THEN motivo := 'el contenido cambió (hash distinto)';
    END IF;
    IF motivo IS NULL THEN
        UPDATE approval SET used_at = now(), used_by = p_actor WHERE id = p_id;
    END IF;
    PERFORM audit_append(p_actor, CASE WHEN motivo IS NULL THEN 'approval.usada' ELSE 'approval.rechazada' END,
        'approval/' || coalesce(p_id::text, '?'),
        jsonb_build_object('tipo', p_tipo, 'objeto', p_objeto, 'hash', p_hash, 'motivo', motivo));
    RETURN motivo;
END $$;
REVOKE ALL ON FUNCTION approval_usar(uuid, text, uuid, text, text) FROM PUBLIC;

-- ¿Hay una aprobación USADA, aprobada, para este objeto y este hash? (para los triggers de abajo)
CREATE FUNCTION aprobacion_usada(p_tipo text, p_objeto uuid, p_hash text, p_id uuid DEFAULT NULL)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT EXISTS (SELECT 1 FROM approval WHERE objeto_tipo = p_tipo AND objeto_id = p_objeto AND content_hash = p_hash
                   AND decision = 'aprobada' AND used_at IS NOT NULL AND (p_id IS NULL OR id = p_id))
$$;
REVOKE ALL ON FUNCTION aprobacion_usada(text, uuid, text, uuid) FROM PUBLIC;

-- Plan inmutable.
DO $$
BEGIN
    EXECUTE 'CREATE TRIGGER plan_inmutable BEFORE UPDATE OR DELETE ON plan FOR EACH ROW EXECUTE FUNCTION bloquear_modificacion()';
    EXECUTE 'CREATE TRIGGER plan_sin_truncate BEFORE TRUNCATE ON plan FOR EACH STATEMENT EXECUTE FUNCTION bloquear_modificacion()';
END $$;

-- Una tarea no corre sin la aprobación del plan exacto (falla cerrada aunque el código del worker se equivoque).
CREATE FUNCTION task_exige_plan_aprobado() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE h text;
BEGIN
    IF NEW.estado IN ('en_curso', 'hecha') AND (TG_OP = 'INSERT' OR OLD.estado = 'propuesta') THEN
        SELECT content_hash INTO h FROM plan WHERE id = NEW.plan_id;
        IF NOT aprobacion_usada('plan', NEW.plan_id, h) THEN
            RAISE EXCEPTION 'la tarea % no puede correr: el plan % no tiene aprobación', NEW.id, NEW.plan_id
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.plan_id, NEW.orden, NEW.rol, NEW.objetivo, NEW.entradas)
                            IS DISTINCT FROM (OLD.plan_id, OLD.orden, OLD.rol, OLD.objetivo, OLD.entradas) THEN
        RAISE EXCEPTION 'la definición de una tarea no cambia' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER task_exige_plan_aprobado BEFORE INSERT OR UPDATE ON task
    FOR EACH ROW EXECUTE FUNCTION task_exige_plan_aprobado();
CREATE TRIGGER task_sin_delete BEFORE DELETE OR TRUNCATE ON task
    FOR EACH STATEMENT EXECUTE FUNCTION bloquear_modificacion();

-- Contacto: nace como candidato o revisar; `confirmado` exige la aprobación usada de ESTE contenido.
CREATE FUNCTION contacto_exige_aprobacion() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.estado NOT IN ('candidato', 'revisar') THEN
        RAISE EXCEPTION 'un contacto nuevo nace como candidato o revisar' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.organization_id, NEW.canal, NEW.correo, NEW.url_portal, NEW.tipo, NEW.capture_id, NEW.cita)
        IS DISTINCT FROM (OLD.organization_id, OLD.canal, OLD.correo, OLD.url_portal, OLD.tipo, OLD.capture_id, OLD.cita) THEN
        RAISE EXCEPTION 'el contenido de un contacto no cambia: se crea otro' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.estado = 'confirmado' AND (TG_OP = 'INSERT' OR OLD.estado <> 'confirmado')
       AND (TG_OP = 'INSERT' OR NOT aprobacion_usada('organization_contact', NEW.id, OLD.content_hash, NEW.approval_id)) THEN
        RAISE EXCEPTION 'confirmar un contacto exige la aprobación de Daniel de este contenido'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER contacto_exige_aprobacion BEFORE INSERT OR UPDATE ON organization_contact
    FOR EACH ROW EXECUTE FUNCTION contacto_exige_aprobacion();
CREATE TRIGGER contacto_sin_delete BEFORE DELETE OR TRUNCATE ON organization_contact
    FOR EACH STATEMENT EXECUTE FUNCTION bloquear_modificacion();

-- Hallazgo `verificado`: al menos una cita, todas verificadas; si alguna está en `revisar`, hace falta la aprobación
-- usada del hallazgo. El enunciado no cambia después de creado (otro enunciado = otro hallazgo).
CREATE FUNCTION finding_reglas() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE total int; buenas int; revisar int;
BEGIN
    IF TG_OP = 'INSERT' AND NEW.estado NOT IN ('borrador', 'pendiente') THEN
        RAISE EXCEPTION 'un hallazgo nuevo nace como borrador o pendiente' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- En un trigger BEFORE la columna generada aún no está calculada: se comparan sus columnas de origen.
    IF TG_OP = 'UPDATE' AND (NEW.investigation_id, NEW.hypothesis_id, NEW.tipo, NEW.enunciado)
                            IS DISTINCT FROM (OLD.investigation_id, OLD.hypothesis_id, OLD.tipo, OLD.enunciado) THEN
        RAISE EXCEPTION 'el enunciado de un hallazgo no cambia' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.estado = 'verificado' AND OLD.estado <> 'verificado' THEN
        SELECT count(*), count(*) FILTER (WHERE verified), count(*) FILTER (WHERE estado = 'revisar')
          INTO total, buenas, revisar FROM citation WHERE finding_id = NEW.id;
        IF total = 0 OR buenas + revisar < total
           OR (revisar > 0 AND NOT aprobacion_usada('finding', NEW.id, OLD.content_hash)) THEN
            RAISE EXCEPTION 'hallazgo %: verificado exige todas sus citas verificadas (o aprobación si hay revisar)', NEW.id
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER finding_reglas BEFORE INSERT OR UPDATE ON finding FOR EACH ROW EXECUTE FUNCTION finding_reglas();
CREATE TRIGGER finding_sin_delete BEFORE DELETE OR TRUNCATE ON finding
    FOR EACH STATEMENT EXECUTE FUNCTION bloquear_modificacion();

-- Captura de nivel alto liberada por Daniel: aprobación usada sobre el pii_scan y el hash del texto redactado.
CREATE TABLE capture_release (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   text NOT NULL,
    pii_scan_id  uuid NOT NULL UNIQUE REFERENCES pii_scan (id),
    approval_id  uuid NOT NULL REFERENCES approval (id)
);
CREATE FUNCTION release_exige_aprobacion() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE h text;
BEGIN
    SELECT hash_texto(texto_redactado) INTO h FROM pii_scan WHERE id = NEW.pii_scan_id;
    IF NOT aprobacion_usada('capture_release', NEW.pii_scan_id, h, NEW.approval_id) THEN
        RAISE EXCEPTION 'liberar una captura aislada exige la aprobación de Daniel' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER release_exige_aprobacion BEFORE INSERT ON capture_release
    FOR EACH ROW EXECUTE FUNCTION release_exige_aprobacion();
DO $$
BEGIN
    EXECUTE 'CREATE TRIGGER capture_release_inmutable BEFORE UPDATE OR DELETE ON capture_release FOR EACH ROW EXECUTE FUNCTION bloquear_modificacion()';
END $$;

-- --- borradores de solicitudes ------------------------------------------------------------------------------------

-- Fase 2 solo crea borradores; envío, plazos y estados posteriores son de la fase 3 (dispatcher).
CREATE TABLE request (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       timestamptz NOT NULL DEFAULT now(),
    created_by       text NOT NULL,
    investigation_id uuid NOT NULL REFERENCES investigation (id),
    organization_id  uuid NOT NULL REFERENCES organization (id),
    contact_id       uuid REFERENCES organization_contact (id),
    anterior_id      uuid REFERENCES request (id),
    asunto           text NOT NULL CHECK (length(asunto) BETWEEN 1 AND 300),
    cuerpo           text NOT NULL CHECK (length(cuerpo) BETWEEN 1 AND 20000),
    estado           text NOT NULL DEFAULT 'borrador' CHECK (estado IN ('borrador', 'en_revision', 'aprobada', 'descartada')),
    content_hash     char(64) GENERATED ALWAYS AS (hash_texto(
        organization_id::text || '|' || coalesce(contact_id::text, '') || '|' || asunto || '|' || cuerpo)) STORED
);

-- Cada afirmación del borrador enlazada a un hallazgo VERIFICADO (se comprueba al insertar).
CREATE TABLE request_claim (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text NOT NULL,
    request_id  uuid NOT NULL REFERENCES request (id),
    finding_id  uuid NOT NULL REFERENCES finding (id),
    texto       text NOT NULL CHECK (length(texto) > 0),
    UNIQUE (request_id, finding_id, texto)
);
CREATE FUNCTION claim_exige_verificado() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM finding WHERE id = NEW.finding_id AND estado = 'verificado') THEN
        RAISE EXCEPTION 'una afirmación de un borrador debe enlazar un hallazgo verificado' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER claim_exige_verificado BEFORE INSERT ON request_claim FOR EACH ROW EXECUTE FUNCTION claim_exige_verificado();
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['request', 'request_claim'] LOOP
        EXECUTE format('CREATE TRIGGER %I_sin_delete BEFORE DELETE OR TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION bloquear_modificacion()', t, t);
    END LOOP;
END $$;
CREATE TRIGGER request_claim_inmutable BEFORE UPDATE ON request_claim FOR EACH ROW EXECUTE FUNCTION bloquear_modificacion();
-- El contenido de un borrador no cambia: una corrección es otra fila con anterior_id (diff en la bandeja).
CREATE FUNCTION request_contenido_fijo() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.organization_id, NEW.contact_id, NEW.asunto, NEW.cuerpo)
       IS DISTINCT FROM (OLD.organization_id, OLD.contact_id, OLD.asunto, OLD.cuerpo) THEN
        RAISE EXCEPTION 'el contenido de un borrador no cambia: se crea una versión nueva' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER request_contenido_fijo BEFORE UPDATE ON request FOR EACH ROW EXECUTE FUNCTION request_contenido_fijo();

-- --- feriados e inventario de dominios ----------------------------------------------------------------------------

-- Feriados de Chile para los días hábiles (fase 3). La carga anual la revisa Daniel; aquí no se siembra.
CREATE TABLE holiday (
    fecha       date PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text NOT NULL,
    nombre      text NOT NULL
);

-- Inventario de la lista blanca del proxy: cada dominio queda verificado, inexistente o no accesible con su evidencia.
CREATE TABLE source_domain (
    dominio     text PRIMARY KEY CHECK (dominio ~ '^[a-z0-9.-]+$'),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text NOT NULL,
    uso         text NOT NULL,
    cliente     text NOT NULL CHECK (cliente IN ('capture', 'llm-gateway', 'searxng', 'dispatcher')),
    en_lista    boolean NOT NULL DEFAULT true,
    estado      text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'verificada', 'inexistente', 'no_accesible')),
    url_prueba  text,
    capture_id  uuid REFERENCES capture (id),
    detalle     text,
    revisado_at timestamptz,
    CHECK (estado <> 'verificada' OR capture_id IS NOT NULL OR cliente <> 'capture')
);

-- --- esquema propio de DBOS para el worker ------------------------------------------------------------------------

-- El worker guarda los checkpoints de sus flujos aquí (dbos_system_schema = dbos_worker). Así no necesita escribir
-- en `public` más allá de sus GRANT, y `web` no toca DBOS: el worker se despierta solo al ver una aprobación nueva.
-- El dueño de la base no es superusuario (CNPG): crea el esquema y le da USAGE y CREATE al worker; las tablas que crea
-- DBOS quedan a nombre de usr_worker.
CREATE SCHEMA IF NOT EXISTS dbos_worker;
REVOKE ALL ON SCHEMA dbos_worker FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA dbos_worker TO inv_worker;

-- --- permisos -----------------------------------------------------------------------------------------------------

GRANT EXECUTE ON FUNCTION hash_texto(text) TO inv_web, inv_worker, inv_verificador;
GRANT EXECUTE ON FUNCTION approval_usar(uuid, text, uuid, text, text) TO inv_web, inv_worker;
-- Los triggers corren con el rol que escribe: necesitan poder preguntar si hay aprobación (solo devuelve un booleano).
GRANT EXECUTE ON FUNCTION aprobacion_usada(text, uuid, text, uuid) TO inv_web, inv_worker, inv_verificador;

-- web: ve todo lo nuevo; crea aprobaciones (solo ella) y aplica lo que Daniel decide en la bandeja.
GRANT SELECT ON organization, organization_contact, hypothesis, plan, task, finding, approval, capture_release,
    request, request_claim, holiday, source_domain TO inv_web;
GRANT INSERT (id, created_by, objeto_tipo, objeto_id, content_hash, decision, comentario, totp_paso) ON approval TO inv_web;
GRANT INSERT ON capture_release, holiday TO inv_web;
GRANT UPDATE (estado, confirmed_at, approval_id) ON organization_contact TO inv_web;
GRANT UPDATE (estado) ON hypothesis, request TO inv_web;
GRANT UPDATE (estado) ON finding TO inv_web;
GRANT INSERT ON source_domain TO inv_web;

-- worker: propone (planes, tareas, hallazgos, contactos, borradores) y ejecuta tareas aprobadas. Sin approval ni
-- verified: ni INSERT ni UPDATE sobre approval, y citation sigue sin verified/estado.
GRANT SELECT ON organization, organization_contact, hypothesis, plan, task, finding, approval, capture_release,
    request, request_claim, holiday, source_domain, investigation, source, capture_job TO inv_worker;
GRANT INSERT ON plan, task, finding, organization_contact, request, request_claim TO inv_worker;
GRANT UPDATE (estado, salida, costo_usd, error, actualizado_at) ON task TO inv_worker;
GRANT UPDATE (estado, last_verified_at, validacion) ON organization_contact TO inv_worker;
GRANT UPDATE (estado, url_prueba, capture_id, detalle, revisado_at) ON source_domain TO inv_worker;
GRANT INSERT (id, created_by, finding_id, capture_id, pii_scan_id, pagina, texto_citado) ON citation TO inv_worker;
GRANT INSERT ON capture_job, source TO inv_worker;

-- verificador: el estado `verificado` de un hallazgo lo pone él (además de verified en citation, como antes).
GRANT SELECT ON finding, approval TO inv_verificador;
GRANT UPDATE (estado) ON finding TO inv_verificador;
