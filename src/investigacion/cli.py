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
    p_lector.add_argument("capture_id", nargs="?")
    p_lector.add_argument("--pendientes", action="store_true", help="todas las capturas sin ficha y no aisladas")
    p_lector.add_argument("--estrategia", choices=["json", "citas"], default="json")
    sub.add_parser("worker", help="flujos DBOS de los agentes (plan aprobado, descubrimiento, inventario, lector)")
    sub.add_parser("eval-lector", help="compara las dos estrategias del lector con la API real (cuesta centavos)")
    sub.add_parser("web", help="interfaz web (NodePort 30960): login con argon2 + TOTP, investigaciones y visor")
    p_usuario = sub.add_parser("crear-usuario", help="crea el usuario único; imprime el secreto TOTP una sola vez")
    p_usuario.add_argument("nombre")
    sub.add_parser("migrar", help="aplica las migraciones SQL pendientes (con el rol dueño de la base)")
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
            ids = lector.pendientes(w) if args.pendientes else [args.capture_id]
            if not ids or ids == [None]:
                raise SystemExit("indica un capture_id o --pendientes")
            for cid in ids:
                sys.stdout.write(f"{cid}: {lector.leer(w, v, gw, cid, args.estrategia)}\n")
    elif args.comando == "worker":
        servicio_worker(settings)
    elif args.comando == "eval-lector":
        evaluar_lector(settings)
    elif args.comando == "web":
        servicio_web(settings)
    elif args.comando == "crear-usuario":
        crear_usuario(settings, args.nombre)
    elif args.comando == "migrar":
        from investigacion import db

        with psycopg.connect(settings.database_url) as conn:
            sys.stdout.write(f"migraciones aplicadas: {db.migrar(conn) or 'ninguna pendiente'}\n")
    elif args.comando == "verificar-capturas":
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            problemas = capture.verificar_capturas(conn, Path(settings.evidencia_dir))
        for p in problemas:
            sys.stdout.write(f"{p.capture_id}: {p.motivo}\n")
        sys.exit(1 if problemas else 0)


def servicio_worker(settings: Settings) -> None:
    from investigacion import buscador, flujos

    if not settings.verificador_database_url:
        raise SystemExit("worker exige INVESTIGACION_VERIFICADOR_DATABASE_URL (rol distinto para verificar citas)")
    entorno = flujos.Entorno(
        worker_url=settings.database_url,
        verificador_url=settings.verificador_database_url,
        gateway=gateway.ClienteGateway(settings.gateway_url),
        buscador=buscador.Buscador(settings.searxng_url),
        solicitante={"nombre": settings.solicitante_nombre, "correo": settings.solicitante_correo},
    )
    # Los checkpoints de DBOS van al esquema dbos_worker de la misma base, con el rol del worker.
    flujos.iniciar(settings.database_url, entorno)
    parar = threading.Event()
    flujos.despertador(settings.database_url, settings.despertador_s, parar)


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
    modelos = {
        "lector": settings.modelo_lector,
        "investigador": settings.modelo_investigador,
        "redactor": settings.modelo_redactor,
        "verificador": settings.modelo_verificador,
    }
    return gateway.Gateway(cliente, conn, modelos, settings.tope_mensual_usd)


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


def servicio_web(settings: Settings) -> None:
    import uvicorn

    from investigacion import web

    if len(settings.web_clave) < 32 or not settings.web_totp_secreto:
        raise SystemExit("web exige INVESTIGACION_WEB_CLAVE (>= 32 caracteres) e INVESTIGACION_WEB_TOTP_SECRETO")
    cfg = web.Configuracion(
        lambda: psycopg.connect(settings.database_url, autocommit=True),
        Path(settings.evidencia_dir),
        settings.web_clave,
        settings.web_totp_secreto,
        settings.web_cookie_segura,
    )
    uvicorn.run(web.crear_app(cfg), host="0.0.0.0", port=8000, log_config=None)  # noqa: S104  (NodePort, LAN/tailnet)


def crear_usuario(settings: Settings, nombre: str) -> None:
    """Con el rol dueño de la base. Contraseña: INVESTIGACION_NUEVA_PASSWORD o se pide. El TOTP es el del Secret."""
    import getpass
    import os

    import pyotp

    from investigacion.web import auth

    if not settings.web_totp_secreto:
        raise SystemExit("falta INVESTIGACION_WEB_TOTP_SECRETO (Secret web-secreto): el TOTP debe ser el que usa web")
    password = os.environ.get("INVESTIGACION_NUEVA_PASSWORD") or getpass.getpass("Contraseña (>= 14 caracteres): ")
    if len(password) < 14:
        raise SystemExit("la contraseña debe tener al menos 14 caracteres")
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app_user (created_by, nombre, password_hash) VALUES ('daniel', %s, %s) "
            "ON CONFLICT (nombre) DO UPDATE SET password_hash = EXCLUDED.password_hash, totp_ultimo_paso = 0",
            (nombre, auth.hashear(password)),
        )
    uri = pyotp.TOTP(settings.web_totp_secreto).provisioning_uri(name=nombre, issuer_name="Investigación")
    sys.stdout.write(f"Usuario {nombre} listo. Agrega este TOTP a tu app de autenticación:\n{uri}\n")


if __name__ == "__main__":
    main()
