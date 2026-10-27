"""Prototipo de la fase 0: un flujo DBOS que debe sobrevivir a `kubectl rollout restart`.

Cada paso deja registrado en qué pod corrió. Si el pod muere a mitad del flujo, el siguiente retoma desde el último
paso completado: los pasos terminados no se repiten (su salida está en `dbos.operation_outputs`) y la espera entre
pasos es un `DBOS.sleep` durable, cuya hora de despertar vive en Postgres.
"""

import socket
import threading
from datetime import UTC, datetime

import structlog
from dbos import DBOS, SetWorkflowID

log = structlog.get_logger()

# ID fijo: arrancar dos veces el mismo flujo devuelve el existente en vez de crear otro.
WORKFLOW_ID = "prototipo-fase0"


@DBOS.step()
def registrar_paso(n: int) -> str:
    marca = f"paso {n} en {socket.gethostname()} a las {datetime.now(UTC).isoformat(timespec='seconds')}"
    log.info("paso", n=n, pod=socket.gethostname())
    return marca


@DBOS.workflow()
def flujo_prueba(pasos: int, espera_s: float) -> list[str]:
    resultados = []
    for n in range(1, pasos + 1):
        resultados.append(registrar_paso(n))
        if n < pasos:
            DBOS.sleep(espera_s)
    return resultados


def iniciar(database_url: str) -> None:
    DBOS(config={"name": "investigacion", "system_database_url": database_url})
    # launch() también recupera los flujos que quedaron a medias en una ejecución anterior.
    DBOS.launch()


def correr(database_url: str, pasos: int, espera_s: float, workflow_id: str = WORKFLOW_ID) -> list[str]:
    iniciar(database_url)
    with SetWorkflowID(workflow_id):
        handle = DBOS.start_workflow(flujo_prueba, pasos, espera_s)
    resultado = handle.get_result()
    log.info("flujo terminado", workflow_id=workflow_id, pasos=len(resultado))
    for linea in resultado:
        log.info("resultado", linea=linea)
    return resultado


def esperar_para_siempre() -> None:
    """Deja el proceso vivo tras terminar, para que el Deployment no reinicie en bucle."""
    threading.Event().wait()
