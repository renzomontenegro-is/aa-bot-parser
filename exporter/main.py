#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
main.py - Orquestador UNICO: fileId -> proceso_tecnico_<BOT>.md

    fileId
      -> export_bot.py    baja el bot del Control Room (sin los .jar)
      -> consolidar.py    junta los taskbots en orden de aparicion, decodifica base64
      -> limpiar.py       quita vacios y uid no referenciados, escribe con sangria
      -> requisitos.py    inventario de lo externo, con evidencia y sin veredicto
      -> Bots/<bot>/proceso_tecnico_<BOT>.md

Que lo hace distinto del pipeline anterior
------------------------------------------
El anterior traducia cada nodo con una tabla por tipo y un renderizador por
construccion: lo que la tabla no nombraba desaparecia en silencio, y su control
de cobertura miraba solo tres claves, asi que tampoco lo veia. De ahi las siete
perdidas de una sola jornada.

Este no traduce. Copia el arbol entero y le quita, con reglas que no nombran
ningun comando de AA, lo que no lleva informacion:
  1. claves vacias        ("", [], {}, null; false y 0 se conservan: son datos)
  2. uid no referenciados (si otro campo lo apunta, se conserva)
  3. sangria en vez de llaves
Y lo comprueba en cada corrida con tres controles encadenados, ninguno con lista
de claves: export<->consolidado (igualdad exacta del objeto), arbol<->arbol (por
ruta), arbol<->texto (conteo de hojas y presencia de cada valor).

Uso:
    python exporter/main.py <file-id>
    python exporter/main.py <file-id> --force         # volver a descargar
    python exporter/main.py --carpeta Bots/<bot>      # ya descargado, sin tocar la API
"""
import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)

import consolidar as C
import limpiar as L
import requisitos as R

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BOTS_DIR = os.path.join(ROOT, "Bots")
INDEX_PATH = os.path.join(BOTS_DIR, ".export_index.json")

# Codigo corto del bot para el nombre del archivo: RP053, T089, AC019.
# Si el nombre de entrada no lo tiene, se usa el nombre completo saneado, que
# es feo pero nunca ambiguo.
RE_CODIGO = re.compile(r"^([A-Za-z]{1,4}\d{2,4})(?![0-9])")


def codigo_de(nombre_entrada):
    m = RE_CODIGO.match(os.path.basename(nombre_entrada))
    if m:
        return m.group(1).upper()
    return re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.basename(nombre_entrada))[:40]


# --- Paso 0: asegurar el export --------------------------------------------
def asegurar_export(file_id, nombre, con_paquetes, force, keep_screenshots=False):
    """Devuelve la carpeta Bots/<bot>/. Exporta solo si hace falta."""
    import json
    key = str(file_id)
    try:
        with open(INDEX_PATH, encoding="utf-8") as fh:
            idx = json.load(fh)
    except (OSError, ValueError):
        idx = {}

    if not force and key in idx:
        cand = os.path.join(BOTS_DIR, idx[key])
        if os.path.isdir(C._long(os.path.join(cand, "export"))):
            print("[export] fileId {} ya esta en Bots/{}  (--force para re-exportar)".format(
                key, idx[key]))
            return cand

    import export_bot
    token = export_bot.Token()
    if nombre:
        file_id, meta = export_bot.verify_file(token, key, nombre)
    else:
        meta = export_bot.get_file_metadata(token, key)
        if not meta:
            raise SystemExit("fileId {} no existe en el workspace. Busca con:\n"
                             "  python exporter/export_bot.py --search <nombre>".format(key))
        file_id = str(meta.get("id"))
    carpeta = export_bot._safe_name(meta.get("name") or "bot_{}".format(file_id))
    cand = os.path.join(BOTS_DIR, carpeta)

    if not force and os.path.isdir(C._long(os.path.join(cand, "export"))):
        print("[export] fileId {} ya esta en Bots/{}".format(file_id, carpeta))
    else:
        print("[export] fileId {} -> Bots/{}".format(file_id, carpeta))
        export_dir = export_bot.run_export(token, file_id, expected_name=nombre,
                                           include_packages=con_paquetes,
                                           keep_screenshots=keep_screenshots,
                                           announce=False)
        cand = os.path.dirname(str(export_dir))

    idx[str(file_id)] = os.path.basename(cand)
    os.makedirs(BOTS_DIR, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=2)
    return cand


# --- Orquestacion -----------------------------------------------------------
def procesar(perbot, verificar=True):
    """Todo en memoria. El unico archivo que toca el disco es el entregable:
    el consolidado y el limpio son pasos intermedios, no productos."""
    perbot = os.path.abspath(perbot)

    print("\n[1/3] consolidar")
    cab_cons, bloques, res_c = C.construir(perbot, hacer_verificar=verificar)
    print("  {} taskbots | {} nodos | entrada: {}".format(
        res_c["taskbots"], res_c["nodos"],
        os.path.basename(res_c["raices"][0]["rel"]) if len(res_c["raices"]) == 1 else "SIN DETERMINAR"))
    for a in res_c["avisos"]:
        print("  !! {}".format(a))
    if verificar:
        print("  [round-trip] OK: cada bloque vuelve a parsear y es identico al export")

    print("\n[2/3] limpiar")
    _cab, _sal, resumen, problemas = L.limpiar_bloques(cab_cons, bloques, hacer_verificar=verificar)
    L.informe(None, resumen, problemas, verificar)

    print("\n[3/3] requisitos externos + proceso tecnico")
    inv = R.recolectar(resumen["arboles"], C._iter_nodos)

    entrada = resumen["arboles"][0][0] if resumen["arboles"] else os.path.basename(perbot)
    cod = codigo_de(entrada)
    destino = os.path.join(perbot, "proceso_tecnico_{}.md".format(cod))

    # Portada: lo primero que se lee es que bot es y de que tamano. Se arma aca
    # porque los conteos salen de requisitos.py, que corre despues de limpiar.
    # El inventario detallado de lo externo NO va al documento: solo por consola.
    c = inv["cont"]
    nombre = os.path.basename(res_c["raices"][0]["rel"]) if len(res_c["raices"]) == 1 else cod
    ficha = "{:,} pasos | {} sub-bots | {} pasos de UI".format(
        c["pasos"], c["subbots"], c["ui"]).replace(",", ".")
    if c["off"]:
        ficha += " | {} apagados ({} pasos sin correr)".format(c["off"], c["off_total"])
    portada = ["=" * 78, nombre, "=" * 78, ficha]

    cab_fin, salida_fin, res_fin, _p = L.limpiar_bloques(cab_cons, bloques, hacer_verificar=False,
                                                         portada=portada)
    C.escribir(destino, cab_fin, salida_fin)

    # El respaldo: lo que salio del documento de trabajo, direccionado por las
    # mismas claves que ese documento escribe. Se genera SIEMPRE junto al tecnico,
    # nunca por separado: la verificacion vale para el par, no para uno solo.
    import partir as P
    detalle_path = os.path.join(perbot, "detalle_{}.md".format(cod))
    texto_det = P.escribir_detalle(nombre, res_fin["detalles"], L.render)
    with io.open(C._long(detalle_path), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(texto_det)

    tam = os.path.getsize(C._long(destino))
    tam_det = os.path.getsize(C._long(detalle_path))
    print("\n" + "=" * 66)
    print("LISTO - {}".format(cod))
    print("=" * 66)
    print("  Pasos AA      : {}".format(inv["cont"]["pasos"]))
    print("  Sub-bots      : {}".format(inv["cont"]["subbots"]))
    print("  Pasos de UI   : {}".format(inv["cont"]["ui"]))
    print("  Desactivados  : {} apagados -> {} pasos sin correr".format(
        inv["cont"]["off"], inv["cont"]["off_total"]))
    print("  Global values : {}".format(len(inv["gv"])))
    print("  Credenciales  : {}".format(len(inv["cred"])))
    print("  Archivos      : {}".format(len(inv["arch"])))
    print("  Entregable    : {}  ({:,} chars)".format(destino, tam))
    print("  Respaldo      : {}  ({:,} chars, no se lee de corrido)".format(detalle_path, tam_det))

    # Mapa de lineas: para que quien lea el tecnico salte directo al sub-bot que
    # busca en vez de grepear un archivo de decenas de miles de lineas.
    with io.open(C._long(destino), encoding="utf-8") as fh:
        lineas = fh.read().split("\n")
    marcas = [(i + 1, l.split(": ", 1)[-1]) for i, l in enumerate(lineas)
              if l.startswith("===== BOT ")]
    fines = [i + 1 for i, l in enumerate(lineas) if l.startswith("===== FIN BOT ")]
    print("\n  MAPA DE LINEAS ({:,} lineas en total)".format(len(lineas)))
    for n, (ini, nombre) in enumerate(marcas):
        fin = fines[n] if n < len(fines) else len(lineas)
        print("    {:>6} - {:<6}  {}".format(ini, fin, nombre))
    print("\n  Siguiente     : redactar proceso_negocio_{}.md".format(cod))
    return destino


def main():
    ap = argparse.ArgumentParser(description="fileId -> proceso_tecnico_<BOT>.md")
    ap.add_argument("file_id", nargs="?", help="fileId del bot en el Control Room")
    # `--export` era el nombre viejo y decia lo contrario de lo que hace: suena a
    # "exporta", cuando significa "NO exportes, usa lo que ya esta bajado".
    ap.add_argument("--carpeta", "--export", dest="carpeta",
                    help="procesa un bot ya descargado en Bots/<bot>/, sin llamar al Control Room")
    ap.add_argument("--name", help="nombre esperado del bot (verifica el fileId antes de exportar)")
    ap.add_argument("--with-packages", action="store_true",
                    help="incluir los .jar al exportar (default NO: no aportan a la auditoria)")
    ap.add_argument("--keep-screenshots", action="store_true",
                    help="conservar los .png del Recorder (default: se purgan; "
                         "el parser solo lee los archivos sin extension + manifest.json)")
    ap.add_argument("--force", action="store_true", help="re-exportar aunque ya exista")
    ap.add_argument("--sin-verificar", action="store_true",
                    help="omite los controles de perdida (no recomendado)")
    args = ap.parse_args()

    if args.carpeta:
        perbot = os.path.abspath(args.carpeta)
    elif args.file_id:
        perbot = asegurar_export(args.file_id, args.name, args.with_packages,
                                 args.force, keep_screenshots=args.keep_screenshots)
    else:
        ap.error("indica un <file-id>, o --carpeta Bots/<bot> si ya lo tenes descargado")
    procesar(perbot, verificar=not args.sin_verificar)


if __name__ == "__main__":
    main()
