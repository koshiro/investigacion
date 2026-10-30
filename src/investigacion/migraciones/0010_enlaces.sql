-- Enlaces de una página HTML (URL absoluta y texto del ancla), para que el descubrimiento de canales siga en código
-- los enlaces de las páginas oficiales: la búsqueda web gratuita no sirve de base (medido el 2026-10-29). Son
-- contenido externo: solo los lee código que filtra por dominio oficial y lista blanca; nunca van a un prompt.
ALTER TABLE extraction ADD COLUMN enlaces jsonb NOT NULL DEFAULT '[]'::jsonb;
