"""Interfaz web (FastAPI + Jinja + HTMX): login, investigaciones, capturas y visor. Sin Ingress, solo LAN o tailnet.

Todo texto que viene de fuentes externas se muestra escapado (autoescape de Jinja) y los archivos originales solo se
entregan como descarga (`attachment`, `nosniff`), nunca se interpretan en el navegador.
"""

import uuid
from collections.abc import Callable
from pathlib import Path

import psycopg
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from investigacion import aprobaciones, auditoria, capture, evidencia
from investigacion.web import auth

AQUI = Path(__file__).parent
COOKIE = "sesion"
CABECERAS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
}


class Configuracion:
    def __init__(
        self,
        conectar: Callable[[], psycopg.Connection],
        evidencia_dir: Path,
        clave: str,
        totp_secreto: str,
        cookie_segura: bool = False,
        limitador: auth.Limitador | None = None,
    ):
        self.conectar, self.evidencia_dir, self.clave = conectar, evidencia_dir, clave
        self.totp_secreto, self.cookie_segura = totp_secreto, cookie_segura
        self.limitador = limitador or auth.Limitador()


def crear_app(cfg: Configuracion) -> FastAPI:
    app = FastAPI(title="investigación", docs_url=None, redoc_url=None, openapi_url=None)
    plantillas = Jinja2Templates(directory=str(AQUI / "plantillas"))  # Starlette activa autoescape=True
    app.mount("/static", StaticFiles(directory=str(AQUI / "static")), name="static")

    @app.middleware("http")
    async def seguridad(request: Request, call_next):
        resp = await call_next(request)
        for k, v in CABECERAS.items():
            resp.headers.setdefault(k, v)
        return resp

    def conexion():
        conn = cfg.conectar()
        try:
            yield conn
        finally:
            conn.close()

    def sesion(request: Request, conn=Depends(conexion)):
        token = request.cookies.get(COOKIE)
        fila = auth.usuario_de_sesion(conn, token)
        if fila is None:
            raise HTTPException(303, headers={"Location": "/login"})
        return {"id": fila[0], "nombre": fila[1], "token": token, "csrf": auth.token_csrf(cfg.clave, token)}  # type: ignore[arg-type]

    def csrf_ok(s: dict, csrf: str) -> None:
        if csrf != s["csrf"]:
            raise HTTPException(403, "token CSRF inválido")

    def render(request: Request, nombre: str, s: dict | None = None, **ctx) -> HTMLResponse:
        return plantillas.TemplateResponse(request, nombre, {"s": s, **ctx})

    # --- acceso ---------------------------------------------------------------------------------------------------

    @app.get("/healthz")
    def salud() -> dict:
        return {"ok": True}

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return render(request, "login.html", error=None)

    @app.post("/login")
    def login(
        request: Request, usuario: str = Form(), password: str = Form(), totp: str = Form(), conn=Depends(conexion)
    ):
        codigo = totp
        ip = request.client.host if request.client else "?"
        claves = (f"u:{usuario}", f"ip:{ip}")
        if any(cfg.limitador.bloqueado(c) for c in claves):
            auditoria.registrar(conn, "service:web", "login.bloqueado", f"user/{usuario[:40]}", {"ip": ip})
            resp = render(request, "login.html", error="Demasiados intentos. Espera unos minutos.")
            resp.status_code = 429
            return resp
        fila = conn.execute(
            "SELECT id, password_hash, totp_ultimo_paso FROM app_user WHERE nombre = %s", (usuario,)
        ).fetchone()
        ok_pass = auth.password_ok(fila[1] if fila else None, password)
        paso = auth.paso_totp(cfg.totp_secreto, codigo, fila[2] if fila else 0) if ok_pass and fila else None
        if paso is None:
            for c in claves:
                cfg.limitador.fallo(c)
            auditoria.registrar(conn, "service:web", "login.fallo", f"user/{usuario[:40]}", {"ip": ip})
            resp = render(request, "login.html", error="Usuario, contraseña o código incorrectos.")
            resp.status_code = 401
            return resp
        for c in claves:
            cfg.limitador.exito(c)
        conn.execute("UPDATE app_user SET totp_ultimo_paso = %s WHERE id = %s", (paso, fila[0]))
        token = auth.crear_sesion(conn, fila[0])
        auditoria.registrar(conn, "daniel", "login.ok", f"user/{usuario[:40]}", {"ip": ip})
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            COOKIE,
            token,
            max_age=auth.SESION_HORAS * 3600,
            httponly=True,
            samesite="strict",
            secure=cfg.cookie_segura,
            path="/",
        )
        return resp

    @app.post("/logout")
    def logout(csrf: str = Form(), s=Depends(sesion), conn=Depends(conexion)):
        csrf_ok(s, csrf)
        auth.cerrar_sesion(conn, s["token"])
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp

    # --- investigaciones ------------------------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def inicio(request: Request, s=Depends(sesion), conn=Depends(conexion)):
        filas = conn.execute(
            "SELECT i.id, i.titulo, i.territorio, i.periodo, i.estado, "
            "(SELECT count(*) FROM capture c WHERE c.investigation_id = i.id) FROM investigation i ORDER BY i.created_at DESC"
        ).fetchall()
        return render(request, "inicio.html", s, investigaciones=filas)

    @app.post("/investigaciones")
    def crear_investigacion(
        titulo: str = Form(max_length=200),
        pregunta: str = Form(max_length=2000),
        periodo: str = Form(""),
        territorio: str = Form(""),
        csrf: str = Form(),
        s=Depends(sesion),
        conn=Depends(conexion),
    ):
        csrf_ok(s, csrf)
        iid = conn.execute(
            "INSERT INTO investigation (created_by, titulo, pregunta, periodo, territorio) VALUES ('daniel',%s,%s,%s,%s) RETURNING id",
            (titulo, pregunta, periodo or None, territorio or None),
        ).fetchone()[0]  # type: ignore[index]
        auditoria.registrar(conn, "daniel", "investigacion.crear", f"investigation/{iid}", {"titulo": titulo[:100]})
        return RedirectResponse(f"/investigaciones/{iid}", status_code=303)

    @app.get("/investigaciones/{iid}", response_class=HTMLResponse)
    def investigacion(request: Request, iid: str, s=Depends(sesion), conn=Depends(conexion)):
        inv = conn.execute(
            "SELECT id, titulo, pregunta, periodo, territorio, estado FROM investigation WHERE id = %s", (iid,)
        ).fetchone()
        if inv is None:
            raise HTTPException(404)
        capturas = conn.execute(
            "SELECT c.id, c.url_final, c.mime, c.tamano, c.sha256, c.fetched_at, u.nivel FROM capture c "
            "LEFT JOIN LATERAL (SELECT p.nivel FROM pii_scan p JOIN extraction e ON e.id = p.extraction_id "
            "WHERE e.capture_id = c.id ORDER BY p.created_at DESC LIMIT 1) u ON true "
            "WHERE c.investigation_id = %s ORDER BY c.fetched_at DESC",
            (iid,),
        ).fetchall()
        trabajos = conn.execute(
            "SELECT url, estado, error FROM capture_job WHERE investigation_id = %s AND estado <> 'hecho' ORDER BY created_at DESC LIMIT 20",
            (iid,),
        ).fetchall()
        pedidos = conn.execute(
            "SELECT created_at, workflow_id, iniciado_at FROM plan_request WHERE investigation_id = %s "
            "ORDER BY created_at DESC LIMIT 5",
            (iid,),
        ).fetchall()
        tareas = conn.execute(
            "SELECT t.orden, t.rol, t.entradas->>'herramienta', t.objetivo, t.estado, t.error, t.costo_usd FROM task t "
            "WHERE t.plan_id = (SELECT id FROM plan WHERE investigation_id = %s ORDER BY created_at DESC LIMIT 1) "
            "ORDER BY t.orden",
            (iid,),
        ).fetchall()
        canales = conn.execute(
            "SELECT o.nombre, k.canal, coalesce(k.correo, k.url_portal), k.tipo, k.estado FROM organization_contact k "
            "JOIN organization o ON o.id = k.organization_id WHERE k.estado <> 'descartado' ORDER BY o.nombre, k.estado",
        ).fetchall()
        borradores = conn.execute(
            "SELECT r.id, o.nombre, r.asunto, r.estado FROM request r JOIN organization o ON o.id = r.organization_id "
            "WHERE r.investigation_id = %s AND NOT EXISTS (SELECT 1 FROM request n WHERE n.anterior_id = r.id) "
            "ORDER BY o.nombre",
            (iid,),
        ).fetchall()
        hallazgos = conn.execute(
            "SELECT coalesce(h.codigo, '—'), f.estado, count(*) FROM finding f LEFT JOIN hypothesis h ON h.id = f.hypothesis_id "
            "WHERE f.investigation_id = %s GROUP BY 1, 2 ORDER BY 1, 2",
            (iid,),
        ).fetchall()
        return render(
            request,
            "investigacion.html",
            s,
            inv=inv,
            capturas=capturas,
            trabajos=trabajos,
            pedidos=pedidos,
            tareas=tareas,
            canales=canales,
            borradores=borradores,
            hallazgos=hallazgos,
        )

    @app.post("/investigaciones/{iid}/plan")
    def pedir_plan(iid: uuid.UUID, csrf: str = Form(), s=Depends(sesion), conn=Depends(conexion)):
        csrf_ok(s, csrf)
        # Un plan a la vez por investigación: un doble clic no debe proponer (y cobrar) dos planes.
        en_curso = conn.execute(
            "SELECT 1 FROM plan_request r WHERE r.investigation_id = %(i)s AND (r.iniciado_at IS NULL "
            "OR NOT EXISTS (SELECT 1 FROM plan p WHERE p.workflow_id = r.workflow_id) "
            "OR EXISTS (SELECT 1 FROM plan p JOIN task t ON t.plan_id = p.id WHERE p.workflow_id = r.workflow_id "
            "AND t.estado IN ('propuesta', 'en_curso') AND NOT EXISTS (SELECT 1 FROM approval a "
            "WHERE a.objeto_tipo = 'plan' AND a.objeto_id = p.id AND a.decision = 'rechazada')))",
            {"i": str(iid)},
        ).fetchone()
        if en_curso:
            raise HTTPException(409, "Ya hay un plan pedido o en curso para esta investigación.")
        pid = conn.execute(
            "INSERT INTO plan_request (created_by, investigation_id) VALUES ('daniel', %s) RETURNING id", (str(iid),)
        ).fetchone()[0]  # type: ignore[index]
        auditoria.registrar(conn, "daniel", "plan.pedir", f"plan_request/{pid}", {"investigation": str(iid)})
        return RedirectResponse(f"/investigaciones/{iid}", status_code=303)

    @app.post("/investigaciones/{iid}/capturar")
    def pedir_captura(
        iid: str, url: str = Form(max_length=2000), csrf: str = Form(), s=Depends(sesion), conn=Depends(conexion)
    ):
        csrf_ok(s, csrf)
        if not url.lower().startswith(("http://", "https://")):
            raise HTTPException(422, "la URL debe empezar con http:// o https://")
        jid = conn.execute(
            "INSERT INTO capture_job (created_by, investigation_id, url) VALUES ('daniel',%s,%s) RETURNING id",
            (iid, url),
        ).fetchone()[0]  # type: ignore[index]
        auditoria.registrar(conn, "daniel", "capture.pedir", f"capture_job/{jid}", {"url": url[:300]})
        return RedirectResponse(f"/investigaciones/{iid}", status_code=303)

    # --- visor de capturas ----------------------------------------------------------------------------------------

    def _captura(conn, cid):
        fila = conn.execute(
            "SELECT id, investigation_id, url_final, sha256, mime, tamano, ruta, estado_http, fetched_at, cabeceras "
            "FROM capture WHERE id = %s",
            (cid,),
        ).fetchone()
        if fila is None:
            raise HTTPException(404)
        return fila

    @app.get("/capturas/{cid}", response_class=HTMLResponse)
    def captura(
        request: Request,
        cid: str,
        pagina: int = 1,
        version: str = "original",
        s=Depends(sesion),
        conn=Depends(conexion),
    ):
        c = _captura(conn, cid)
        archivo = evidencia.ruta_absoluta(cfg.evidencia_dir, c[3])
        integra = archivo.exists() and evidencia.hash_archivo(archivo) == c[3]
        ext = conn.execute(
            "SELECT id, extractor, texto, paginas FROM extraction WHERE capture_id = %s ORDER BY created_at DESC LIMIT 1",
            (cid,),
        ).fetchone()
        pii = (
            conn.execute(
                "SELECT p.nivel, p.hallazgos, p.texto_redactado, p.mapa, p.paginas_redactadas FROM pii_scan p "
                "WHERE p.extraction_id = %s ORDER BY p.created_at DESC LIMIT 1",
                (ext[0],),
            ).fetchone()
            if ext
            else None
        )
        texto, paginas = None, []
        if ext and pii:
            usar_redactado = version == "redactado"
            fuente = pii[2] if usar_redactado else ext[2]
            paginas = pii[4] if usar_redactado else ext[3]
            pag = next((p for p in paginas if p["n"] == pagina), paginas[0] if paginas else None)
            texto = fuente[pag["inicio"] : pag["fin"]] if pag else ""
        citas = conn.execute(
            "SELECT texto_citado, pagina, estado, verified, similitud, inicio, fin FROM citation WHERE capture_id = %s ORDER BY created_at",
            (cid,),
        ).fetchall()
        return render(
            request,
            "captura.html",
            s,
            c=c,
            integra=integra,
            ext=ext,
            pii=pii,
            texto=texto,
            paginas=paginas,
            pagina=pagina,
            version=version,
            citas=citas,
        )

    @app.get("/capturas/{cid}/archivo")
    def descargar(cid: str, s=Depends(sesion), conn=Depends(conexion)):
        c = _captura(conn, cid)
        archivo = evidencia.ruta_absoluta(cfg.evidencia_dir, c[3])
        if not archivo.exists():
            raise HTTPException(404, "archivo ausente")
        auditoria.registrar(conn, "daniel", "capture.descargar", f"capture/{cid}", {"sha256": c[3]})
        # Contenido no confiable: siempre como descarga opaca, nunca interpretado por el navegador.
        return FileResponse(
            archivo,
            media_type="application/octet-stream",
            filename=f"{c[3][:12]}.bin",
            headers={"Content-Disposition": f'attachment; filename="{c[3][:12]}.bin"'},
        )

    @app.post("/capturas/{cid}/verificar", response_class=HTMLResponse)
    def verificar_hash(request: Request, cid: str, csrf: str = Form(), s=Depends(sesion), conn=Depends(conexion)):
        csrf_ok(s, csrf)
        c = _captura(conn, cid)
        archivo = evidencia.ruta_absoluta(cfg.evidencia_dir, c[3])
        ok = archivo.exists() and evidencia.hash_archivo(archivo) == c[3]
        auditoria.registrar(conn, "daniel", "capture.verificar", f"capture/{cid}", {"integra": ok})
        return render(request, "_integridad.html", s, integra=ok, sha=c[3])

    # --- bandeja de aprobaciones -----------------------------------------------------------------------------------

    @app.get("/aprobaciones", response_class=HTMLResponse)
    def bandeja(request: Request, s=Depends(sesion), conn=Depends(conexion)):
        decididas = conn.execute(
            "SELECT decided_at, objeto_tipo, objeto_id, decision, used_at FROM approval ORDER BY decided_at DESC LIMIT 20"
        ).fetchall()
        return render(
            request,
            "aprobaciones.html",
            s,
            pendientes=aprobaciones.pendientes(conn),
            decididas=decididas,
            tipos=aprobaciones.TIPOS,
        )

    def _objeto(conn, tipo: str, oid: uuid.UUID) -> aprobaciones.Objeto:
        if tipo not in aprobaciones.TIPOS:
            raise HTTPException(404)
        obj = aprobaciones.cargar(conn, tipo, str(oid))
        if obj is None:
            raise HTTPException(404)
        return obj

    @app.get("/aprobaciones/{tipo}/{oid}", response_class=HTMLResponse)
    def ver_aprobacion(request: Request, tipo: str, oid: uuid.UUID, s=Depends(sesion), conn=Depends(conexion)):
        obj = _objeto(conn, tipo, oid)
        previas = conn.execute(
            "SELECT decided_at, decision, used_at, comentario FROM approval WHERE objeto_tipo = %s AND objeto_id = %s "
            "ORDER BY decided_at",
            (tipo, str(oid)),
        ).fetchall()
        envio = None
        if tipo == "request":
            envio = conn.execute(
                "SELECT r.estado, r.enviada_at, coalesce(k.correo, k.url_portal), k.canal, r.asunto, r.cuerpo "
                "FROM request r LEFT JOIN organization_contact k ON k.id = r.contact_id WHERE r.id = %s",
                (str(oid),),
            ).fetchone()
        return render(
            request, "aprobacion.html", s, obj=obj, tipos=aprobaciones.TIPOS, previas=previas, error=None, envio=envio
        )

    @app.post("/aprobaciones/{tipo}/{oid}", response_class=HTMLResponse)
    def decidir(
        request: Request,
        tipo: str,
        oid: uuid.UUID,
        content_hash: str = Form(min_length=64, max_length=64),
        decision: str = Form(pattern="^(aprobada|rechazada)$"),
        totp: str = Form(max_length=10),
        comentario: str = Form("", max_length=2000),
        usuario: str = Form("", max_length=100),  # solo para que el gestor de contraseñas asocie el código
        csrf: str = Form(),
        s=Depends(sesion),
        conn=Depends(conexion),
    ):
        csrf_ok(s, csrf)
        obj = _objeto(conn, tipo, oid)

        def error(msg: str, estado: int) -> HTMLResponse:
            resp = render(
                request, "aprobacion.html", s, obj=obj, tipos=aprobaciones.TIPOS, previas=[], error=msg, envio=None
            )
            resp.status_code = estado
            return resp

        clave = f"aprobar:{s['id']}"
        if cfg.limitador.bloqueado(clave):
            return error("Demasiados códigos incorrectos. Espera unos minutos.", 429)
        # TOTP en CADA aprobación: un código nuevo (paso posterior al último usado, también al del login).
        ultimo = conn.execute("SELECT totp_ultimo_paso FROM app_user WHERE id = %s", (s["id"],)).fetchone()[0]  # type: ignore[index]
        paso = auth.paso_totp(cfg.totp_secreto, totp, ultimo)
        if (
            paso is None
            or conn.execute(
                "UPDATE app_user SET totp_ultimo_paso = %s WHERE id = %s AND totp_ultimo_paso < %s",
                (paso, s["id"], paso),
            ).rowcount
            != 1
        ):
            cfg.limitador.fallo(clave)
            auditoria.registrar(conn, "daniel", "approval.totp_fallo", f"{tipo}/{oid}", {})
            return error(
                "Código incorrecto o ya usado. Cada aprobación pide un código nuevo (espera al siguiente).", 401
            )
        cfg.limitador.exito(clave)
        try:
            aprobaciones.decidir(conn, obj, content_hash, decision, paso, comentario)
        except aprobaciones.ContenidoCambiado as e:
            return error(str(e), 409)
        return RedirectResponse("/aprobaciones", status_code=303)

    @app.post("/solicitudes/{rid}/enviada")
    def registrar_envio(rid: uuid.UUID, csrf: str = Form(), s=Depends(sesion), conn=Depends(conexion)):
        """Daniel envió a mano desde su correo el contenido aprobado. Consume la aprobación (uso único)."""
        csrf_ok(s, csrf)
        f = conn.execute(
            "SELECT r.content_hash, a.id FROM request r JOIN approval a ON a.objeto_tipo = 'request' "
            "AND a.objeto_id = r.id AND a.decision = 'aprobada' AND a.used_at IS NULL WHERE r.id = %s "
            "AND r.estado = 'aprobada'",
            (str(rid),),
        ).fetchone()
        if f is None:
            raise HTTPException(409, "La solicitud no está aprobada o su aprobación ya se usó.")
        with conn.transaction():
            motivo = conn.execute(
                "SELECT approval_usar(%s, 'request', %s, %s, 'daniel')", (f[1], str(rid), f[0])
            ).fetchone()[0]  # type: ignore[index]
            if motivo is not None:
                raise HTTPException(409, f"No se pudo usar la aprobación: {motivo}")
            conn.execute(
                "UPDATE request SET estado = 'enviada', enviada_at = now(), enviada_por = 'manual' WHERE id = %s",
                (str(rid),),
            )
            auditoria.registrar(conn, "daniel", "solicitud.enviada_manual", f"request/{rid}", {"hash": f[0]})
        return RedirectResponse(f"/aprobaciones/request/{rid}", status_code=303)

    # --- auditoría ------------------------------------------------------------------------------------------------

    @app.get("/auditoria", response_class=HTMLResponse)
    def ver_auditoria(request: Request, s=Depends(sesion), conn=Depends(conexion)):
        r = auditoria.verificar(conn)
        ultimas = conn.execute(
            "SELECT seq, ts, actor, accion, objeto FROM audit_log ORDER BY seq DESC LIMIT 50"
        ).fetchall()
        problemas = capture.verificar_capturas(conn, cfg.evidencia_dir)
        return render(request, "auditoria.html", s, cadena=r, ultimas=ultimas, problemas=problemas)

    return app
