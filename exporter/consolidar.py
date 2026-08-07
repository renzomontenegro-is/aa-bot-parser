#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
consolidar.py - Junta los archivos SIN EXTENSION de un export de AA en UN solo
archivo, ordenado por orden de llamada, SIN PERDER NADA.

Por que existe
--------------
Todo codigo que decide que emitir a partir de una lista de claves/tipos/comandos
esta implicitamente calibrado contra los bots que su autor ya vio. El primer bot
con un paquete nuevo lo rompe en silencio. Este paso no decide nada: copia el
JSON completo de cada taskbot, indentado, uno detras de otro.

Garantia (verificable, no prometida)
------------------------------------
El archivo de salida es REVERSIBLE: cada bloque vuelve a json.loads() y es
igual, objeto por objeto, al archivo original. Eso lo comprueba --verificar
(activo por defecto) releyendo la salida y comparando contra los originales.
No hay lista de claves que revisar: si el round-trip da igual, no se perdio
nada, y eso vale para cualquier bot futuro.

Que NO hace (a proposito): no poda, no redacta, no decodifica los blob base64,
no traduce comandos. Las transformaciones son el paso siguiente y van sobre
este archivo, no sobre el export.

Orden de las secciones
----------------------
1. El bot de entrada (el taskbot al que nadie mas llama).
2. Los sub-bots en orden de PRIMERA llamada, recorriendo runTask en profundidad.
3. Si nadie llama a mas de uno, se emiten todos y se AVISA: el export solo no
   alcanza para saber cual es la entrada, y adivinar desordena todo en silencio.
Un sub-bot llamado N veces aparece UNA sola vez, con el conteo de llamadas.

Uso
---
    python exporter/consolidar.py bots/RP053_ProcesoPagoVentanillaIBK
    python exporter/consolidar.py bots/<bot> --out ruta/salida.txt
    python exporter/consolidar.py bots/<bot> --sin-verificar
"""
import argparse
import base64
import io
import json
import os
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Marcadores de seccion. Tienen que ser reconocibles sin ambiguedad para que el
# verificador pueda volver a partir el archivo. Van a principio de linea y
# ningun JSON indentado puede producirlos.
MARCA_INI = "===== BOT "
MARCA_FIN = "===== FIN BOT "
SEP = "=" * 12

# Frontera dentro de la cabecera. Arriba va la descripcion de QUE ES este
# archivo (la escribe el paso que lo genera); abajo, los datos del bot (indice,
# grafo de llamadas, avisos), que todos los pasos heredan igual.
#
# Existe porque el limpiador heredaba la cabecera entera y el archivo limpio
# terminaba afirmando "el JSON esta COMPLETO y sin modificar", que ya era falso.
# La alternativa era buscar esa frase por su texto y reemplazarla: eso se rompe
# en silencio en cuanto alguien reescribe la prosa. Con la marca, un paso nuevo
# que se olvide de poner su descripcion produce un archivo sin descripcion, que
# se ve; no uno que miente, que no se ve.
MARCA_DESC = "----- fin de la descripcion del paso -----"


def _long(path):
    r"""Windows: prefijo \\?\ para pasar MAX_PATH. Los exports de AA anidan
    rutas larguisimas (Bots/Area/Nombre largo/Dependencias/<sub>Metadata/...)."""
    if os.name == "nt":
        p = os.path.abspath(str(path))
        return p if p.startswith("\\\\?\\") else "\\\\?\\" + p
    return str(path)


def leer_texto(path):
    """Devuelve (texto, encoding_usado). Si un archivo no es UTF-8 limpio se
    reporta el encoding que si funciono, en vez de reemplazar bytes en silencio:
    un caracter perdido en un nombre de variable es una perdida como cualquier
    otra."""
    with open(_long(path), "rb") as fh:
        raw = fh.read()
    # latin-1 mapea los 256 bytes, asi que NUNCA falla: es el ultimo recurso real
    # y por eso no hace falta (ni existe) un fallback con reemplazos despues.
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue


# --- 1. Descubrimiento -------------------------------------------------------
def descubrir(export_dir):
    """Todos los archivos SIN EXTENSION del export. Devuelve tres listas:
    taskbots (JSON con 'nodes'), otros_json (JSON sin 'nodes') y no_json.
    Nada se descarta en silencio: lo que no entra igual se reporta."""
    taskbots, otros_json, no_json = [], [], []
    for dirpath, _dirs, files in os.walk(_long(export_dir)):
        for name in files:
            if os.path.splitext(name)[1]:
                continue
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, _long(export_dir))
            try:
                txt, enc = leer_texto(p)
                d = json.loads(txt)
            except (OSError, ValueError) as e:
                no_json.append((rel, type(e).__name__))
                continue
            if isinstance(d, dict) and "nodes" in d:
                taskbots.append({"rel": rel, "abs": p, "json": d,
                                 "enc": enc, "bytes": len(txt)})
            else:
                otros_json.append((rel, enc, d))
    return taskbots, otros_json, no_json


def contrastar_manifest(export_dir, taskbots):
    """El export trae un manifest.json que declara cada archivo con su
    contentType. Nosotros NO lo usamos para descubrir (la regla es "sin
    extension"), pero si para CONTRASTAR: si el manifest declara un taskbot que
    la regla no levanto, la regla se quedo corta y hay que enterarse.

    Sirve justamente para el caso que la regla no cubre: un bot cuyo nombre
    tenga un punto ('Proceso v1.2') se veria como si tuviera extension y se
    saltaria en silencio. Hoy no pasa en ninguno de los 12 exports."""
    p = os.path.join(export_dir, "manifest.json")
    if not os.path.exists(_long(p)):
        return []
    try:
        man = json.loads(leer_texto(p)[0])
    except ValueError:
        return ["manifest.json existe pero no parsea: no se pudo contrastar el descubrimiento"]
    declarados = {_clave(f.get("path", "")) for f in man.get("files") or []
                  if f.get("contentType") == "application/vnd.aa.taskbot"}
    hallados = {_clave(t["rel"]) for t in taskbots}
    avisos = []
    for falta in sorted(declarados - hallados):
        avisos.append("el manifest declara el taskbot '{}' y el descubrimiento no lo levanto "
                      "(nombre con punto? otra carpeta?). NO esta en este archivo.".format(falta))
    for extra in sorted(hallados - declarados):
        avisos.append("'{}' se levanto como taskbot pero el manifest no lo declara como tal.".format(extra))
    return avisos


# --- 1b. Blobs base64 --------------------------------------------------------
# El recorder guarda QUE control toca el bot como un JSON pasado por base64.
# Codificado no lo entiende nadie y ademas cuesta el triple de tokens (el
# tokenizador no reconoce palabras y lo parte en pedacitos: 1.43 chars/token
# contra 2.95 del texto normal). Decodificarlo es reversible y da -61% de tokens.
#
# Es la UNICA transformacion que hace este paso, y por eso la verificacion de
# round-trip compara contra el export con el MISMO decode aplicado: sigue siendo
# igualdad estructural, sin lista de claves.

def _decodificar(s):
    """JSON dentro de base64 -> objeto. None si no es eso."""
    if not isinstance(s, str) or len(s) < 40:
        return None
    try:
        d = json.loads(base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8"))
    except Exception:
        return None
    return d if isinstance(d, (dict, list)) else None


def decodificar_blobs(o, stats=None, clave=""):
    """Copia del arbol con los blobs decodificados. `stats` acumula:
    ok (decodificados), fallo (clave 'blob' que NO decodifico: se deja igual y
    se avisa) y sospecha (otra clave que parece base64 con JSON adentro: NO se
    toca, solo se reporta, para no decidir a ciegas sobre algo no conocido)."""
    stats = {"ok": 0, "fallo": [], "sospecha": {}} if stats is None else stats
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if isinstance(v, str):
                d = _decodificar(v)
                if k == "blob":
                    if d is None:
                        stats["fallo"].append(k)
                        out[k] = v
                    else:
                        stats["ok"] += 1
                        out[k] = decodificar_blobs(d, stats, k)
                    continue
                if d is not None:
                    stats["sospecha"][k] = stats["sospecha"].get(k, 0) + 1
            out[k] = decodificar_blobs(v, stats, k)
        return out
    if isinstance(o, list):
        return [decodificar_blobs(v, stats, clave) for v in o]
    return o


# --- 2. Grafo de llamadas ----------------------------------------------------
def _iter_nodos(nodos):
    """Todo nodo del arbol: children y branches (y los children de las branches)."""
    for n in nodos or []:
        if not isinstance(n, dict):
            continue
        yield n
        yield from _iter_nodos(n.get("children"))
        for b in n.get("branches") or []:
            if isinstance(b, dict):
                yield b
                yield from _iter_nodos(b.get("children"))


def contar_nodos(d):
    return sum(1 for _ in _iter_nodos(d.get("nodes")))


def _rutas_taskbot(valor, salida):
    """Junta toda ruta de taskbot que cuelgue de un value, sin asumir donde esta.
    En A360 la ruta vive en value.taskbotFile.string, pero se busca por
    estructura (cualquier 'taskbotFile') para no depender del nivel exacto."""
    if isinstance(valor, dict):
        tf = valor.get("taskbotFile")
        if isinstance(tf, dict):
            s = tf.get("string") or tf.get("expression") or ""
            if s:
                salida.append(urllib.parse.unquote(s))
        elif isinstance(tf, str) and tf:
            salida.append(urllib.parse.unquote(tf))
        for v in valor.values():
            _rutas_taskbot(v, salida)
    elif isinstance(valor, list):
        for v in valor:
            _rutas_taskbot(v, salida)


def llamadas_de(d):
    """Rutas de sub-bot invocadas por este taskbot, en orden de aparicion.

    Se mira el nodo ENTERO (menos children/branches, que ya recorre _iter_nodos)
    y no solo `attributes`. Hoy las 955 llamadas del parque viven todas en
    attributes, pero suponerlo no cuesta nada de mas y si AA colgara una ruta de
    otro lado (un returnTo, un campo nuevo) la llamada desapareceria del grafo y
    el sub-bot saldria como huerfano sin que nadie lo note."""
    out = []
    for n in _iter_nodos(d.get("nodes")):
        for k, v in n.items():
            if k not in ("children", "branches"):
                _rutas_taskbot({k: v}, out)
    return out


def _clave(ruta):
    """Normaliza una ruta para cruzarla contra los archivos del export: se
    compara por nombre de archivo, que es lo unico estable entre el
    'repository://...' de la llamada y la ruta relativa del zip."""
    r = ruta.replace("\\", "/").rstrip("/")
    return r.rsplit("/", 1)[-1].lower()


def ordenar(taskbots):
    """Devuelve (ordenados, raices, avisos). Recorrido en profundidad desde la
    raiz, siguiendo el orden en que aparecen los runTask.

    RAIZ = taskbot al que nadie llama. Eso es un hecho del grafo, no una
    suposicion: si nadie lo invoca, el proceso empieza ahi.

    Si hay mas de una raiz (o ninguna, por un ciclo) NO se elige una: se emiten
    todas y se avisa. Cualquier criterio para desempatar seria inventado. El
    primero que se me ocurrio fue "gana el de mas pasos, el orquestador es el
    grande", y medido contra el parque resulto falso: el bot de entrada es el
    MAS CHICO en 11 de 12 casos (RP091 tiene 74 pasos de 1560; RP102, 72 de
    479), porque el orquestador delega. Adivinar mal aqui desordena todo el
    archivo sin que nadie se entere."""
    por_nombre = {}
    for t in taskbots:
        por_nombre.setdefault(_clave(t["rel"]), t)

    # Aristas: quien llama a quien, y cuantas veces.
    for t in taskbots:
        t["llama"] = []
        t["veces_llamado"] = 0
        t["llamado_por"] = []
        t["nodos"] = contar_nodos(t["json"])
        for ruta in llamadas_de(t["json"]):
            dest = por_nombre.get(_clave(ruta))
            t["llama"].append((ruta, dest))

    for t in taskbots:
        for ruta, dest in t["llama"]:
            if dest is not None and dest is not t:
                dest["veces_llamado"] += 1
                if _clave(t["rel"]) not in dest["llamado_por"]:
                    dest["llamado_por"].append(_clave(t["rel"]))

    avisos = []
    raices = sorted((t for t in taskbots if t["veces_llamado"] == 0),
                    key=lambda t: t["rel"])          # orden estable, no un juicio
    if not raices:
        # Todos son llamados por alguien: hay un ciclo y no existe un "empieza
        # aqui". Se arranca por el primero en orden de ruta, solo para poder
        # recorrer, y se avisa.
        raices = sorted(taskbots, key=lambda t: t["rel"])
        avisos.append("NINGUN taskbot queda sin ser llamado: hay un ciclo de llamadas. "
                      "No se puede determinar el bot de entrada desde el export.")
    elif len(raices) > 1:
        avisos.append("Hay {} taskbots a los que nadie llama: {}. No se puede saber cual es "
                      "el bot de entrada mirando solo el export; se emiten todos, en orden de "
                      "ruta. Confirmar cual corresponde al fileId exportado.".format(
                          len(raices), ", ".join(os.path.basename(t["rel"]) for t in raices)))

    orden, vistos = [], set()

    def visitar(t):
        if id(t) in vistos:
            return
        vistos.add(id(t))
        orden.append(t)
        for _ruta, dest in t["llama"]:
            if dest is not None:
                visitar(dest)

    for r in raices:
        visitar(r)
    # Lo que queda despues de recorrer TODAS las raices solo puede ser una isla
    # de llamadas ciclicas (A llama a B y B a A, sin que nada de afuera entre):
    # todos tienen quien los llame, pero el ciclo no arranca en ningun lado. Un
    # taskbot que simplemente nadie invoca NO cae aqui: es una raiz mas, y sale
    # listado como tal en el aviso de raices multiples.
    sueltos = [t for t in sorted(taskbots, key=lambda t: t["rel"]) if id(t) not in vistos]
    if sueltos:
        avisos.append("{} taskbot(s) forman un ciclo de llamadas que no se alcanza desde "
                      "ninguna raiz: {}. Se emiten al final.".format(
                          len(sueltos), ", ".join(os.path.basename(t["rel"]) for t in sueltos)))
    for t in sueltos:
        t["inalcanzable"] = True
        visitar(t)
    return orden, raices, avisos


# --- 3. Render ---------------------------------------------------------------
def encabezado(nombre_bot, orden, raices, avisos, otros_json, no_json, export_dir):
    L = []
    L.append(SEP)
    L.append("EXPORT CONSOLIDADO - {}".format(nombre_bot))
    L.append(SEP)
    L.append("")
    L.append("Contenido: los {} archivos sin extension del export (los taskbots de A360,".format(len(orden)))
    L.append("que son JSON de una sola linea), indentados y en orden de llamada.")
    L.append("El JSON de cada uno esta COMPLETO. La unica transformacion es que los")
    L.append("blobs base64 del recorder (que control toca el bot) se decodifican a texto")
    L.append("legible; base64 es reversible, y la verificacion compara contra el export")
    L.append("con el mismo decode aplicado. Nada mas se poda, redacta ni traduce.")
    L.append(MARCA_DESC)
    L.append("")
    if len(raices) != 1:
        L.append("!! BOT DE ENTRADA SIN DETERMINAR (ver avisos)")
        L.append("")
    if avisos:
        L.append("!! AVISOS")
        L.append("-" * 12)
        for a in avisos:
            L.append("  - {}".format(a))
        L.append("")
    L.append("SUB-BOTS (orden de llamada)")
    L.append("-" * 12)
    L.append("  {:<3} {:<46} {:>7} {:>9}".format("#", "taskbot", "pasos", "llamadas"))
    for i, t in enumerate(orden, 1):
        # El bot de entrada es el que nadie llama: se dice ahi mismo, en la columna
        # de llamadas, en vez de gastar dos lineas arriba repitiendo su nombre.
        if t.get("inalcanzable"):
            llam, marca = str(t["veces_llamado"]), "  <- CICLO INALCANZABLE"
        elif t["veces_llamado"] == 0:
            llam, marca = "entrada", ""
        else:
            llam, marca = str(t["veces_llamado"]), ""
        L.append("  {:<3} {:<46} {:>7} {:>9}{}".format(
            i, os.path.basename(t["rel"])[:46], t["nodos"], llam, marca))
    L.append("  {:<3} {:<46} {:>7}".format("", "total", sum(t["nodos"] for t in orden)))
    L.append("")

    L.append("QUIEN LLAMA A QUIEN")
    L.append("-" * 12)
    for t in orden:
        destinos = []
        for ruta, dest in t["llama"]:
            nom = os.path.basename(dest["rel"]) if dest else "{} (NO ESTA EN EL EXPORT)".format(ruta)
            if nom not in destinos:
                destinos.append(nom)
        if destinos:
            L.append("  {} ->".format(os.path.basename(t["rel"])))
            for nom in destinos:
                L.append("      {}".format(nom))
    L.append("")

    encs = sorted({t["enc"] for t in orden})
    if encs != ["utf-8"] and encs != ["utf-8-sig"]:
        L.append("ENCODING POR ARCHIVO (no todos son UTF-8 limpio)")
        L.append("-" * 12)
        for t in orden:
            L.append("  {:<50} {}".format(os.path.basename(t["rel"])[:50], t["enc"]))
        L.append("")

    if otros_json or no_json:
        L.append("OTROS ARCHIVOS SIN EXTENSION (no son taskbots; se reportan, no se incluyen)")
        L.append("-" * 12)
        for rel, enc, d in otros_json:
            claves = ", ".join(list(d)[:8]) if isinstance(d, dict) else type(d).__name__
            L.append("  JSON sin 'nodes'  {:<40} claves: {}".format(rel[-40:], claves))
        for rel, err in no_json:
            L.append("  no es JSON        {:<40} ({})".format(rel[-40:], err))
        L.append("")
    return L


def a_bloques(orden):
    """[(titulo, meta, obj)] listo para `escribir`."""
    out = []
    for i, t in enumerate(orden, 1):
        if t.get("inalcanzable"):
            quien = "NADIE alcanzable (parte de un ciclo de llamadas aislado)"
        elif t["llamado_por"]:
            quien = "{}  ({} llamada/s)".format(", ".join(t["llamado_por"]), t["veces_llamado"])
        else:
            quien = "(bot de entrada)"
        meta = ["# ruta en el export : {}".format(t["rel"]),
                "# nodos             : {}".format(t["nodos"]),
                "# llamado por       : {}".format(quien),
                "# encoding original : {}".format(t["enc"])]
        out.append(("{}/{}: {}".format(i, len(orden), os.path.basename(t["rel"])), meta, t["json"]))
    return out


# --- 4. El formato: un solo lector y un solo escritor -----------------------
# Todo paso de la cadena (limpiar, aplanar, lo que venga) usa ESTAS dos. Si el
# formato cambia, cambia en un solo lugar y ningun paso puede quedar leyendo
# una version distinta de la que otro escribe.

def leer_bloques(path):
    """(cabecera, [Bloque]) de un ARCHIVO del formato."""
    return leer_bloques_texto(leer_texto(path)[0])


def leer_bloques_texto(txt):
    """(cabecera, [Bloque]) de un TEXTO del formato. Bloque = (titulo, meta,
    texto_json, obj). Se devuelve el texto ademas del objeto para poder decir
    QUE bloque fallo si uno no parsea, no solo que el archivo esta roto.

    Existe la version sobre texto porque los pasos intermedios no se escriben a
    disco: el round-trip se comprueba en memoria, que es la misma garantia (se
    serializa, se vuelve a leer y se compara) sin dejar archivos que nadie usa."""
    cabecera, bloques = [], []
    titulo = meta = cuerpo = None
    for ln in txt.split("\n"):
        if ln.startswith(MARCA_INI):
            titulo, meta, cuerpo = ln[len(MARCA_INI):].strip(), [], None
        elif ln.startswith(MARCA_FIN):
            bloques.append(_bloque(titulo, meta, cuerpo))
            titulo = meta = cuerpo = None
        elif titulo is None:
            cabecera.append(ln)
        elif cuerpo is not None:
            cuerpo.append(ln)
        elif ln.startswith("{"):        # el JSON arranca en la primera '{' a col 0
            cuerpo = [ln]
        elif ln.startswith("#"):
            meta.append(ln)
    return cabecera, bloques


def _bloque(titulo, meta, cuerpo):
    texto = "\n".join(cuerpo or [])
    try:
        obj = json.loads(texto)
    except ValueError as e:
        obj = _NoParsea(str(e))
    return (titulo, meta or [], texto, obj)


class _NoParsea(str):
    """Marca un bloque ilegible sin abortar la lectura de los demas."""


def datos_de_cabecera(cabecera):
    """Las lineas de la cabecera que van DEBAJO de MARCA_DESC: los datos del bot
    (indice, grafo, avisos), que cualquier paso hereda tal cual. Si no aparece
    la marca se devuelve todo, para no perder nada."""
    for i, ln in enumerate(cabecera):
        if ln.strip() == MARCA_DESC:
            return cabecera[i + 1:]
    return list(cabecera)


def serializar(cabecera, bloques):
    """cabecera + bloques -> texto del formato. `bloques` es [(titulo, meta, obj)].
    Si `obj` ya es texto se usa tal cual (lo usa el paso que renderiza a sangria);
    si es un objeto se serializa como JSON indentado."""
    L = list(cabecera)
    for titulo, meta, obj in bloques:
        cuerpo = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=2)
        L += ["", SEP, MARCA_INI + titulo, SEP]
        L += list(meta)
        L += ["", cuerpo, "", MARCA_FIN + titulo]
    L.append("")
    return "\n".join(L)


def escribir(path, cabecera, bloques):
    """Igual que `serializar`, pero a disco. Solo lo usa el entregable final."""
    with io.open(_long(path), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(serializar(cabecera, bloques))
    return path


# --- 5. Verificacion por round-trip -----------------------------------------
def verificar(texto, taskbots):
    """Vuelve a leer el texto generado y compara cada bloque contra su original.
    Igualdad estructural, no conteo de valores: no hay lista de claves que se
    pueda quedar corta con un paquete de AA que nunca vimos.

    El original se compara con el decode de blobs aplicado, que es la unica
    transformacion de este paso. Como base64 es biyectivo, la comparacion sigue
    siendo exacta: si el bloque es igual al export-con-decode, entonces contiene
    exactamente la misma informacion que el export."""
    problemas = []
    _cab, leidos = leer_bloques_texto(texto)
    if len(leidos) != len(taskbots):
        problemas.append("bloques leidos {} != taskbots {}".format(len(leidos), len(taskbots)))

    firma = lambda o: json.dumps(o, ensure_ascii=False, sort_keys=True)
    originales = {}
    for t in taskbots:
        originales.setdefault(firma(decodificar_blobs(t["json"])), t["rel"])

    vistos = set()
    for i, (titulo, _meta, _txt, obj) in enumerate(leidos, 1):
        if isinstance(obj, _NoParsea):
            problemas.append("bloque {} ({}): no vuelve a parsear ({})".format(i, titulo, obj))
        elif firma(obj) not in originales:
            problemas.append("bloque {} ({}): no coincide con ningun archivo del export".format(i, titulo))
        else:
            vistos.add(firma(obj))
    for f in set(originales) - vistos:
        problemas.append("no salio al archivo: {}".format(originales[f]))
    return (not problemas), problemas


# --- 6. CLI ------------------------------------------------------------------
def export_dir_de(carpeta):
    """Acepta bots/<bot>/ o bots/<bot>/export/ indistintamente."""
    sub = os.path.join(carpeta, "export")
    return sub if os.path.isdir(_long(sub)) else carpeta


def construir(carpeta, hacer_verificar=True):
    """Consolida EN MEMORIA. Devuelve (cabecera, bloques, resumen).

    No escribe nada: el consolidado es un paso intermedio, no un entregable. El
    round-trip se comprueba igual, serializando a texto y volviendo a leerlo, que
    es la misma garantia que hacerlo contra un archivo."""
    carpeta = os.path.abspath(carpeta)
    export_dir = export_dir_de(carpeta)
    nombre_bot = os.path.basename(carpeta.rstrip(os.sep))

    taskbots, otros_json, no_json = descubrir(export_dir)
    if not taskbots:
        raise SystemExit("No se encontraron taskbots (archivos sin extension con 'nodes') en: " + export_dir)

    orden, raices, avisos = ordenar(taskbots)
    avisos = contrastar_manifest(export_dir, taskbots) + avisos

    # Decode de blobs: la unica transformacion de este paso.
    blobs = {"ok": 0, "fallo": [], "sospecha": {}}
    for t in orden:
        t["json"] = decodificar_blobs(t["json"], blobs)
    if blobs["fallo"]:
        avisos.append("{} blob(s) NO se pudieron decodificar; se dejan en base64.".format(
            len(blobs["fallo"])))
    for k, n in sorted(blobs["sospecha"].items()):
        avisos.append("la clave '{}' trae {} valor(es) que parecen base64 con JSON adentro. "
                      "NO se tocan (solo 'blob' se decodifica). Revisar si corresponde.".format(k, n))

    cab = encabezado(nombre_bot, orden, raices, avisos, otros_json, no_json, export_dir)
    bloques = a_bloques(orden)

    problemas = []
    if hacer_verificar:
        ok, problemas = verificar(serializar(cab, bloques), taskbots)
        if not ok:
            print("  [round-trip] {} PROBLEMA(S):".format(len(problemas)))
            for p in problemas[:20]:
                print("      {}".format(p))
            raise SystemExit(1)

    resumen = {"bot": nombre_bot, "taskbots": len(orden), "orden": orden, "raices": raices,
               "avisos": avisos, "otros_json": otros_json, "no_json": no_json,
               "nodos": sum(t["nodos"] for t in orden),
               "verificado": hacer_verificar}
    return cab, bloques, resumen


def consolidar(carpeta, out=None, hacer_verificar=True):
    """CLI: consolida y ESCRIBE. Solo para inspeccionar el intermedio a mano;
    el pipeline (main.py) usa `construir` y no deja el archivo."""
    carpeta = os.path.abspath(carpeta)
    nombre_bot = os.path.basename(carpeta.rstrip(os.sep))
    cab, bloques, res = construir(carpeta, hacer_verificar)
    orden, raices, avisos = res["orden"], res["raices"], res["avisos"]
    otros_json, no_json = res["otros_json"], res["no_json"]

    out = out or os.path.join(carpeta, "consolidado.aa.txt")
    os.makedirs(os.path.dirname(_long(out)) or ".", exist_ok=True)
    escribir(out, cab, bloques)

    # --- resumen compacto (no vuelca contenido) ---
    print("=" * 66)
    print("CONSOLIDADO - {}".format(nombre_bot))
    print("=" * 66)
    print("  Taskbots          : {}".format(len(orden)))
    print("  Nodos totales     : {}".format(sum(t["nodos"] for t in orden)))
    if len(raices) == 1:
        print("  Bot de entrada    : {}  (nadie lo llama)".format(os.path.basename(raices[0]["rel"])))
    else:
        print("  Bot de entrada    : SIN DETERMINAR")
    for a in avisos:
        print("  !! {}".format(a))
    faltantes = sorted({r for t in orden for r, d in t["llama"] if d is None})
    if faltantes:
        print("  Sub-bots llamados que NO estan en el export: {}".format(len(faltantes)))
        for r in faltantes[:10]:
            print("      {}".format(r))
    if otros_json or no_json:
        print("  Sin extension y no taskbot: {} json, {} no-json (listados en el archivo)".format(
            len(otros_json), len(no_json)))
    entrada_bytes = sum(t["bytes"] for t in orden)
    salida_bytes = os.path.getsize(_long(out))
    print("  Entrada           : {:,} chars en {} archivos".format(entrada_bytes, len(orden)))
    print("  Salida            : {:,} chars  (~{:,} tokens)".format(salida_bytes, salida_bytes // 4))
    print("  Archivo           : {}".format(out))

    if hacer_verificar:
        print("  [round-trip] OK: cada bloque vuelve a parsear y es identico al original")
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Junta los archivos sin extension de un export de AA en un solo archivo ordenado.")
    ap.add_argument("carpeta", help="bots/<bot>/ o la carpeta export/ directamente")
    ap.add_argument("--out", help="ruta del archivo de salida (default: <carpeta>/consolidado.aa.txt)")
    ap.add_argument("--sin-verificar", action="store_true",
                    help="omite el round-trip (no recomendado)")
    args = ap.parse_args()
    consolidar(args.carpeta, args.out, hacer_verificar=not args.sin_verificar)


if __name__ == "__main__":
    main()
