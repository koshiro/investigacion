import pytest
from dbos import DBOS

from investigacion import prototipo


@pytest.fixture
def db_url(tmp_path):
    yield f"sqlite:///{tmp_path / 'dbos.sqlite'}"
    DBOS.destroy()


def test_flujo_corre_todos_los_pasos(db_url):
    resultado = prototipo.correr(db_url, pasos=3, espera_s=0, workflow_id="prueba-1")
    assert [r.split(" en ")[0] for r in resultado] == ["paso 1", "paso 2", "paso 3"]


def test_mismo_id_no_repite_el_flujo(db_url):
    primero = prototipo.correr(db_url, pasos=2, espera_s=0, workflow_id="prueba-2")
    with prototipo.SetWorkflowID("prueba-2"):
        segundo = DBOS.start_workflow(prototipo.flujo_prueba, 2, 0).get_result()
    # Mismo ID → misma salida guardada, aunque las marcas de hora serían distintas si se hubiera vuelto a correr.
    assert segundo == primero
