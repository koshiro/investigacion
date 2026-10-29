-- Medido en vivo el 2026-10-29 (SearXNG): el sitio oficial de la Municipalidad de Villarrica es munivillarrica.cl y su
-- ficha de transparencia activa en el Portal es MU339. villarrica.cl queda fuera de los dominios oficiales hasta que el
-- inventario muestre qué es (un dominio no oficial podría proponer un correo equivocado).
UPDATE organization SET dominios = ARRAY['munivillarrica.cl'],
       url_transparencia = 'https://www.portaltransparencia.cl/PortalPdT/pdtta/-/ta/MU339'
WHERE nombre = 'Municipalidad de Villarrica';

INSERT INTO source_domain (dominio, created_by, uso, cliente, en_lista) VALUES
    ('www.munivillarrica.cl', 'daniel', 'Municipalidad de Villarrica: sitio oficial y transparencia', 'capture', true)
ON CONFLICT (dominio) DO NOTHING;
UPDATE source_domain SET uso = 'Dominio villarrica.cl (no es el sitio municipal; por verificar qué es)'
WHERE dominio = 'www.villarrica.cl';
