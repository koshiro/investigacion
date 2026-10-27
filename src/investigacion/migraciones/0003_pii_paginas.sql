-- Páginas de la versión redactada: [{n, inicio, fin}] en coordenadas de texto_redactado (para el verificador de citas).
ALTER TABLE pii_scan ADD COLUMN paginas_redactadas jsonb NOT NULL DEFAULT '[]'::jsonb;
