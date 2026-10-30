-- Envío manual (decisión de Daniel, 2026-10-30): las primeras solicitudes las envía él desde su correo y lo registra en
-- la web. Registrar el envío CONSUME la aprobación (uso único) y exige que el contenido enviado sea el aprobado; los
-- plazos y la máquina de estados completa son de la fase 3 (dispatcher).
ALTER TABLE request DROP CONSTRAINT request_estado_check;
ALTER TABLE request ADD CONSTRAINT request_estado_check
    CHECK (estado IN ('borrador', 'en_revision', 'aprobada', 'descartada', 'enviada'));
ALTER TABLE request ADD COLUMN enviada_at timestamptz;
ALTER TABLE request ADD COLUMN enviada_por text CHECK (enviada_por IN ('manual', 'dispatcher'));
ALTER TABLE request ADD CONSTRAINT request_enviada_completa
    CHECK (estado <> 'enviada' OR (enviada_at IS NOT NULL AND enviada_por IS NOT NULL));

CREATE FUNCTION request_envio_exige_aprobacion() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.estado = 'enviada' AND OLD.estado <> 'enviada'
       AND NOT aprobacion_usada('request', NEW.id, OLD.content_hash) THEN
        RAISE EXCEPTION 'registrar el envío exige la aprobación usada de este contenido exacto'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.estado = 'enviada' AND (NEW.estado, NEW.enviada_at, NEW.enviada_por)
                                  IS DISTINCT FROM (OLD.estado, OLD.enviada_at, OLD.enviada_por) THEN
        RAISE EXCEPTION 'un envío registrado no se modifica' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER request_envio_exige_aprobacion BEFORE UPDATE ON request
    FOR EACH ROW EXECUTE FUNCTION request_envio_exige_aprobacion();

GRANT UPDATE (enviada_at, enviada_por) ON request TO inv_web;
