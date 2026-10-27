"""Almacén de evidencia direccionado por hash: `<raíz>/<sha[0:2]>/<sha>`. Nada se sobrescribe ni se borra (regla 3)."""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

BLOQUE = 1024 * 1024


class EvidenciaCorrupta(Exception):
    """El archivo en disco no coincide con el hash que lo nombra."""


@dataclass(frozen=True)
class Guardado:
    sha256: str
    ruta: str  # relativa: evidencia/<sha[0:2]>/<sha>
    tamano: int
    nuevo: bool  # False si el mismo contenido ya estaba guardado


def ruta_relativa(sha256: str) -> str:
    return f"evidencia/{sha256[:2]}/{sha256}"


def ruta_absoluta(raiz: Path, sha256: str) -> Path:
    return raiz / sha256[:2] / sha256


def hash_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        while bloque := f.read(BLOQUE):
            h.update(bloque)
    return h.hexdigest()


class Escritor:
    """Escribe un flujo de bytes en un temporal (mismo sistema de archivos) y lo publica con su hash."""

    def __init__(self, raiz: Path, max_bytes: int):
        self.raiz = raiz
        self.max_bytes = max_bytes
        self._hash = hashlib.sha256()
        self._n = 0
        raiz.mkdir(parents=True, exist_ok=True)
        fd, nombre = tempfile.mkstemp(prefix=".subiendo-", dir=raiz)
        self._tmp = Path(nombre)
        self._f = os.fdopen(fd, "wb")

    def escribir(self, datos: bytes) -> None:
        self._n += len(datos)
        if self._n > self.max_bytes:
            self.descartar()
            raise ValueError(f"la descarga supera el límite de {self.max_bytes} bytes")
        self._hash.update(datos)
        self._f.write(datos)

    def descartar(self) -> None:
        self._f.close()
        self._tmp.unlink(missing_ok=True)

    def publicar(self) -> Guardado:
        self._f.flush()
        os.fsync(self._f.fileno())
        self._f.close()
        sha = self._hash.hexdigest()
        destino = ruta_absoluta(self.raiz, sha)
        destino.parent.mkdir(parents=True, exist_ok=True)
        try:
            # link() falla si el destino existe: nunca reemplaza un archivo existente.
            os.link(self._tmp, destino)
            nuevo = True
            destino.chmod(0o444)
        except FileExistsError:
            nuevo = False
            if hash_archivo(destino) != sha:
                self._tmp.unlink(missing_ok=True)
                raise EvidenciaCorrupta(f"{destino} existe pero su contenido no coincide con su nombre") from None
        finally:
            self._tmp.unlink(missing_ok=True)
        return Guardado(sha, ruta_relativa(sha), self._n, nuevo)


def guardar_bytes(raiz: Path, datos: bytes, max_bytes: int = 100 * 1024 * 1024) -> Guardado:
    e = Escritor(raiz, max_bytes)
    e.escribir(datos)
    return e.publicar()
