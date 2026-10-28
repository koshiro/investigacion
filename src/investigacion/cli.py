import argparse
import logging
import sys
import threading
from pathlib import Path

import psycopg
import structlog

from investigacion import capture, extract, gateway, lector, prototipo
from investigacion.config import Settings


def configurar_logs() -> None:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ]
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="investigacion")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("prototipo", help="flujo DBOS de prueba de la fase 0 (sobrevive reinicios)")
    sub.add_parser("capture", help="servicio de capturas: atiende capture_job y descarga por el proxy")
    sub.add_parser("extract", help="servicio de extracción y redacción (sin red): captura -> texto -> pii_scan")
    sub.add_parser(
        "llm-gateway", help="único cliente de la API de Claude (redacción previa, registro, sin herramientas)"
    )
    p_lector = sub.add_parser("lector", help="lee una captura con el agente lector y verifica sus citas")
    p_lector.add_argument("capture_id")
    p_lector.add_argument("--estrategia", choices=["json", "citas"], default="json")
    sub.add_parser("eval-lector", help="compara las dos estrategias del lector con la API real (cuesta centavos)")
    sub.add_parser("verificar-capturas", help="recalcula el hash de cada captura contra su fila y la auditoría")
    args = parser.parse_args(argv)

    configurar_logs()
    settings = Settings()
    if args.comando == "prototipo":
        prototipo.correr(settings.database_url, settings.prototipo_pasos, settings.prototipo_espera_s)
        prototipo.esperar_para_siempre()
    elif args.comando == "capture":
        servicio_capture(settings)
    elif args.comando == "extract":
        servicio_extract(settings)
    elif args.comando == "llm-gateway":
        servicio_gateway(settings)
    elif args.comando == "lector":
        gw = gateway.ClienteGateway(settings.gateway_url)
        with (
            psycopg.connect(settings.database_url, autocommit=True) as w,
            psycopg.connect(settings.verificador_database_url or settings.database_url, autocommit=True) as v,
        ):
            sys.stdout.write(f"{lector.leer(w, v, gw, args.capture_id, args.estrategia)}\n")
    elif args.comando == "eval-lector":
        evaluar_lector(settings)
    elif args.comando == "verificar-capturas":
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            problemas = capture.verificar_capturas(conn, Path(settings.evidencia_dir))
        for p in problemas:
            sys.stdout.write(f"{p.capture_id}: {p.motivo}\n")
        sys.exit(1 if problemas else 0)


def servicio_capture(settings: Settings) -> None:
    descargador = capture.Descargador(settings.proxy_url, settings.captura_max_bytes)
    raiz = Path(settings.evidencia_dir)
    parar = threading.Event()
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        while not parar.is_set():
            if capture.procesar_pendientes(conn, descargador, raiz) == 0:
                parar.wait(settings.captura_intervalo_s)


def servicio_extract(settings: Settings) -> None:
    from investigacion import detectores_ml, redaccion

    detectores = (*redaccion.DETECTORES_DETERMINISTAS, detectores_ml.detector_gliner())
    raiz = Path(settings.evidencia_dir)
    parar = threading.Event()
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        while not parar.is_set():
            if extract.procesar_capturas(conn, raiz, detectores) == 0:
                parar.wait(settings.captura_intervalo_s)


def _gateway(settings: Settings, conn: psycopg.Connection) -> gateway.Gateway:
    cliente = gateway.crear_cliente_anthropic(settings.anthropic_api_key, settings.proxy_url)
    return gateway.Gateway(cliente, conn, {"lector": settings.modelo_lector}, settings.tope_mensual_usd)


def servicio_gateway(settings: Settings) -> None:
    import uvicorn

    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        app = gateway.crear_app(_gateway(settings, conn))
        uvicorn.run(app, host="0.0.0.0", port=8080, log_config=None)  # noqa: S104  (Service interno, NetworkPolicy)


def evaluar_lector(settings: Settings) -> None:
    from investigacion import eval_lector

    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        gw = _gateway(settings, conn)
        for estrategia in ("json", "citas"):
            sys.stdout.write(eval_lector.evaluar(gw, estrategia).resumen() + "\n")


if __name__ == "__main__":
    main()
