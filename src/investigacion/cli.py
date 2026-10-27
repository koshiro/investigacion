import argparse
import logging
import sys
import threading
from pathlib import Path

import psycopg
import structlog

from investigacion import capture, prototipo
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
    sub.add_parser("verificar-capturas", help="recalcula el hash de cada captura contra su fila y la auditoría")
    args = parser.parse_args(argv)

    configurar_logs()
    settings = Settings()
    if args.comando == "prototipo":
        prototipo.correr(settings.database_url, settings.prototipo_pasos, settings.prototipo_espera_s)
        prototipo.esperar_para_siempre()
    elif args.comando == "capture":
        servicio_capture(settings)
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


if __name__ == "__main__":
    main()
