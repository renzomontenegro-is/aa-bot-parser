"""
export_bot.py — Exporta un bot de Automation Anywhere A360 via Control Room API.

Capacidad reutilizable del proyecto AA Bot Parser. Dado un fileId (o nombre) de un
bot en el Control Room:
  1. autentica (POST /v2/authentication -> token JWT, ~20 min)
  2. resuelve el nombre del bot desde el fileId (y lo verifica si pasas --name)
  3. lanza el export asincrono (POST /v2/blm/export). Dependencias (sub-bots, .py,
     archivos referenciados) y screenshots del Recorder van SIEMPRE (la API no
     tiene flag para excluirlos: son contenido del bot, no un paquete). Los
     packages (.jar de runtime) NO van por defecto: el parser no los lee y pesan
     GB. Se activan con --with-packages. Los .png del Recorder se purgan DESPUES
     de descomprimir (--keep-screenshots para conservarlos).
  4. hace polling del estado con timeout (GET /v2/blm/status/{requestId})
  5. descarga el zip (GET /v2/blm/download/{downloadFileId})
  6. lo descomprime en bots/<nombre>/ (estructura que consume parse_bot.py) y borra el zip

Endpoints y fuentes: knowledge/api_notes.md.

------------------------------------------------------------------------------
COMO EXPORTAR OTRO BOT
------------------------------------------------------------------------------
  Si YA tienes el fileId (forma mas corta; el nombre se resuelve solo):
      python tools/export_bot.py --export <fileId>
    Ejemplo (liviano, ~segundos, solo lo que el parser necesita):
      python tools/export_bot.py --export 11625

  Por defecto NO baja los packages (.jar). Para incluirlos (pesa GB, rara vez
  necesario porque el parser no los lee):
      python tools/export_bot.py --export 11625 --with-packages

  Si quieres ademas verificar que el fileId es el bot que crees (no exporta si
  el nombre no coincide; te muestra candidatos):
      python tools/export_bot.py --export <fileId> --name <NombreExactoDelBot>

  Si NO sabes el fileId, buscalo por nombre y copia el id:
      python tools/export_bot.py --search RP045

  Solo verificar a que bot corresponde un fileId (no exporta nada):
      python tools/export_bot.py --check <fileId>

  Bots grandes: correr en background sin buffer para ver el progreso:
      PYTHONUNBUFFERED=1 python -u tools/export_bot.py --export <id> > exp.log 2>&1 &

  El fileId sale de la URL del Creator:
      /bots/repository/public/files/task/<fileId>/view
------------------------------------------------------------------------------

Credenciales: NO van en el codigo. Se leen de un archivo `.env` en la raiz del repo
(gitignoreado). Copia `.env.example` a `.env` y completa AA_CR_BASE / AA_CR_USER /
AA_CR_KEY. Asi el parser se puede compartir sin filtrar la API key.
"""

import sys
import os
import time
import shutil
import argparse
import zipfile
from pathlib import Path

try:
    import requests
    import urllib3
except ImportError:
    # El mensaje lleva sys.executable a proposito: el error mas comun es tener
    # varios Python y haber instalado en el otro.
    raise SystemExit(
        "\nFalta 'requests' (solo hace falta para descargar del Control Room).\n\n"
        "    {} -m pip install -r requirements.txt\n".format(sys.executable))

# raiz del repo = carpeta padre de tools/
ROOT = Path(__file__).resolve().parent.parent


def _load_env():
    """Carga ROOT/.env (lineas KEY=VALUE) a os.environ. Sin dependencias externas
    (no requiere python-dotenv). Las variables ya presentes en el entorno ganan."""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env()

# ---------------------------------------------------------------------------
# CONFIG — credenciales desde .env (ver .env.example); resto ajustable aqui
# ---------------------------------------------------------------------------
BASE = os.environ.get("AA_CR_BASE", "")   # URL del Control Room
USER = os.environ.get("AA_CR_USER", "")   # si el CR usa dominio: "dominio\\usuario"
KEY = os.environ.get("AA_CR_KEY", "")     # API key del Control Room

WORKSPACE = os.environ.get("AA_CR_WORKSPACE", "public")  # bots productivos del CR
VERIFY_SSL = False                # CR interno con cert propio; True si tienes la CA
TOKEN_TTL = 20 * 60              # vigencia nominal del token (s)
RENEW_MARGIN = 120               # renovar si faltan < 2 min para expirar
POLL_INTERVAL = 5                # s entre consultas de estado
POLL_TIMEOUT = 15 * 60          # corte total del polling (s)
DOWNLOAD_TIMEOUT = 600          # s para la descarga del zip

if VERIFY_SSL is False:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

S = requests.Session()
S.verify = VERIFY_SSL


class ApiError(SystemExit):
    """Error accionable: se imprime y corta el programa con mensaje claro."""


def _explain(resp, contexto):
    """Traduce un codigo HTTP a una accion concreta (nunca imprime la API key)."""
    code = resp.status_code
    cuerpo = (resp.text or "")[:300]
    if code == 401:
        return (f"401 en {contexto}: token o API key invalido/expirado. Revisa USER "
                f"y KEY en el bloque CONFIG. Si el CR usa dominio, prueba "
                f"USER='dominio\\\\usuario'. Detalle: {cuerpo}")
    if code == 403:
        return (f"403 en {contexto}: faltan permisos sobre esa carpeta. Pide al admin "
                f"del Control Room los permisos 'Export bots', 'View package' y "
                f"'Check in or Check out'. Detalle: {cuerpo}")
    if code == 404:
        return (f"404 en {contexto}: recurso inexistente (fileId/downloadFileId), o el "
                f"path del repositorio tiene otra capitalizacion (es case-sensitive). "
                f"Detalle: {cuerpo}")
    if code == 429:
        return f"429 en {contexto}: rate limiting. Reintenta en unos segundos."
    return f"{code} en {contexto}: {cuerpo}"


# ---------------------------------------------------------------------------
# Autenticacion con renovacion
# ---------------------------------------------------------------------------
class Token:
    """Maneja el JWT y lo renueva antes de que expire durante un export largo."""

    def __init__(self):
        self._value = None
        self._issued = 0.0

    def _authenticate(self):
        if not (BASE and USER and KEY):
            raise ApiError("Faltan credenciales del Control Room. Crea un archivo .env en "
                           "la raiz del repo (copia .env.example) con AA_CR_BASE, AA_CR_USER "
                           "y AA_CR_KEY.")
        r = S.post(f"{BASE}/v2/authentication",
                   json={"username": USER, "apiKey": KEY}, timeout=30)
        if r.status_code != 200:
            raise ApiError(_explain(r, "POST /v2/authentication"))
        tok = r.json().get("token")
        if not tok:
            raise ApiError("Autenticacion sin 'token' en la respuesta. "
                           "Revisa la URL base del Control Room.")
        self._value = tok
        self._issued = time.time()
        print("[ok] Autenticado (token vigente ~20 min).")

    def header(self, content_json=True):
        """Devuelve headers con X-Authorization; renueva el token si esta por vencer."""
        if self._value is None or (time.time() - self._issued) > (TOKEN_TTL - RENEW_MARGIN):
            self._authenticate()
        h = {"X-Authorization": self._value}
        if content_json:
            h["Content-Type"] = "application/json"
        return h


# ---------------------------------------------------------------------------
# Repositorio: verificar / buscar
# ---------------------------------------------------------------------------
def _files_list(token, body):
    r = S.post(f"{BASE}/v2/repository/workspaces/{WORKSPACE}/files/list",
               headers=token.header(), json=body, timeout=30)
    if r.status_code != 200:
        raise ApiError(_explain(r, f"POST /v2/repository/workspaces/{WORKSPACE}/files/list"))
    return r.json().get("list", [])


def get_file_metadata(token, file_id):
    items = _files_list(token, {
        "filter": {"operator": "eq", "field": "id", "value": str(file_id)}})
    return items[0] if items else None


def search_by_name(token, name):
    return _files_list(token, {
        "filter": {"operator": "substring", "field": "name", "value": name},
        "sort": [{"field": "name", "direction": "asc"}]})


def verify_file(token, file_id, expected_name):
    """Confirma que file_id == expected_name. Devuelve (file_id_final, metadata)."""
    meta = get_file_metadata(token, file_id)
    if meta and expected_name.lower() in (meta.get("name") or "").lower():
        print(f"[ok] fileId {file_id} = '{meta['name']}' (tipo {meta.get('type')})")
        print(f"     path: {meta.get('path')}")
        return str(file_id), meta

    if meta:
        print(f"[!] fileId {file_id} existe pero es '{meta.get('name')}', "
              f"no '{expected_name}'. Busco por nombre...")
    else:
        print(f"[!] fileId {file_id} no existe en workspace '{WORKSPACE}'. "
              f"Busco por nombre...")

    candidatos = search_by_name(token, expected_name)
    if not candidatos:
        raise ApiError(f"No encontre ningun bot que contenga '{expected_name}' en "
                       f"workspace '{WORKSPACE}'. Verifica el nombre o el workspace.")
    print(f"[i] {len(candidatos)} candidato(s) por nombre '{expected_name}':")
    for c in candidatos:
        print(f"     id={c.get('id')}  name='{c.get('name')}'  path={c.get('path')}")
    exacto = [c for c in candidatos
              if (c.get("name") or "").lower() == expected_name.lower()]
    elegido = exacto[0] if exacto else candidatos[0]
    print(f"[ok] Uso fileId {elegido.get('id')} = '{elegido.get('name')}'.")
    return str(elegido.get("id")), elegido


# ---------------------------------------------------------------------------
# Export asincrono
# ---------------------------------------------------------------------------
def export_request(token, file_id, export_name, include_packages=False):
    # DEPENDENCIAS (sub-bots, .py, archivos referenciados) van SIEMPRE con el bot
    # padre, sin opcion: son parte del contenido del bot. Los SCREENSHOTS del
    # Recorder (*Metadata/*.png) tambien viajan con los bots (verificado: un export
    # sin packages de RP039 trae los 118 png) y NO hay flag en la API para
    # excluirlos: se purgan despues de descomprimir. includePackages solo controla
    # los .jar de runtime, que el parser NUNCA lee y que pesan GB (2 recorders =
    # 2.28 GB en RP039). Default: False.
    body = {
        "name": export_name,
        "fileIds": [str(file_id)],
        "includePackages": bool(include_packages),
        # archivePassword omitido => zip sin contrasena (parse_bot lo lee directo)
    }
    r = S.post(f"{BASE}/v2/blm/export", headers=token.header(), json=body, timeout=60)
    if r.status_code not in (200, 202):
        raise ApiError(_explain(r, "POST /v2/blm/export"))
    data = r.json()
    request_id = data.get("requestId") or data.get("id")
    if not request_id:
        raise ApiError(f"Export aceptado pero sin requestId: {str(data)[:300]}")
    pkg = "con packages (.jar)" if body["includePackages"] else "sin packages (solo bots + dependencias + screenshots)"
    print(f"[ok] Export lanzado {pkg}. requestId = {request_id}")
    return request_id


def poll_status(token, request_id):
    """Consulta el estado hasta COMPLETED/FAILED o timeout. Devuelve downloadFileId."""
    deadline = time.time() + POLL_TIMEOUT
    ultimo = None
    while time.time() < deadline:
        r = S.get(f"{BASE}/v2/blm/status/{request_id}",
                  headers=token.header(content_json=False), timeout=30)
        if r.status_code != 200:
            raise ApiError(_explain(r, f"GET /v2/blm/status/{request_id}"))
        data = r.json()
        estado = (data.get("status") or "?").upper()
        if estado != ultimo:
            print(f"  [{int(time.time() - (deadline - POLL_TIMEOUT))}s] estado: {estado}")
            ultimo = estado
        if estado == "COMPLETED":
            download_id = data.get("downloadFileId") or request_id
            print(f"[ok] Export COMPLETED. downloadFileId = {download_id}")
            return download_id
        if estado in ("FAILED", "ERROR"):
            raise ApiError(f"Export FALLIDO (status={estado}): {str(data)[:400]}")
        time.sleep(POLL_INTERVAL)
    raise ApiError(f"Timeout ({POLL_TIMEOUT}s) esperando el export. "
                   f"Ultimo estado: {ultimo}. Reintenta o sube POLL_TIMEOUT.")


def _fmt_mb(n):
    return f"{n / 1e6:.1f} MB"


def _long(path):
    r"""Windows: antepone el prefijo \\?\ para superar el limite MAX_PATH (260
    chars). Los exports de AA anidan rutas larguisimas (Bots/Area/Nombre largo/
    Dependencias/<sub>Metadata/<uuid>.png) que revientan zipfile/open sin esto.
    Sin efecto en otros SO."""
    if os.name == "nt":
        p = os.path.abspath(str(path))
        return p if p.startswith("\\\\?\\") else "\\\\?\\" + p
    return str(path)


def _progress(done, total, started):
    """Dibuja el avance de la descarga. Barra con \\r si hay TTY; linea si no."""
    elapsed = max(time.time() - started, 1e-6)
    speed = done / elapsed
    tty = sys.stderr.isatty()
    if total > 0:
        pct = done / total
        eta = (total - done) / speed if speed else 0
        if tty:
            barra = "#" * int(pct * 30) + "-" * (30 - int(pct * 30))
            sys.stderr.write(
                f"\r  [{barra}] {pct*100:5.1f}%  {_fmt_mb(done)}/{_fmt_mb(total)}  "
                f"{speed/1e6:.1f} MB/s  ETA {int(eta)}s   ")
            sys.stderr.flush()
        else:
            print(f"  descarga {pct*100:5.1f}%  {_fmt_mb(done)}/{_fmt_mb(total)}  "
                  f"{speed/1e6:.1f} MB/s  ETA {int(eta)}s", flush=True)
    else:  # sin Content-Length: solo bytes acumulados
        msg = f"  descargado {_fmt_mb(done)}  {speed/1e6:.1f} MB/s"
        if tty:
            sys.stderr.write("\r" + msg + "   "); sys.stderr.flush()
        else:
            print(msg, flush=True)


def download_zip(token, download_id, dest_zip):
    r = S.get(f"{BASE}/v2/blm/download/{download_id}",
              headers=token.header(content_json=False), stream=True,
              timeout=DOWNLOAD_TIMEOUT)
    if r.status_code != 200:
        raise ApiError(_explain(r, f"GET /v2/blm/download/{download_id}"))
    total = int(r.headers.get("Content-Length", 0))
    done = 0
    started = time.time()
    tty = sys.stderr.isatty()
    next_tick = 0  # para modo sin-TTY: siguiente umbral (%) a imprimir
    print(f"[i] Descargando {_fmt_mb(total) if total else '(tamano desconocido)'} ...")
    with open(dest_zip, "wb") as fh:
        for chunk in r.iter_content(1024 * 256):  # 256 KB
            if not chunk:
                continue
            fh.write(chunk)
            done += len(chunk)
            if tty:
                _progress(done, total, started)
            elif total and (done / total) * 100 >= next_tick:
                _progress(done, total, started)
                next_tick += 10  # una linea cada ~10% en el log
    if tty:
        sys.stderr.write("\n"); sys.stderr.flush()
    print(f"[ok] Descargado: {dest_zip.name} ({_fmt_mb(dest_zip.stat().st_size)})")
    if not zipfile.is_zipfile(dest_zip):
        raise ApiError("El archivo descargado no es un zip valido (¿zip con "
                       "contrasena, o el endpoint devolvio otro contenido?).")


def _extract_progress(idx, total, done_bytes, total_bytes, started):
    """Avance de la descompresion. Barra con \\r si hay TTY; linea cada ~10% si no."""
    elapsed = max(time.time() - started, 1e-6)
    pct = (done_bytes / total_bytes) if total_bytes else (idx / total if total else 0)
    speed = done_bytes / elapsed
    eta = (total_bytes - done_bytes) / speed if speed else 0
    if sys.stderr.isatty():
        barra = "#" * int(pct * 30) + "-" * (30 - int(pct * 30))
        sys.stderr.write(
            f"\r  [{barra}] {pct*100:5.1f}%  {idx}/{total} archivos  "
            f"{_fmt_mb(done_bytes)}/{_fmt_mb(total_bytes)}  ETA {int(eta)}s   ")
        sys.stderr.flush()
    else:
        print(f"  descompresion {pct*100:5.1f}%  {idx}/{total} archivos  "
              f"{_fmt_mb(done_bytes)}/{_fmt_mb(total_bytes)}", flush=True)


def extract_zip(dest_zip, out_dir):
    # Limpiar la carpeta destino antes de extraer: si un export anterior dejo
    # archivos (p.ej. .jar de un --with-packages previo, o bots que ya no existen),
    # no deben quedar fantasma. El contenido debe reflejar exactamente el zip.
    # Extraccion MANUAL con prefijo \\?\ (_long): zipfile.extract() usa open() sin
    # ese prefijo y revienta con rutas > 260 chars (MAX_PATH) en exports anidados.
    if out_dir.exists():
        shutil.rmtree(_long(out_dir), ignore_errors=True)
    os.makedirs(_long(out_dir), exist_ok=True)
    tty = sys.stderr.isatty()
    with zipfile.ZipFile(dest_zip) as z:
        infos = z.infolist()
        total = len(infos)
        total_bytes = sum(i.file_size for i in infos)
        done_bytes = 0
        started = time.time()
        next_tick = 0  # umbral (%) para el modo sin-TTY
        print(f"[i] Descomprimiendo {total} archivos ({_fmt_mb(total_bytes)}) ...")
        for idx, info in enumerate(infos, 1):
            target = os.path.join(str(out_dir), *info.filename.split("/"))
            if info.is_dir():
                os.makedirs(_long(target), exist_ok=True)
            else:
                os.makedirs(_long(os.path.dirname(target)), exist_ok=True)
                with z.open(info) as src, open(_long(target), "wb") as dst:
                    shutil.copyfileobj(src, dst)
            done_bytes += info.file_size
            pct = (done_bytes / total_bytes * 100) if total_bytes else 0
            if tty:
                _extract_progress(idx, total, done_bytes, total_bytes, started)
            elif pct >= next_tick:
                _extract_progress(idx, total, done_bytes, total_bytes, started)
                next_tick += 10
    if tty:
        sys.stderr.write("\n"); sys.stderr.flush()
    print(f"[ok] Descomprimido en: {out_dir}")
    return out_dir


def purge_screenshots(export_dir):
    """Borra las capturas del Recorder que AA empaqueta con cada taskbot
    (carpetas <bot>Metadata/*.png). El parser NUNCA las lee: el objeto de UI
    vive decodificado dentro del JSON del bot; el .png es solo la imagen, y por
    eso tambien son casi todo el peso y la mayoria de los archivos del export
    (en RP149: 405 de 440). Se conserva TODO lo demas, incluido manifest.json,
    que el descubrimiento usa para contrastar. Devuelve (png_borrados,
    carpetas_vaciadas).

    Conservador a proposito: se borran SOLO .png, y las carpetas *Metadata solo
    se eliminan si quedaron vacias. Si una version futura de AA metiera algo mas
    ahi, no se pierde nada: la carpeta queda y solo se reporta en el conteo."""
    base = _long(export_dir)
    pngs = []
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            if name.lower().endswith(".png"):
                pngs.append(os.path.join(dirpath, name))
    for p in pngs:
        try:
            os.remove(p)
        except OSError:
            pass
    vaciadas = 0
    metadata_dirs = [os.path.join(dirpath, d)
                     for dirpath, dirs, _ in os.walk(base)
                     for d in dirs if d.endswith("Metadata")]
    for md in metadata_dirs:
        # De adentro hacia afuera, y solo carpetas vacias: si dentro de un
        # *Metadata hubiera algo que no sea .png, nada de esto lo toca.
        for dirpath, dirs, _files in os.walk(md, topdown=False):
            for d in dirs:
                try:
                    os.rmdir(os.path.join(dirpath, d))
                    vaciadas += 1
                except OSError:
                    pass
        try:
            os.rmdir(md)
            vaciadas += 1
        except OSError:
            pass
    return len(pngs), vaciadas


def _safe_name(name):
    keep = "-_. "
    return "".join(c for c in name if c.isalnum() or c in keep).strip().replace(" ", "_")


# ---------------------------------------------------------------------------
# Descubrimiento (fallback si /v2/blm/* respondiera 404 en este CR)
# ---------------------------------------------------------------------------
def discover(token):
    for path in ["/swagger/v2/api-docs", "/v2/api-docs", "/swagger-resources"]:
        try:
            r = S.get(f"{BASE}{path}", headers=token.header(content_json=False), timeout=30)
        except Exception:
            continue
        if r.status_code == 200 and '"paths"' in r.text[:4000]:
            paths = r.json().get("paths", {})
            print(f"\n[Swagger en {path}] endpoints relevantes:\n")
            for p, methods in sorted(paths.items()):
                if any(k in p.lower() for k in
                       ("export", "import", "blm", "lifecycle", "download", "repository")):
                    print(f"  {','.join(m.upper() for m in methods)} {p}")
            return
    print(f"No encontre el Swagger por API. Abre en el navegador: {BASE}/swagger/")


# ---------------------------------------------------------------------------
def run_export(token, file_id, expected_name=None, include_packages=False,
               keep_screenshots=False, announce=True):
    """Exporta, descarga y descomprime. Devuelve la carpeta extraida (Path).
    keep_screenshots=True conserva los .png del Recorder (default: se purgan,
    el parser no los lee). announce=False silencia el epilogo (util al encadenar
    desde main.py)."""
    if expected_name:
        # con --name: verifica que el fileId sea ese bot (no exporta si no coincide)
        file_id, meta = verify_file(token, file_id, expected_name)
    else:
        # sin --name: basta el fileId; el nombre se resuelve desde el Control Room
        meta = get_file_metadata(token, file_id)
        if not meta:
            raise ApiError(f"fileId {file_id} no existe en workspace '{WORKSPACE}'. "
                           f"Usa --search <nombre> para encontrarlo.")
        file_id = str(meta.get("id"))
        print(f"[ok] fileId {file_id} = '{meta.get('name')}' (tipo {meta.get('type')})")
        print(f"     path: {meta.get('path')}")

    bot_name = meta.get("name") or f"bot_{file_id}"
    export_name = f"export_{_safe_name(bot_name)}_{int(time.time())}"
    request_id = export_request(token, file_id, export_name, include_packages)
    download_id = poll_status(token, request_id)

    # Una carpeta por bot: Bots/<bot>/. Los archivos AA van en Bots/<bot>/export/.
    # El zip es un intermedio: se descarga en la carpeta del bot, se descomprime y
    # se borra (no debe quedar en la raiz).
    out_name = _safe_name(bot_name)
    perbot = ROOT / "Bots" / out_name
    perbot.mkdir(parents=True, exist_ok=True)
    dest_zip = perbot / f"{out_name}.zip"
    out_dir = perbot / "export"
    download_zip(token, download_id, dest_zip)
    extract_zip(dest_zip, out_dir)
    dest_zip.unlink(missing_ok=True)  # el zip se descarta; queda solo la carpeta

    if not keep_screenshots:
        n_png, n_dir = purge_screenshots(out_dir)
        print(f"[i] Screenshots del Recorder purgados: {n_png} .png en "
              f"{n_dir} carpeta(s) *Metadata (el parser no las lee; "
              f"--keep-screenshots para conservarlas).")

    if announce:
        print("\n== LISTO ==")
        print(f"Carpeta del bot:  {perbot}")
        print("Estructura: Bots/<bot>/export/Automation Anywhere/Bots/...")
        print("El parser NO se corrio automaticamente: corre el pipeline")
        print(f"(exporter/main.py --export \"{perbot}\") cuando quieras procesarlo.")
    return out_dir


def main():
    ap = argparse.ArgumentParser(description="Export de bots AA A360 via Control Room API.")
    ap.add_argument("--check", metavar="FILE_ID", help="verificar a que bot corresponde el fileId")
    ap.add_argument("--search", metavar="NOMBRE", help="buscar bots por nombre")
    ap.add_argument("--export", metavar="FILE_ID", help="exportar, descargar y descomprimir")
    ap.add_argument("--name", default=None,
                    help="opcional: nombre esperado del bot. Si lo pasas, verifica "
                         "que el fileId corresponda a ese bot antes de exportar. "
                         "Si lo omites, el nombre se resuelve desde el fileId.")
    ap.add_argument("--with-packages", action="store_true",
                    help="incluir los .jar de packages en el export (default: NO). "
                         "El parser no los lee y pesan GB (2 recorders = 2.28 GB en "
                         "RP039). Solo actívalo si necesitas los binarios del runtime.")
    ap.add_argument("--keep-screenshots", action="store_true",
                    help="conservar los .png del Recorder en el export (default: "
                         "se purgan despues de descomprimir, el parser no los lee).")
    ap.add_argument("--discover", action="store_true", help="listar endpoints BLM del Swagger")
    a = ap.parse_args()

    token = Token()

    if a.discover:
        discover(token)
    elif a.check:
        meta = get_file_metadata(token, a.check)
        if meta:
            print(f"[ok] fileId {a.check} = '{meta.get('name')}' (tipo {meta.get('type')})")
            print(f"     path: {meta.get('path')}")
        else:
            print(f"[!] fileId {a.check} no existe en workspace '{WORKSPACE}'.")
    elif a.search:
        for c in search_by_name(token, a.search):
            print(f"  id={c.get('id')}  name='{c.get('name')}'  path={c.get('path')}")
    elif a.export:
        run_export(token, a.export, a.name, include_packages=a.with_packages,
                   keep_screenshots=a.keep_screenshots)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
