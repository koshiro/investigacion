-- Semillas de la primera investigación (plan de investigación «Caminos de Villarrica», 27-oct-2026). Los correos de
-- transparencia NO se siembran: los descubre el flujo discover_contact y Daniel los confirma.

INSERT INTO investigation (created_by, titulo, pregunta, periodo, territorio)
SELECT 'daniel', 'Caminos de Villarrica',
       '¿Cuánto dinero público se programó y se ejecutó en construcción y mantención de caminos urbanos y rurales de '
       'la comuna de Villarrica entre 2019 y 2026, quién lo gastó, en qué tramos, y cómo se relaciona eso con el '
       'estado actual de esas vías?',
       '2019-2026', 'Villarrica, Región de La Araucanía'
WHERE NOT EXISTS (SELECT 1 FROM investigation WHERE titulo = 'Caminos de Villarrica');

INSERT INTO organization (created_by, nombre, tipo, dominios, url_transparencia, canal_preferido) VALUES
    ('daniel', 'Municipalidad de Villarrica', 'municipalidad', ARRAY['villarrica.cl'], NULL, NULL),
    ('daniel', 'SERVIU Araucanía', 'servicio', ARRAY['serviuaraucania.minvu.gob.cl', 'minvu.gob.cl', 'minvu.cl'], NULL, NULL),
    ('daniel', 'Gobierno Regional de La Araucanía', 'gobierno_regional', ARRAY['gorearaucania.cl'], NULL, NULL),
    ('daniel', 'Dirección de Vialidad Araucanía (MOP)', 'ministerio', ARRAY['vialidad.mop.gob.cl', 'mop.gob.cl', 'mop.cl'], NULL, NULL)
ON CONFLICT (nombre) DO NOTHING;

INSERT INTO hypothesis (created_by, investigation_id, codigo, enunciado, la_sostiene, la_descarta, donde_mirar)
SELECT 'daniel', i.id, h.codigo, h.enunciado, h.sostiene, h.descarta, h.mirar
FROM investigation i
CROSS JOIN (VALUES
    ('H1', 'Falta de presupuesto',
     'Gasto en vías por km o por habitante bajo frente a comunas comparables (Pucón, Loncoche, Pitrufquén)',
     'Gasto similar o mayor con peores resultados',
     'Presupuesto municipal (SINIM, transparencia activa), FNDR asignado por el GORE, presupuesto de Vialidad'),
    ('H2', 'Baja ejecución',
     'Proyectos aprobados que no se gastan: licitaciones desiertas, proyectos sin recomendación técnica, saldos devueltos',
     'Ejecución cercana al 100%',
     'Banco Integrado de Proyectos (estados de cada iniciativa), ejecución trimestral municipal, licitaciones en Mercado Público'),
    ('H3', 'Mala calidad de obras o de su fiscalización',
     'Obras recientes deterioradas antes de su vida útil, recepciones sin observaciones, aumentos de contrato, garantías no cobradas',
     'Obras que duran lo esperado',
     'Contratos y recepciones en Mercado Público, decretos municipales, informes de Contraloría, inspección en terreno'),
    ('H4', 'Caminos sin responsable',
     'Muchos kilómetros rurales fuera de la red enrolada del MOP',
     'Red rural mayoritariamente enrolada y con contrato de conservación',
     'Red vial de Vialidad por comuna, inventario municipal de caminos, solicitudes de enrolamiento'),
    ('H5', 'Priorización territorial',
     'Inversión concentrada en el centro urbano y zonas turísticas, sectores rurales postergados',
     'Distribución proporcional a kilómetros o población',
     'Ubicación de cada proyecto, actas del Concejo Municipal y del Consejo Regional'),
    ('H6', 'Factores físicos y de uso',
     'Lluvias intensas, suelos, tránsito pesado (forestal, camiones) y peak turístico de verano explican el desgaste',
     'Deterioro similar en comunas con menos lluvia y tráfico',
     'Precipitaciones (Dirección Meteorológica), conteos de tránsito de Vialidad'),
    ('H7', 'Irregularidades',
     'Contratistas repetidos con vínculos con funcionarios, fraccionamiento de compras, tratos directos frecuentes',
     'Licitaciones competitivas con varios oferentes',
     'Mercado Público, declaraciones de intereses (InfoProbidad), informes de Contraloría')
) AS h (codigo, enunciado, sostiene, descarta, mirar)
WHERE i.titulo = 'Caminos de Villarrica'
ON CONFLICT (investigation_id, codigo) DO NOTHING;

-- Inventario: la lista blanca de capture al 2026-10-29 más los tres dominios pendientes (fuera de la lista porque no
-- respondieron a clientes automáticos el 27-oct). El flujo de inventario los marca con evidencia.
INSERT INTO source_domain (dominio, created_by, uso, cliente, en_lista) VALUES
    ('bid-ckan.ministeriodesarrollosocial.gob.cl', 'daniel', 'Banco Integrado de Proyectos (CSV 2019-2027)', 'capture', true),
    ('api.mercadopublico.cl', 'daniel', 'API de licitaciones y órdenes de compra (ticket gratuito)', 'capture', true),
    ('www.mercadopublico.cl', 'daniel', 'Fichas de licitaciones', 'capture', true),
    ('www.chilecompra.cl', 'daniel', 'ChileCompra (institucional)', 'capture', true),
    ('datos-abiertos.chilecompra.cl', 'daniel', 'Datos abiertos de ChileCompra', 'capture', true),
    ('www.sinim.gov.cl', 'daniel', 'Presupuesto municipal comparable (SINIM)', 'capture', true),
    ('www.dipres.gob.cl', 'daniel', 'Ejecución presupuestaria MINVU y MOP', 'capture', true),
    ('www.contraloria.cl', 'daniel', 'Informes de fiscalización', 'capture', true),
    ('www.infolobby.cl', 'daniel', 'Audiencias de lobby', 'capture', true),
    ('www.infoprobidad.cl', 'daniel', 'Declaraciones de intereses', 'capture', true),
    ('www.villarrica.cl', 'daniel', 'Municipalidad de Villarrica: sitio y transparencia activa', 'capture', true),
    ('serviuaraucania.minvu.gob.cl', 'daniel', 'SERVIU Araucanía: sitio y documentos', 'capture', true),
    ('vialidad.mop.gob.cl', 'daniel', 'Dirección de Vialidad: red vial y contratos', 'capture', true),
    ('www.portaltransparencia.cl', 'daniel', 'Portal de Transparencia (fichas de los cuatro organismos)', 'capture', false),
    ('www.bcn.cl', 'daniel', 'Texto de la Ley 20.285 y normas citadas', 'capture', false),
    ('www.gorearaucania.cl', 'daniel', 'Gobierno Regional: transparencia activa y cartera FNDR', 'capture', false),
    ('api.anthropic.com', 'daniel', 'API de Claude', 'llm-gateway', true)
ON CONFLICT (dominio) DO NOTHING;
