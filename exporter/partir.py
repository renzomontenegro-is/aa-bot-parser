#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
partir.py - Paso 2b. Separa el arbol limpio en dos: lo que se lee y el detalle.

    consolidar -> limpiar -> partir -> proceso_tecnico_<COD>.md   (lo que se lee)
                                    -> detalle_<COD>.md           (el respaldo)

POR QUE EXISTE
--------------
El tecnico dejo de ser un espejo del export para pasar a ser materia prima de un
solo consumidor: el agente que redacta el proceso de negocio. Con esa vara, la
mitad de lo que habia adentro no aporta (coordenadas en pixel, el HTML de la
pagina volcado entero, el cuerpo de pasos que no corren) y ademas tapa lo que si.

No se borra: se MUEVE. El contrato del pipeline cambia de

    el documento == el export

a

    el documento + el detalle == el export

`unir()` es el inverso exacto de `partir()` y la verificacion lo comprueba en cada
corrida sobre el arbol, no sobre un conteo.

LAS CUATRO PODAS, Y HACIA DONDE FALLA CADA UNA
----------------------------------------------
Ninguna nombra un comando ni un paquete de AA. Todas se apoyan en una marca
estructural, y todas fallan hacia ARRIBA: si la marca no esta, la regla no
dispara y el documento sale mas gordo. Ninguna puede hacer desaparecer algo por
no reconocerlo.

  OFF   el cuerpo de un paso apagado          marca: `disabled: true`
  UI    `blob` y `capture` de una pantalla    marca: los dos contenedores
  CRIT  los criterios que el bot no usa       marca: `enabled: true` en los que si
  CONST la casilla que nunca varia            no mira nombres: compara valores

Sobre CRIT: en los 16 bots del parque hay 242 claves distintas, y las 70 que
aparecen en uno o dos bots estan TODAS dentro de `criteria` (FontUnderlineStyle,
LegacyKeyboardShortcut, CSS Selector...). Por eso la regla no puede tener lista de
nombres: mira la marca de uso, y un criterio que AA invente manana funciona igual.

LA NUMERACION
-------------
Cada paso lleva `$n`, correlativo por sub-bot y en orden de lectura. Plano, no
jerarquico: el arbol llega a 15 niveles de profundidad, asi que una direccion
`1.2.3.4.5.6.7.8` costaria el doble que `#38` y la jerarquia ya la muestra la
sangria. El sub-bot mas grande del parque tiene 748 pasos: nunca pasa de 3
digitos.

Sirve para tres cosas: citar un paso en el documento de negocio, cruzar la linea
de error de un log contra el bot, y direccionar el detalle. Se calcula por
posicion, asi que insertar un paso en AA corre los numeros de ahi para abajo; no
es un problema porque los dos archivos se generan siempre juntos.
"""
import collections
import json

# Claves propias de este paso. Todas arrancan con `$` para que nunca choquen con
# una clave de AA, que no las usa. Las mismas marcas que ya usaba el dedup de
# objetos de pantalla ($id / $ref), por consistencia.
N_PASO = "$n"        # numero de paso dentro del sub-bot
REF_OFF = "$off"     # el cuerpo de este paso apagado esta en el detalle
REF_ID = "$id"       # ficha de pantalla: definicion (la pone limpiar.py)
REF_A = "$ref"       # ficha de pantalla: referencia  (la pone limpiar.py)

UI_CLAVE = "uiObject"
UI_AL_DETALLE = ("blob", "capture")     # los dos contenedores que se mueven
CRIT_CLAVE = "criteria"
CRIT_MARCA = "enabled"                  # el criterio que el bot USA lo trae en True
SIN_ANCLA = "$sin-ancla"                # la ficha no marco NINGUN criterio en uso

CUERPO = ("children", "branches")


def es_paso(n):
    """Un paso es un nodo con comando. Vale para los nodos y para las ramas
    (else, catch, finally), que en A360 tambien traen commandName."""
    return isinstance(n, dict) and "commandName" in n


# --- Numeracion --------------------------------------------------------------
def numerar(o, cont=None):
    """Copia con `$n` en cada paso, en el MISMO orden en que se renderiza.

    Recorre generico (valores de dict en orden de insercion, listas en orden), sin
    nombrar `children` ni `branches`: si A360 agrega otro contenedor de pasos, se
    numera igual."""
    cont = [0] if cont is None else cont
    if isinstance(o, dict):
        out = {}
        if es_paso(o):
            cont[0] += 1
            out[N_PASO] = cont[0]
        for k, v in o.items():
            out[k] = numerar(v, cont)
        return out
    if isinstance(o, list):
        return [numerar(v, cont) for v in o]
    return o


def desnumerar(o):
    """Inverso: saca `$n`. La numeracion es anotacion, no dato del bot."""
    if isinstance(o, dict):
        return {k: desnumerar(v) for k, v in o.items() if k != N_PASO}
    if isinstance(o, list):
        return [desnumerar(v) for v in o]
    return o


# --- Poda CONST: la casilla que nunca varia -----------------------------------
def _terna(n):
    return (n.get("packageName", ""), n.get("commandName", ""))


# Solo una CASILLA puede ser constante: un atributo con un dato escalar adentro.
# Un atributo con estructura adentro (otro `attributes`, un taskbotFile, un
# diccionario) no es una casilla, es contenido, y que se repita no lo vuelve
# relleno.
#
# La distincion no es un capricho: sin ella la regla se llevaba la CONDICION de
# los cinco `If` de RP029_001_DescargarTramasCobroFTP, porque los cinco preguntan
# lo mismo. Eso es la regla de negocio del sub-bot, no una casilla repetida. Lo
# encontro el control (d) de suficiencia, no una lectura a ojo.
#
# Es una regla de FORMA, no de nombres: no dice "los If no", dice "lo que tiene
# estructura adentro no". Un paquete de AA que nunca vimos se mide igual.
_ESCALARES = ("string", "number", "boolean", "expression")


def _es_casilla(bloque):
    if set(bloque) != {"name", "value"} or not isinstance(bloque["value"], dict):
        return False
    v = bloque["value"]
    if "type" not in v:
        return False
    resto = [k for k in v if k != "type"]
    return all(k in _ESCALARES and isinstance(v[k], (str, int, float, bool)) for k in resto)


def constantes_de(arbol):
    """{(paquete, comando, atributo): bloque} de los atributos que valen SIEMPRE
    lo mismo en este sub-bot.

    Condiciones, las tres a la vez:
      - la terna aparece 2+ veces (con una sola no hay nada que declarar)
      - TODOS esos pasos traen ese atributo exactamente una vez
      - el bloque entero del atributo es identico en todos

    La tercera es la que la hace segura: no dice "esta clave suele valer X" (eso
    ya se probo en limpiar.py y se llevaba datos reales), dice "esta casilla de
    este comando no esta eligiendo nada en este bot".
    """
    pasos = collections.defaultdict(list)
    for n in _todos_los_pasos(arbol):
        pasos[_terna(n)].append(n)

    out = {}
    for terna, ns in pasos.items():
        if len(ns) < 2:
            continue
        nombres = collections.Counter()
        for n in ns:
            for a in n.get("attributes") or []:
                if isinstance(a, dict) and "name" in a:
                    nombres[a["name"]] += 1
        for nom, veces in nombres.items():
            if veces != len(ns):            # no lo traen todos: no se toca
                continue
            firmas = set()
            bloque = None
            for n in ns:
                cand = [a for a in (n.get("attributes") or [])
                        if isinstance(a, dict) and a.get("name") == nom]
                if len(cand) != 1:
                    firmas.add("(repetido)")
                    break
                bloque = cand[0]
                firmas.add(json.dumps(bloque, ensure_ascii=False, sort_keys=True))
            if len(firmas) == 1 and bloque is not None and _es_casilla(bloque):
                out[terna + (nom,)] = bloque
    return out


SEP_OFF = "  ->  "     # separa la clave del respaldo de su resumen


def _resumen_cuerpo(cuerpo):
    """`  ->  14 pasos: Recorder x6, Keystrokes x4, ...` para lo que se movio.

    Va pegado a la clave y no como clave aparte para no agregar una hoja al
    arbol: `unir` corta por el separador y recupera la clave exacta."""
    pasos = list(_todos_los_pasos(cuerpo))
    if not pasos:
        return ""
    c = collections.Counter(n.get("packageName") or n.get("commandName") or "?" for n in pasos)
    detalle = ", ".join("{} x{}".format(k, v) if v > 1 else k for k, v in c.most_common(6))
    if len(c) > 6:
        detalle += ", ..."
    return "{}{} paso(s): {}".format(SEP_OFF, len(pasos), detalle)


def _todos_los_pasos(o):
    if isinstance(o, dict):
        if es_paso(o):
            yield o
        for v in o.values():
            yield from _todos_los_pasos(v)
    elif isinstance(o, list):
        for v in o:
            yield from _todos_los_pasos(v)


# --- Partir ------------------------------------------------------------------
def partir(arbol, subbot):
    """(trabajo, detalle). `detalle` es {'off': {...}, 'ui': {...}, 'const': {...}}.

    `trabajo` es lo que se lee; `detalle` lo que se guarda por si un ticket lo
    pide. `unir(trabajo, detalle)` reconstruye `arbol` exacto."""
    det = {"off": {}, "ui": {}, "const": {}}
    const = constantes_de(arbol)
    for k, v in const.items():
        det["const"]["|".join(k)] = v

    def rec(o, apagado=False):
        if isinstance(o, dict):
            # --- ficha de pantalla: se van blob y capture, y los criterios sin uso
            if REF_ID in o and any(k in o for k in UI_AL_DETALLE + (CRIT_CLAVE,)):
                h = o[REF_ID]
                guardado = det["ui"].setdefault(h, {})
                out = {}
                for k, v in o.items():
                    if k in UI_AL_DETALLE:
                        guardado[k] = v
                        continue
                    if k == CRIT_CLAVE and isinstance(v, dict):
                        usados, sobran = {}, {}
                        for nom, c in v.items():
                            (usados if isinstance(c, dict) and c.get(CRIT_MARCA) is True
                             else sobran)[nom] = c
                        # Ficha sin ningun criterio en uso: existe (T046_LoginGTI del
                        # RP116 tiene una, 23 criterios y los 23 en false). Ahi la regla
                        # se apaga y la ficha se queda con TODOS: si se fueran al
                        # detalle, el control quedaria en el documento como un cuadrito
                        # de tipo y tecnologia, sin nada con que leerlo. Y que un clic
                        # no tenga ancla no es un hueco del parser, es el hallazgo.
                        if not usados:
                            out[SIN_ANCLA] = True
                            out[k] = rec(v, apagado)
                            continue
                        if sobran:
                            guardado[CRIT_CLAVE] = sobran
                        out[k] = rec(usados, apagado)
                        continue
                    out[k] = rec(v, apagado)
                return out

            # --- paso: constantes fuera, y si esta apagado su cuerpo al detalle
            if es_paso(o):
                yo_apagado = apagado or o.get("disabled") is True
                out = {}
                for k, v in o.items():
                    if k == "attributes" and isinstance(v, list):
                        quedan = [a for a in v
                                  if not (isinstance(a, dict)
                                          and _terna(o) + (a.get("name"),) in const)]
                        if quedan:
                            out[k] = rec(quedan, yo_apagado)
                        continue
                    if k in CUERPO and yo_apagado and v:
                        clave = "{}#{}".format(subbot, o.get(N_PASO))
                        det["off"].setdefault(clave, {})[k] = v
                        # La clave sola no alcanza: el lector no sabe si adentro
                        # hay tres pasos triviales o un login entero con su URL y
                        # su casilla de correo. Sin esto, el codigo muerto pasa de
                        # ser ruido a ser invisible, que es peor: en RP112 el
                        # unico rastro del login apagado a Qualitat seria un hash.
                        out[REF_OFF] = clave + _resumen_cuerpo(det["off"][clave])
                        continue
                    out[k] = rec(v, yo_apagado)
                return out

            return {k: rec(v, apagado) for k, v in o.items()}
        if isinstance(o, list):
            return [rec(v, apagado) for v in o]
        return o

    return rec(arbol), det


def unir(trabajo, det, subbot):
    """Inverso exacto de partir(). Lo usa la verificacion antes de comparar."""
    const = {tuple(k.split("|")): v for k, v in det.get("const", {}).items()}

    def rec(o):
        if isinstance(o, dict):
            if REF_ID in o and o[REF_ID] in det.get("ui", {}):
                guardado = det["ui"][o[REF_ID]]
                out = {}
                for k, v in o.items():
                    if k == SIN_ANCLA:      # marca del documento, no sale del export
                        continue
                    if k == CRIT_CLAVE and isinstance(v, dict):
                        junto = dict(v)
                        junto.update(guardado.get(CRIT_CLAVE, {}))
                        out[k] = rec(junto)
                        continue
                    out[k] = rec(v)
                for k in UI_AL_DETALLE:
                    if k in guardado:
                        out[k] = guardado[k]
                if CRIT_CLAVE not in out and CRIT_CLAVE in guardado:
                    out[CRIT_CLAVE] = guardado[CRIT_CLAVE]
                return out

            if es_paso(o):
                out = {}
                for k, v in o.items():
                    if k == REF_OFF:
                        for ck, cv in det["off"].get(v.split(SEP_OFF)[0], {}).items():
                            out[ck] = cv
                        continue
                    out[k] = rec(v)
                faltan = [a for k, a in const.items() if k[:2] == _terna(o)]
                if faltan:
                    out.setdefault("attributes", [])
                    out["attributes"] = list(out["attributes"]) + faltan
                return out

            return {k: rec(v) for k, v in o.items()}
        if isinstance(o, list):
            return [rec(v) for v in o]
        return o

    return rec(trabajo)


# --- Controles de suficiencia -------------------------------------------------
# El control viejo mide "no se perdio nada" y sigue estando. No mide "lo que
# quedo alcanza para auditar", que es lo que pasa a importar cuando algo sale del
# documento. Estos cuatro cubren esa mitad. Ninguno tiene lista de claves.
def verificar_suficiencia(arbol_completo, trabajo, det, subbot, cabecera=""):
    """[(motivo, detalle)] de lo que quedaria inservible aunque nada se pierda.

    `cabecera` es el texto que se declara arriba del sub-bot: cuenta como parte
    del documento de trabajo, porque una casilla constante se omite abajo pero se
    escribe entera ahi."""
    problemas = []

    # (a) ningun paso vive solo en el detalle
    n_orig = sum(1 for _ in _todos_los_pasos(arbol_completo))
    n_trab = sum(1 for _ in _todos_los_pasos(trabajo))
    n_det = sum(1 for cuerpo in det["off"].values()
                for _ in _todos_los_pasos(cuerpo))
    if n_trab + n_det != n_orig:
        problemas.append(("PASOS", "{}: {} pasos, {} en el trabajo + {} en el detalle"
                          .format(subbot, n_orig, n_trab, n_det)))

    # (b) ninguna ficha de pantalla se quedo sin criterios POR CULPA DE LA PODA.
    #     Que el export no marque ninguno en uso es un caso real y se lee en el
    #     documento (marca SIN_ANCLA, con los criterios enteros al lado); lo que
    #     no puede pasar es que la ficha tuviera criterios y se fueran todos al
    #     detalle, porque ahi el control queda ilegible en el documento.
    for ficha, h in _fichas(trabajo):
        if ficha.get(CRIT_CLAVE):
            continue
        if CRIT_CLAVE in det.get("ui", {}).get(h, {}):
            problemas.append(("SIN ANCLA", "{}: la ficha {} tenia criterios de busqueda y "
                              "todos terminaron en el detalle. Revisar las marcas `{}` y `{}`"
                              .format(subbot, h, CRIT_MARCA, SIN_ANCLA)))

    # (c) cada referencia al detalle existe, y cada entrada del detalle se referencia
    refs = {r.split(SEP_OFF)[0] for r in _refs_off(trabajo)}
    claves = set(det["off"])
    for r in refs - claves:
        problemas.append(("HUERFANO", "{}: el paso apunta a {} y no esta en el detalle".format(subbot, r)))
    for r in claves - refs:
        problemas.append(("HUERFANO", "{}: {} esta en el detalle y nadie lo apunta".format(subbot, r)))

    # (d) todo texto escrito por una persona, en un paso que CORRE, sigue en el
    #     documento de trabajo. La linea es esa: lo que tecleo alguien se queda,
    #     lo que genero el grabador de AA se puede mover.
    #
    #     Dos exclusiones, y las dos son direccionables desde el propio documento,
    #     que es lo que las hace legitimas:
    #       - el interior de una ficha de pantalla: lo genero AA, y el control (b)
    #         ya exige que la ficha conserve como encuentra el control;
    #       - el cuerpo de un paso apagado: el paso sigue visible con su `$off`,
    #         asi que el texto no se perdio de vista, se corrio de archivo.
    #     Las casillas constantes NO se excluyen: se declaran enteras en la
    #     cabecera, asi que tienen que aparecer ahi o esto salta.
    #     La cabecera se busca en las DOS formas, cruda y escapada, igual que hace
    #     `verificar_texto`: una consulta SQL o un cuerpo de correo con saltos de
    #     linea se escribe escapado, y sigue estando.
    en_trabajo = collections.Counter(_textos(trabajo))
    for t, n in collections.Counter(_textos_humanos(arbol_completo)).items():
        escapado = json.dumps(t, ensure_ascii=False)[1:-1]
        if en_trabajo[t] < n and t not in cabecera and escapado not in cabecera:
            problemas.append(("TEXTO", "{}: {!r} aparece {} vez/veces en un paso vivo del bot "
                              "y {} en el documento".format(subbot, t[:60], n, en_trabajo[t])))
    return problemas


def _fichas(o, h=None):
    if isinstance(o, dict):
        if REF_ID in o:
            yield o, o[REF_ID]
        for v in o.values():
            yield from _fichas(v)
    elif isinstance(o, list):
        for v in o:
            yield from _fichas(v)


def _refs_off(o):
    if isinstance(o, dict):
        if REF_OFF in o:
            yield o[REF_OFF]
        for v in o.values():
            yield from _refs_off(v)
    elif isinstance(o, list):
        for v in o:
            yield from _refs_off(v)


def _textos(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _textos(v)
    elif isinstance(o, list):
        for v in o:
            yield from _textos(v)
    elif isinstance(o, str):
        yield o


def _textos_humanos(o, dentro_ui=False, apagado=False):
    """Los textos que escribio una persona en un paso que corre.

    Fuera quedan dos zonas, las dos direccionables desde el documento:
      - el interior de una ficha de pantalla (`uiObject`): rutas del DOM, volcados
        de HTML e identificadores que genero el grabador de AA;
      - el cuerpo de un paso apagado: sus `children` y `branches`. El paso en si
        se sigue viendo, con su marca y su `{}`.""".format(REF_OFF)
    if isinstance(o, dict):
        yo = apagado or o.get("disabled") is True
        for k, v in o.items():
            if yo and k in CUERPO:
                continue
            if k == "type":
                continue        # etiqueta del formato de AA, no la tecleo nadie
            yield from _textos_humanos(v, dentro_ui or k == UI_CLAVE, yo)
    elif isinstance(o, list):
        for v in o:
            yield from _textos_humanos(v, dentro_ui, apagado)
    elif isinstance(o, str) and not dentro_ui:
        yield o


# --- Cabecera y detalle a texto ----------------------------------------------
def bloque_cabecera(det, subbot, trabajo=None):
    """Lo que se declara arriba del sub-bot sobre lo que se movio."""
    L = []
    sin_ancla = sorted({h for f, h in _fichas(trabajo or {}) if f.get(SIN_ANCLA)})
    if det["const"]:
        L += ["CASILLAS QUE NO VARIAN EN ESTE BOT",
              "-" * 78,
              "Estos atributos valen lo mismo en TODAS las apariciones de su comando, asi",
              "que no estan eligiendo nada aqui. Se escriben una vez y se omiten abajo.",
              ""]
        for k in sorted(det["const"]):
            pkg, cmd, nom = k.split("|")
            # El valor va ENTERO, no resumido: el control de suficiencia lo busca
            # aqui, asi que si se truncara, el texto omitido abajo no estaria en
            # ninguna parte del documento y el control frenaria la generacion.
            L.append("  {}.{} -> {} = {}".format(pkg or "?", cmd or "?", nom,
                                                 _valor_entero(det["const"][k])))
        L.append("")
    if det["off"]:
        L += ["PASOS APAGADOS",
              "-" * 78,
              "El paso queda con su comando y su marca; su cuerpo esta en el detalle,",
              "bajo la clave que dice `{}`. {} paso(s) con cuerpo.".format(REF_OFF, len(det["off"])),
              ""]
    if det["ui"]:
        L += ["FICHAS DE PANTALLA",
              "-" * 78,
              "De cada control queda como lo ubica el bot (los criterios marcados en uso),",
              "su tipo y su tecnologia. Las coordenadas, el HTML volcado y los criterios",
              "que no usa estan en el detalle, bajo su `{}`. {} ficha(s).".format(REF_ID, len(det["ui"])),
              ""]
    if sin_ancla:
        L += ["  {} ficha(s) traen `{}: true`: el export no marco NI UN criterio en uso.".format(
                  len(sin_ancla), SIN_ANCLA),
              "  Ahi van los criterios COMPLETOS (ninguno se movio al detalle), y todos",
              "  valen `{}: false`. El bot ubica ese control sin ningun ancla declarada.".format(CRIT_MARCA),
              "  Fichas: " + ", ".join(sin_ancla),
              ""]
    return L


def escribir_detalle(nombre_bot, detalles, render):
    """El archivo de respaldo, en texto. No se lee de corrido: se busca una cosa.

    Cada entrada arranca con una linea `----- <clave> -----` que es unica en todo
    el archivo, asi que se llega de un grep exacto y no hay que barrer nada:
      - un paso apagado:  `----- T042_LoginGmail#38 -----`, la misma direccion que
        el documento de trabajo escribe en su `{}`
      - una ficha de pantalla: `----- pantalla 11f5c525dac6 -----`, el mismo hash
        que el documento escribe en su `{}`
      - una casilla constante: `----- <sub-bot> String.assign sourceString -----`
    """.format(REF_OFF, REF_ID)
    L = ["=" * 78,
         "DETALLE DE " + nombre_bot,
         "=" * 78,
         "Respaldo del proceso tecnico. Aqui esta lo que se saco del documento de",
         "trabajo para que ese documento se pueda leer entero: el cuerpo de los pasos",
         "que no corren, y de cada control de pantalla las coordenadas, el volcado de",
         "HTML y los criterios de busqueda que el bot no usa.",
         "",
         "NO se lee de corrido. Cada entrada tiene una clave unica: se busca esa clave",
         "y se lee solo ese bloque. Las claves salen del documento de trabajo.",
         "",
         "documento de trabajo + este archivo = el export original. Lo comprueba la",
         "verificacion en cada generacion; si no cuadra, no se genera ninguno de los dos.",
         ""]

    off = [(s, k, v) for s, d in detalles for k, v in sorted(d["off"].items())]
    ui = [(s, k, v) for s, d in detalles for k, v in sorted(d["ui"].items())]
    const = [(s, k, v) for s, d in detalles for k, v in sorted(d["const"].items())]

    if off:
        L += ["", "=" * 78, "CUERPO DE LOS PASOS APAGADOS ({})".format(len(off)), "=" * 78, ""]
        for _s, k, v in off:
            L += ["----- {} -----".format(k), render(v), ""]
    if ui:
        L += ["", "=" * 78, "FICHAS DE PANTALLA ({})".format(len(ui)), "=" * 78, ""]
        for _s, k, v in ui:
            L += ["----- pantalla {} -----".format(k), render(v), ""]
    if const:
        L += ["", "=" * 78, "CASILLAS CONSTANTES ({})".format(len(const)), "=" * 78, ""]
        for s, k, v in const:
            pkg, cmd, nom = k.split("|")
            L += ["----- {} {}.{} {} -----".format(s, pkg or "?", cmd or "?", nom),
                  render(v), ""]
    return "\n".join(L) + "\n"


def _valor_entero(bloque):
    """El atributo constante escrito completo y en una linea. Sin recortes: el
    control de suficiencia busca aqui el texto que se omitio abajo."""
    v = bloque.get("value")
    if not isinstance(v, dict):
        return json.dumps(bloque, ensure_ascii=False)
    tipo = v.get("type", "")
    for k in ("string", "expression", "number", "boolean"):
        if k in v:
            marca = "" if k in ("string", "number", "boolean") and tipo in (
                "STRING", "NUMBER", "BOOLEAN") else "{}.{} ".format(tipo, k)
            return marca + json.dumps(v[k], ensure_ascii=False)
    return json.dumps(v, ensure_ascii=False)
