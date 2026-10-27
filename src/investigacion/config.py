from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración por variables de entorno; falla al arrancar si falta algo obligatorio."""

    model_config = SettingsConfigDict(env_prefix="INVESTIGACION_")

    # Base del sistema de DBOS (checkpoints de flujos). En el cluster: Postgres de investigacion-pg.
    database_url: str

    # Prototipo de la fase 0: cuántos pasos y cuánto duerme (durable) entre pasos.
    prototipo_pasos: int = 10
    prototipo_espera_s: float = 30.0
