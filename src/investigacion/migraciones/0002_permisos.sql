-- Roles por servicio con permisos mínimos, inmutabilidad y registro de auditoría encadenado.
-- Los roles son de grupo (NOLOGIN): en el cluster, los roles con login de CNPG (`managed.roles`) los reciben con
-- `inRoles`. Nadie tiene UPDATE ni DELETE sobre capture ni audit_log; los triggers lo impiden también al dueño.

DO $$
DECLARE r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['inv_web', 'inv_capture', 'inv_extract', 'inv_worker', 'inv_gateway', 'inv_verificador'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', r);
        END IF;
    END LOOP;
END $$;

-- Inmutabilidad: ninguna de estas tablas admite UPDATE, DELETE ni TRUNCATE (la transformación crea filas nuevas).
CREATE FUNCTION bloquear_modificacion() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'la tabla % es inmutable (% bloqueado)', TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['capture', 'extraction', 'pii_scan', 'card', 'audit_log', 'llm_call'] LOOP
        EXECUTE format('CREATE TRIGGER %I_inmutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION bloquear_modificacion()', t, t);
        EXECUTE format('CREATE TRIGGER %I_sin_truncate BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION bloquear_modificacion()', t, t);
    END LOOP;
END $$;

-- citation: solo cambian las columnas del verificador, y nunca se borra.
CREATE TRIGGER citation_sin_delete BEFORE DELETE OR TRUNCATE ON citation FOR EACH STATEMENT EXECUTE FUNCTION bloquear_modificacion();

-- Auditoría: la única forma de agregar es audit_append(). SECURITY DEFINER para que los roles de servicio no tengan
-- INSERT sobre la tabla; un candado asesor serializa las inserciones y mantiene la cadena sin bifurcaciones.
CREATE FUNCTION audit_hash(prev text, seq bigint, ts timestamptz, actor text, accion text, objeto text, datos jsonb)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to(concat_ws('|', prev, seq::text,
        to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'), actor, accion, objeto, datos::text), 'UTF8')), 'hex')
$$;

CREATE FUNCTION audit_append(p_actor text, p_accion text, p_objeto text, p_datos jsonb DEFAULT '{}'::jsonb)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    v_prev text;
    v_seq bigint;
    v_ts timestamptz := date_trunc('microseconds', clock_timestamp());
BEGIN
    PERFORM pg_advisory_xact_lock(7001);
    SELECT hash, seq INTO v_prev, v_seq FROM audit_log ORDER BY seq DESC LIMIT 1;
    IF NOT FOUND THEN
        v_prev := repeat('0', 64);
        v_seq := 0;
    END IF;
    v_seq := v_seq + 1;
    INSERT INTO audit_log (seq, ts, actor, accion, objeto, datos, prev_hash, hash)
    VALUES (v_seq, v_ts, p_actor, p_accion, p_objeto, p_datos, v_prev,
            audit_hash(v_prev, v_seq, v_ts, p_actor, p_accion, p_objeto, p_datos));
    RETURN v_seq;
END $$;

REVOKE ALL ON FUNCTION audit_append(text, text, text, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION audit_hash(text, bigint, timestamptz, text, text, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION audit_hash(text, bigint, timestamptz, text, text, text, jsonb)
    TO inv_web, inv_capture, inv_extract, inv_worker, inv_gateway, inv_verificador;
GRANT EXECUTE ON FUNCTION audit_append(text, text, text, jsonb)
    TO inv_web, inv_capture, inv_extract, inv_worker, inv_gateway, inv_verificador;

-- Ningún rol de servicio toca el esquema público más allá de lo que se concede aquí.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO inv_web, inv_capture, inv_extract, inv_worker, inv_gateway, inv_verificador;

-- web: lectura de todo lo que muestra, alta de investigaciones/fuentes y manejo de sesiones.
GRANT SELECT ON investigation, source, capture, extraction, pii_scan, card, citation, llm_call, audit_log TO inv_web;
GRANT INSERT ON investigation, source TO inv_web;
GRANT SELECT ON app_user TO inv_web;
GRANT UPDATE (totp_ultimo_paso) ON app_user TO inv_web;
GRANT SELECT, INSERT, DELETE ON web_session TO inv_web;

-- capture: crea capturas; ve el catálogo de fuentes.
GRANT SELECT ON investigation, source, capture TO inv_capture;
GRANT INSERT ON capture TO inv_capture;

-- extract: lee capturas y escribe extracciones y análisis de datos personales; nada más.
GRANT SELECT ON capture, extraction, pii_scan TO inv_extract;
GRANT INSERT ON extraction, pii_scan TO inv_extract;

-- worker: lee texto redactado y escribe fichas y citas (sin poder tocar verified/estado).
GRANT SELECT ON capture, extraction, pii_scan, card, citation TO inv_worker;
GRANT INSERT ON card TO inv_worker;
GRANT INSERT (id, created_by, card_id, capture_id, pii_scan_id, pagina, texto_citado, inicio, fin) ON citation TO inv_worker;

-- llm-gateway: solo registra llamadas.
GRANT SELECT, INSERT ON llm_call TO inv_gateway;

-- verificador: único que escribe verified/estado/similitud.
GRANT SELECT ON citation, pii_scan, extraction, capture TO inv_verificador;
GRANT UPDATE (verified, estado, similitud, verificador) ON citation TO inv_verificador;
