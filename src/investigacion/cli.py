import argparse
import logging
import sys

import structlog

from investigacion import prototipo
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
    args = parser.parse_args(argv)

    configurar_logs()
    settings = Settings()
    if args.comando == "prototipo":
        prototipo.correr(settings.database_url, settings.prototipo_pasos, settings.prototipo_espera_s)
        prototipo.esperar_para_siempre()


if __name__ == "__main__":
    main()
