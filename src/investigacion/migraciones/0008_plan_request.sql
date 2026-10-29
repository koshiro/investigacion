-- Daniel pide un plan desde la web; el worker lo encola (flujo DBOS con id `plan-<id>`, idempotente). Así `web` no
-- necesita acceso a DBOS y ningún agente gasta API sin que Daniel lo haya pedido.
CREATE TABLE plan_request (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       timestamptz NOT NULL DEFAULT now(),
    created_by       text NOT NULL CHECK (created_by = 'daniel'),
    investigation_id uuid NOT NULL REFERENCES investigation (id),
    workflow_id      text,
    iniciado_at      timestamptz
);
CREATE TRIGGER plan_request_sin_delete BEFORE DELETE OR TRUNCATE ON plan_request
    FOR EACH STATEMENT EXECUTE FUNCTION bloquear_modificacion();

GRANT SELECT ON plan_request TO inv_web, inv_worker;
GRANT INSERT (created_by, investigation_id) ON plan_request TO inv_web;
GRANT UPDATE (workflow_id, iniciado_at) ON plan_request TO inv_worker;

-- Los tres dominios pendientes del 27-oct entran a la lista del proxy (homelab-config) para que el inventario los pruebe
-- con evidencia; el descubrimiento de canales necesita además el Portal y el sitio del GORE.
UPDATE source_domain SET en_lista = true
WHERE dominio IN ('www.portaltransparencia.cl', 'www.bcn.cl', 'www.gorearaucania.cl');
