"""Conjunto fijo de la fase 1: 20 citas verdaderas y 20 inventadas o alteradas contra un documento sintético.

El documento imita una extracción real: guiones de corte de línea, espacios duros, comillas tipográficas, cifras con
formato chileno y marcadores de datos personales ya redactados. No cambiar el conjunto sin revisar el criterio de
aceptación de la fase 1 (el verificador debe aceptar las 20 verdaderas y rechazar las 20 falsas).
"""

PAGINAS = [
    (
        "MUNICIPALIDAD DE VILLARRICA\n"
        "Decreto alcaldicio N° 1.482 del 14 de marzo de 2023\n"
        "VISTOS: la ley N° 18.695, orgánica constitucional de municipali-\n"
        "dades, y el acuerdo del concejo municipal adoptado en sesión ordinaria.\n"
        "CONSIDERANDO: que el camino Pucón–Villarrica requiere repavimenta-\n"
        "ción urgente por el deterioro causado por el invierno.\n"
        "Se aprueba un monto de $ 245.800.000 para la obra denominada «Mejoramiento Calle Anfión Muñoz».\n"
        "El contratista adjudicado es la empresa Constructora Lago Calafquén SpA, RUT 76.123.456-7.\n"
    ),
    (
        "Anexo técnico del proyecto\n"
        "El inspector fiscal designado es [PERSONA_1], quien debe informar mensualmente el avance físico.\n"
        "El plazo de ejecución de las obras será de 180 días corridos contados desde la entrega de terreno.\n"
        "La superficie intervenida alcanza 12.350,5 metros cuadrados de calzada y 3.200 metros de aceras.\n"
        "El financiamiento proviene del Fondo Nacional de Desarrollo Regional, con un aporte municipal del 15%.\n"
        "No se contemplan obras de alcantarillado en esta etapa del proyecto.\n"
        "La recepción provisoria se realizó el 2 de octubre de 2023 sin observaciones de la inspección.\n"
    ),
    (
        "Ejecución presupuestaria al cuarto trimestre\n"
        "El gasto devengado acumulado asciende a $ 198.450.320, equivalente al 80,7% del monto aprobado.\n"
        "Se observó un atraso de 45 días en la entrega de los pavimentos de hormigón.\n"
        "El Servicio de Vivienda y Urbanización de la región de La Araucanía no cofinancia esta iniciativa.\n"
        "Los caminos vecinales de la comuna sin rol asignado quedan fuera de este convenio.\n"
        "El saldo por ejecutar es de $ 47.349.680 y se imputará al presupuesto del año siguiente.\n"
    ),
]

# (cita, página indicada). Todas deben salir `verificada`.
VERDADERAS = [
    ("Se aprueba un monto de $ 245.800.000 para la obra", 1),
    ("Decreto alcaldicio N° 1.482 del 14 de marzo de 2023", 1),
    ("orgánica constitucional de municipalidades", 1),  # guion de corte de línea
    ("requiere repavimentación urgente por el deterioro causado por el invierno", 1),  # corte en medio de palabra
    ("«Mejoramiento Calle Anfión Muñoz»", 1),
    ('"Mejoramiento Calle Anfión Muñoz"', 1),  # comillas rectas vs. angulares del original
    ("Constructora Lago Calafquén SpA, RUT 76.123.456-7", 1),
    ("el camino Pucón-Villarrica requiere", 1),  # guion simple vs. raya del original
    ("El inspector fiscal designado es [PERSONA_1]", 2),
    ("El plazo de ejecución de las obras será de 180 días corridos", 2),
    ("12.350,5 metros cuadrados de calzada y 3.200 metros de aceras", 2),
    ("con un aporte municipal del 15%", 2),
    ("No se contemplan obras de alcantarillado en esta etapa", 2),  # espacio duro en el original
    ("La recepción provisoria se realizó\nel 2 de octubre de 2023", 2),  # salto de línea en la cita
    ("El gasto devengado acumulado asciende a $ 198.450.320", 3),
    ("equivalente al 80,7% del monto aprobado", 3),
    ("Se observó un atraso de 45 días en la entrega de los pavimentos de hormigón", 3),
    ("no cofinancia esta iniciativa", 3),
    ("El saldo por ejecutar es de $ 47.349.680", 3),
    ("El plazo de ejecución de las obras será de 180 días corridos", 3),  # página equivocada: está en la 2
]

# (cita, página indicada). Ninguna debe salir `verified`.
FALSAS = [
    ("Se aprueba un monto de $ 254.800.000 para la obra", 1),  # cifra con dígitos transpuestos
    ("Se aprueba un monto de $ 245.800 para la obra", 1),  # cifra truncada
    ("Decreto alcaldicio N° 1.428 del 14 de marzo de 2023", 1),  # número de decreto alterado
    ("Decreto alcaldicio N° 1.482 del 14 de marzo de 2024", 1),  # año alterado
    (
        "El contratista adjudicado es la empresa Constructora Los Volcanes Ltda, RUT 76.123.456-7",
        1,
    ),  # empresa inventada
    ("Se rechaza el monto de $ 245.800.000 para la obra", 1),  # verbo invertido
    ("El plazo de ejecución de las obras será de 280 días corridos", 2),  # plazo alterado
    ("El plazo de ejecución de las obras será de 18 días corridos", 2),  # plazo truncado
    ("El inspector fiscal designado es Juan Pérez Soto, quien debe informar", 2),  # nombre inventado
    ("La superficie intervenida alcanza 12.350,5 metros cuadrados de calzada y 5.200 metros de aceras", 2),
    ("con un aporte municipal del 51%", 2),  # cifra alterada
    ("Se contemplan obras de alcantarillado en esta etapa del proyecto", 2),  # negación omitida (parece cita real)
    (
        "La recepción provisoria se realizó el 2 de octubre de 2023 con múltiples observaciones",
        2,
    ),  # extensión inventada
    ("El gasto devengado acumulado asciende a $ 198.450.230", 3),  # cifra con dígitos transpuestos
    ("equivalente al 8,07% del monto aprobado", 3),  # decimal desplazado
    ("equivalente al 90,7% del monto aprobado", 3),
    ("El Servicio de Vivienda y Urbanización cofinancia esta iniciativa con el 50% del monto", 3),
    ("La alcaldesa aprobó personalmente el pago anticipado de todas las obras", 3),  # oración inventada
    ("El saldo por ejecutar es de $ 47.349.680 y será devuelto al Gobierno Regional", 3),  # cola inventada
    ("Texto que no pertenece al documento en absoluto ni de lejos", 1),
]

assert len(VERDADERAS) == 20 and len(FALSAS) == 20
