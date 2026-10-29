from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración por variables de entorno; falla al arrancar si falta algo obligatorio."""

    model_config = SettingsConfigDict(env_prefix="INVESTIGACION_")

    # Base del sistema de DBOS (checkpoints de flujos). En el cluster: Postgres de investigacion-pg.
    database_url: str

    # capture: proxy con usuario (HTTPS_PROXY del Secret proxy-capture), raíz de la evidencia y límites.
    proxy_url: str = ""
    evidencia_dir: str = "/evidencia"
    captura_max_bytes: int = 100 * 1024 * 1024
    captura_intervalo_s: float = 5.0

    # Prototipo de la fase 0: cuántos pasos y cuánto duerme (durable) entre pasos.
    prototipo_pasos: int = 10
    prototipo_espera_s: float = 30.0

    # llm-gateway: único con la clave de la API; sale solo por el proxy (usuario llm-gateway).
    anthropic_api_key: str = ""
    modelo_lector: str = "claude-haiku-5-5"
    # Fase 2: un modelo por rol (especificación, «Agentes»); el verificador usa uno distinto del que escribió.
    modelo_investigador: str = "claude-sonnet-5-5"
    modelo_redactor: str = "claude-sonnet-5-5"
    modelo_verificador: str = "claude-opus-5-5"
    # Tope propio opcional por mes (USD). Daniel fija el límite duro en la consola de Anthropic.
    tope_mensual_usd: float | None = None
    # worker/lector: dónde está el gateway (Service interno).
    gateway_url: str = "http://llm-gateway:8080"

    # El lector escribe citas con el rol worker y las verifica con el rol verificador (credenciales distintas).
    verificador_database_url: str = ""

    # worker: SearXNG interno (solo título y URL) y datos del solicitante para las solicitudes (artículo 12 de la
    # Ley 20.285). Los datos del solicitante los agrega el código DESPUÉS del modelo: nunca salen hacia la API.
    searxng_url: str = "http://searxng:8080"
    solicitante_nombre: str = ""
    solicitante_correo: str = ""
    # Cada cuánto el worker busca aprobaciones nuevas para despertar a los flujos que esperan (DBOS.send).
    despertador_s: float = 30.0

    # web: clave para tokens CSRF, secreto TOTP del único usuario (ambos del Secret) y cookie Secure (solo con HTTPS).
    web_clave: str = ""
    web_totp_secreto: str = ""
    web_cookie_segura: bool = False
