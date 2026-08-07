#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
limpiar.py - Paso 2. Deja el bot en el minimo de tokens SIN perder informacion.

    export -> consolidar.py -> consolidado.aa.txt -> limpiar.py -> limpio.aa.txt

REGLA: se borra una clave si su valor es "", [], {} o null. De abajo hacia
arriba, asi que un objeto que queda vacio tambien se va.

Por que no pierde informacion: el `value` de A360 es un struct de esquema FIJO
(siempre trae sus ~25 claves y solo 1 o 2 vienen llenas segun el `type`). Las
vacias no son "un dato que vale vacio": son casillas que ese comando no usa.
Con esquema fijo, ausencia y vacio son el mismo estado.

OJO: `false` y `0` NO son vacio, son datos (el valor de un IF, un contador
inicial, un trimAtBeginning=false). Escribir la regla como "borrar lo falsy"
seria el error de siempre: decidir por la forma del valor y no por su contenido.

Uso:
    python exporter/limpiar.py bots/<bot>
    python exporter/limpiar.py bots/<bot> --out ruta.txt --sin-verificar
"""
import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import consolidar as C
import partir as P

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

NOTA = None

# Va al FINAL del encabezado, no al principio: lo primero que se lee tiene que ser
# que bot es y de que tamano, no como funciona el limpiador.
NOTA_FINAL = [
    "",
    "Como se escribio: sin claves vacias, sin uid no referenciados, con los pares por",
    "defecto declarados por sub-bot, con el atributo escalar en una linea y con sangria",
    "en vez de llaves.",
    "",
    "Cada paso lleva `$n`, correlativo dentro de su sub-bot. Sirve para citarlo, para",
    "cruzarlo con la linea de error de un log, y para ir al respaldo.",
    "",
    "Lo que no esta aqui esta en detalle_<CODIGO>.md, en el mismo directorio: el cuerpo",
    "de los pasos apagados (clave `$off`), y de cada control de pantalla las coordenadas,",
    "el HTML volcado y los criterios que el bot no usa (clave `$id`). Este documento mas",
    "ese archivo son el export completo, y la verificacion lo comprueba en cada corrida.",
]


def es_vacio(v):
    if isinstance(v, (str, list, dict)):
        return len(v) == 0
    return v is None                    # numeros y booleanos NUNCA son vacio


def podar(o, borrados_de_lista=None):
    """Copia sin claves vacias. `borrados_de_lista` acumula las rutas donde se
    elimino un ELEMENTO de lista, que es el unico caso en que podar corre las
    posiciones. Hoy no ocurre ni una vez en los 12 bots, pero si un dia una
    lista posicional (adjuntos, form-data) trae un hueco vacio, conviene verlo
    en vez de suponer que nunca pasa."""
    if isinstance(o, dict):
        return {k: v for k, v in ((k, podar(v, borrados_de_lista)) for k, v in o.items())
                if not es_vacio(v)}
    if isinstance(o, list):
        out = [v for v in (podar(x, borrados_de_lista) for x in o) if not es_vacio(v)]
        if borrados_de_lista is not None and len(out) != len(o):
            borrados_de_lista.append(len(o) - len(out))
        return out
    return o


# --- Regla 2: identificadores internos --------------------------------------
# El `uid` es el numero de serie que AA le pone a cada paso. No dice nada del
# proceso y cuesta 321 K tokens en el parque (11.577 pasos), porque un UUID es
# texto sin patron y el tokenizador lo parte en pedacitos.
#
# Se borra POR NOMBRE (es lo que hay hoy, es explicito y se lee de un vistazo),
# pero solo si NADIE lo referencia: si el mismo texto aparece en otro lado del
# bot, alguien lo esta apuntando y se conserva. Pasa de verdad: un sub-bot de
# RP112 dejo dos `breakpoints` del depurador apuntando a pasos concretos.
#
# Cualquier OTRA clave con forma de identificador NO se toca: se reporta, para
# enterarnos sin borrar a ciegas algo que no conocemos.
CLAVES_ID = ("uid",)
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                  r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def contar_textos(o, cuenta=None):
    """Cuantas veces aparece cada texto con forma de UUID en TODO el taskbot.
    1 = solo su propia definicion (nadie lo referencia). 2+ = alguien lo apunta."""
    cuenta = collections.Counter() if cuenta is None else cuenta
    if isinstance(o, dict):
        for v in o.values():
            contar_textos(v, cuenta)
    elif isinstance(o, list):
        for v in o:
            contar_textos(v, cuenta)
    elif isinstance(o, str):
        for u in re.findall(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", o):
            cuenta[u] += 1
    return cuenta


def podar_ids(o, cuenta, quitados, otras, ruta=()):
    """Copia sin los identificadores internos no referenciados.
    `quitados` acumula (ruta, valor) de lo borrado, para que la verificacion
    sepa exactamente que se fue a proposito. `otras` acumula las claves con
    forma de id que NO se tocan."""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if isinstance(v, str) and UUID.match(v) and cuenta.get(v, 0) == 1:
                if k in CLAVES_ID:
                    quitados.append((".".join(ruta + (k,)), json.dumps(v, ensure_ascii=False)))
                    continue
                otras[k] = otras.get(k, 0) + 1
            out[k] = podar_ids(v, cuenta, quitados, otras, ruta + (k,))
        return out
    if isinstance(o, list):
        return [podar_ids(v, cuenta, quitados, otras, ruta) for v in o]
    return o


def hojas(o, ruta=()):
    """(ruta_sin_indices, valor) de cada hoja. Se omiten los indices de lista
    porque podar compacta las listas y los indices se corren."""
    if isinstance(o, dict):
        for k, v in o.items():
            yield from hojas(v, ruta + (k,))
    elif isinstance(o, list):
        for v in o:
            yield from hojas(v, ruta)
    else:
        yield ".".join(ruta), json.dumps(o, ensure_ascii=False)


# --- Regla 4: valores por defecto ------------------------------------------
# Si una clave vale casi siempre lo mismo, ese valor no esta eligiendo nada: se
# declara UNA vez en la cabecera y se omite donde coincide. Donde NO coincide se
# escribe, asi que la excepcion queda mas visible que antes (los 11 pasos
# apagados de RP053 pasan a ser las unicas lineas `disabled` del documento).
#
# El riesgo real es leer el cuerpo sin la cabecera y concluir mal. Tres defensas:
#   1. la cabecera se declara arriba de todo, con el conteo de cada omision;
#   2. el bloque de requisitos publica los conteos (11 pasos apagados), asi que
#      el lector puede cruzar: si dice 11 y encuentra 11, cuadra;
#   3. la verificacion exige que lo que falte sea EXACTAMENTE lo declarado.
#
# El umbral separa solo los casos claros, sin que nadie elija a mano: con 90%
# entran securelyRecorded/taskbotSelf/sessionTarget/operator/enabled/disabled
# (0 a 2 valores alternativos) y queda afuera `type` (67%, 22 alternativas), que
# es el que distingue el "5" texto del 5 numero.
# LISTA EXPLICITA de pares clave=valor que se omiten. No se elige por frecuencia.
#
# Se probo elegirlos por umbral (cualquier clave cuyo valor dominante superara el
# 90%) y estaba mal: la frecuencia no distingue "relleno del formato" de "dato que
# en ESTE bot casualmente se repite". Con ese criterio entraban `boolean: false`
# (el valor de una condicion del bot), `output: true` (que variables devuelve un
# sub-bot, o sea su contrato) y `browserType: CHROME`. Ninguno es ruido.
#
# Una lista explicita aqui es lo correcto, al reves que en el resto del pipeline:
# dice QUE SE BORRA, no que se conserva. Una clave que AA invente manana no esta
# en la lista, entonces se queda, que es el lado seguro.
# Cada par dice "aqui no aplica". El OTRO valor de esa clave SIEMPRE se conserva,
# y es el que importa: se omiten los 25.526 `operator: NONE` pero quedan los 107
# AND y los 38 OR, que son las condiciones compuestas (la cuarta perdida de
# informacion del parser viejo). Igual con sessionTarget: se van los 38.667 NONE
# y quedan los 100 LOCAL y 16 GLOBAL, que son sesiones reales compartidas.
DEFECTOS_OMITIBLES = (
    # --- Plomeria de Protocol Buffers, la libreria con la que AA serializa. ---
    # No son campos de AA: son el estado de un objeto Java en el instante en que
    # se guardo el archivo. La doc de protobuf los marca "For use by generated
    # code only": memoizedIsInitialized cachea si el objeto valido (1 = si),
    # memoizedSize el tamano serializado (-1 = sin calcular), memoizedHashCode
    # el hash (0 = sin calcular).
    ("memoizedIsInitialized", 1),
    ("memoizedSize", -1),
    ("memoizedHashCode", 0),

    # --- Marcadores de "aqui no aplica" del formato A360 ---
    ("securelyRecorded", False),            # el campo NO se capturo enmascarado.
                                            # Un `true` (0 en el parque) se conserva:
                                            # marca un campo con secreto.
    ("securelyRecordedRemoveDisabled", False),
    ("taskbotSelf", False),                 # la llamada NO apunta al bot mismo.
    ("sessionTarget", "NONE"),              # el valor no pertenece a ninguna sesion.
                                            # LOCAL/GLOBAL se conservan: son sesiones
                                            # reales, y GLOBAL viaja a los sub-bots.
    ("operator", "NONE"),                   # la condicion no encadena con otra.
                                            # AND/OR se conservan.
    ("disabled", False),                    # paso activo. Solo los apagados llevan marca.

    # --- Regla 5 (UI): criterio de busqueda que el paso NO usa ---------------
    # Cada objeto de pantalla guarda ~44 formas posibles de encontrar el control
    # y marca cuales usa. En el parque hay 24.242 `false` contra 1.597 `true`, y
    # la clave NO existe fuera de un uiObject (comprobado en los 15 bots), asi que
    # este par queda acotado a UI sin necesitar una regla de alcance.
    # OJO: `enabled` NO tiene nada que ver con `disabled`. `disabled: true` dice
    # que el PASO no corre y se conserva siempre. `enabled: false` dice que esa
    # propiedad no se usa para ubicar el control; el paso corre igual.
    ("enabled", False),
)


def contar_pares(o, d=None):
    """Cuantas veces aparece cada par clave=valor escalar, EN ESTE BOT."""
    d = collections.defaultdict(collections.Counter) if d is None else d
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, (str, bool, int, float)):
                d[k][json.dumps(v, ensure_ascii=False)] += 1
            contar_pares(v, d)
    elif isinstance(o, list):
        for v in o:
            contar_pares(v, d)
    return d


def elegir_defectos(cuenta):
    """{clave: (valor_json, veces_que_valen_eso, veces_totales)} para los pares de
    DEFECTOS_OMITIBLES que aparezcan en este bot. Los que no aparecen no se
    declaran, para no llenar la cabecera de lineas que no aplican."""
    out = {}
    for k, v in DEFECTOS_OMITIBLES:
        vals = cuenta.get(k)
        if not vals:
            continue
        vj = json.dumps(v, ensure_ascii=False)
        c = vals.get(vj, 0)
        if c:
            out[k] = (vj, c, sum(vals.values()))
    return out


def podar_defectos(o, defectos, quitados, ruta=()):
    """Copia sin los pares clave=valor que coinciden con el defecto declarado."""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            d = defectos.get(k)
            if d is not None and isinstance(v, (str, bool, int, float)) \
                    and json.dumps(v, ensure_ascii=False) == d[0]:
                quitados.append((".".join(ruta + (k,)), d[0]))
                continue
            out[k] = podar_defectos(v, defectos, quitados, ruta + (k,))
        return out
    if isinstance(o, list):
        return [podar_defectos(v, defectos, quitados, ruta) for v in o]
    return o


def verificar_defectos(antes, despues, defectos):
    """[(motivo, detalle)] si la omision de defectos no cuadra.

    Existe porque el control general seria tautologico aqui: `podar_defectos`
    declara lo que quita y el control compara contra esa declaracion, asi que si
    la poda se equivoca, se equivocan las dos igual. Esto se calcula aparte,
    contando sobre los dos arboles:
      CUADRE   : para cada clave, apariciones antes == omitidas + las que quedan
      SOBREVIVE: ninguna aparicion que QUEDA puede tener el valor por defecto
                 (si quedo, es porque vale otra cosa; si no, se omitio de menos)
    """
    ca, cd = contar_pares(antes), contar_pares(despues)
    problemas = []
    for k, (v, c, n) in defectos.items():
        quedan = sum(cd.get(k, {}).values())
        if n != c + quedan:
            problemas.append(("CUADRE", "{}: {} antes, {} omitidas, {} quedan"
                              .format(k, n, c, quedan)))
        if cd.get(k, {}).get(v):
            problemas.append(("SOBREVIVE", "{} = {} quedo {} vez/veces con el valor por defecto"
                              .format(k, v, cd[k][v])))
    return problemas


def bloque_defectos(defectos):
    if not defectos:
        return []
    L = ["VALORES POR DEFECTO DE ESTE BOT",
         "-" * 78,
         "Si una de estas claves NO aparece en un paso, su valor es el de aqui abajo.",
         "Se escriben solo donde valen otra cosa, asi que cada aparicion es una excepcion.",
         ""]
    for k, (v, c, n) in sorted(defectos.items()):
        otros = n - c
        L.append("  {:<32} = {:<14} ({:,} de {:,}; se escriben las {:,} excepcion/es)".format(
            k, v[:14], c, n, otros))
    L.append("")
    return L


def bloque_ui(n_vacios, n_defs, n_refs):
    """Declara en la cabecera del sub-bot lo que hicieron las reglas 6 y 7."""
    if not (n_vacios or n_refs):
        return []
    L = ["OBJETOS DE PANTALLA DE ESTE BOT", "-" * 78]
    if n_vacios:
        L.append("  Un `value` ausente dentro de un objeto de pantalla es una casilla vacia")
        L.append("  de tipo {}. Se omitieron {:,}. Donde el vacio es de otro tipo, se escribe."
                 .format(TIPO_VACIO_DEFECTO, n_vacios))
    if n_refs:
        L.append("  El mismo control capturado varias veces se escribe UNA vez, marcado con")
        L.append("  `{}: <hash>`, y las demas apariciones dicen `{}: <hash>`. El hash sale del"
                 .format(REF_ID, REF_A))
        L.append("  contenido, asi que dos fichas iguales dan el mismo hash. {:,} ficha(s) unica(s),"
                 .format(n_defs))
        L.append("  {:,} referencia(s). Ninguna referencia sale de este sub-bot.".format(n_refs))
    L.append("")
    return L


# --- Regla 6: la casilla vacia que quedo con su etiqueta de tipo -------------
# Regla 1 borra las claves vacias, pero un `value` de A360 que no lleva dato
# igual sobrevive con una sola clave adentro: `{"type": "STRING"}`. Es la misma
# casilla sin usar de siempre, solo que trae puesta la etiqueta del tipo.
#
# Se borra ENTERA y se declara el tipo en la cabecera. Donde el vacio sea de otro
# tipo, se escribe: la excepcion queda visible.
#
# Acotado a los objetos de pantalla, que es donde se concentra (miles de casos en
# el bloque `criteria` de cada control). Fuera de uiObject NO se toca: ahi el
# `value` vacio pertenece al contrato del paso (el `cc` y el `bcc` de un correo)
# y conviene verlo escrito.
UI_CLAVE = "uiObject"
TIPO_VACIO_DEFECTO = "STRING"


def podar_valor_solo_tipo(o, quitados, ruta=(), ui=False):
    """Copia sin los `value` que dentro de un uiObject quedaron con solo `type`.
    `quitados` acumula la hoja borrada, para que la verificacion exija que lo que
    falta sea EXACTAMENTE esto."""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            dentro = ui or k == UI_CLAVE
            if (dentro and k == "value" and isinstance(v, dict)
                    and set(v) == {"type"} and v["type"] == TIPO_VACIO_DEFECTO):
                quitados.append((".".join(ruta + (k, "type")),
                                 json.dumps(v["type"], ensure_ascii=False)))
                continue
            out[k] = podar_valor_solo_tipo(v, quitados, ruta + (k,), dentro)
        return out
    if isinstance(o, list):
        return [podar_valor_solo_tipo(v, quitados, ruta, ui) for v in o]
    return o


# --- Regla 7: objetos de pantalla repetidos ---------------------------------
# El mismo control capturado N veces se guarda entero N veces (unas 330 lineas
# cada una). En T016_LoginSAMP el campo de contrasena aparece 4 veces, una por
# rama de credencial: 1.320 lineas para describir un solo campo.
#
# Se escribe una vez y las demas lo referencian. El nombre es el HASH DEL
# CONTENIDO, no un correlativo ni el `uniqueID` que trae AA:
#   - un correlativo depende del orden de recorrido: agregar un paso renumeraria
#     todo y ensuciaria el diff entre dos generaciones;
#   - el `uniqueID` no sirve de clave: 93 de los 726 objetos del parque no lo
#     tienen, y hay 8 casos en RP091 con el mismo uniqueID y contenido distinto.
#     Colapsarlos por ese campo uniria dos cosas que no son iguales.
# El hash es a la vez el nombre y la prueba de equivalencia. 0 colisiones en el
# parque incluso truncando a 6 caracteres; se usan 12 por margen.
#
# ALCANCE: por sub-bot. Una referencia nunca sale de su bloque `===== BOT n/N`,
# asi que cada bloque se sigue leyendo solo. Cuesta 12 casos en todo el parque
# (~1,3%) y evita que un paso del sub-bot 11 apunte a una definicion del 9.
REF_ID, REF_A = "$id", "$ref"


def _firma(o):
    return __import__("hashlib").sha256(
        json.dumps(o, ensure_ascii=False, sort_keys=False).encode("utf-8")).hexdigest()[:12]


def dedup_uiobjects(o, defs):
    """Copia con los uiObject repetidos reemplazados por {'$ref': hash}.
    `defs` acumula {hash: subarbol} con la primera aparicion, que ademas se
    marca con {'$id': hash} para que el lector la encuentre."""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if k == UI_CLAVE and isinstance(v, dict) and v:
                h = _firma(v)
                if h in defs:
                    out[k] = {REF_A: h}
                else:
                    defs[h] = v
                    out[k] = dict([(REF_ID, h)] + list(dedup_uiobjects(v, defs).items()))
                continue
            out[k] = dedup_uiobjects(v, defs)
        return out
    if isinstance(o, list):
        return [dedup_uiobjects(v, defs) for v in o]
    return o


def expandir_uiobjects(o, defs):
    """Inverso exacto de dedup_uiobjects. Lo usa la verificacion antes de
    comparar, para que el control siga siendo arbol-contra-arbol."""
    if isinstance(o, dict):
        if set(o) == {REF_A}:
            return expandir_uiobjects(defs[o[REF_A]], defs)
        out = {}
        for k, v in o.items():
            if k == REF_ID:
                continue
            out[k] = expandir_uiobjects(v, defs)
        return out
    if isinstance(o, list):
        return [expandir_uiobjects(v, defs) for v in o]
    return o


def contar_refs(o):
    """(definiciones, referencias) para el informe."""
    d = r = 0
    if isinstance(o, dict):
        if set(o) == {REF_A}:
            return 0, 1
        if REF_ID in o:
            d = 1
        for v in o.values():
            a, b = contar_refs(v)
            d += a; r += b
    elif isinstance(o, list):
        for v in o:
            a, b = contar_refs(v)
            d += a; r += b
    return d, r


# --- Regla 3: escribir con sangria en vez de llaves --------------------------
# El JSON gasta en andamiaje: llaves, corchetes, comas y comillas en cada valor.
# Nada de eso es informacion. Se cambia por sangria (una corrida de espacios
# cuesta 1 token, sin importar cuantos sean) y `clave: valor`.
#
# NO nombra ninguna clave de AA. Recorre el arbol entero y escribe todo lo que
# encuentra, en el mismo orden. Si el arbol tiene N hojas, el texto tiene N
# valores: eso es lo que comprueba `verificar_texto`.

# Un texto va sin comillas si no puede confundirse con la sintaxis del formato:
# no arranca con un caracter estructural, no trae saltos ni comillas ni ':'.
_SIN_COMILLAS = re.compile(r'^[^\s"\'\\:#\[\]{}|>&*!%@`-][^\n"\\:]*$')


def escribir_valor(v):
    if isinstance(v, str) and v.strip() == v and _SIN_COMILLAS.match(v):
        return v
    return json.dumps(v, ensure_ascii=False)


# --- Regla 8: el atributo escalar en una linea -------------------------------
# Un atributo con un solo dato adentro ocupa cuatro lineas, y tres son andamiaje:
#
#     - name: sourceString                    - sourceString = "RP053"
#      value:                          ->
#       type: STRING
#       string: RP053
#
# El tipo no se tira: se codifica en como se escribe el valor. Un texto va entre
# comillas SIEMPRE en esta forma (aunque `escribir_valor` lo dejaria pelado), y un
# numero va pelado, asi que el "5" texto y el 5 numero siguen siendo distintos,
# que es justo lo que hace que `type` no entre en la lista de defectos omitibles.
#
# Cuando la pareja tipo/casilla no es la natural (un FILE que guarda su ruta en
# `expression`, por ejemplo) el tipo se escribe: `- filePath = FILE.expression
# "file://$sRuta-Log$"`. Nunca se adivina.
#
# NO nombra ninguna clave de AA: el patron es de forma (un dict de dos claves con
# un `value` de a lo sumo dos). Un atributo con mas datos adentro no entra y se
# escribe como siempre.
COLAPSO = " = "
_NATURAL = {"STRING": "string", "NUMBER": "number", "BOOLEAN": "boolean"}
_ESCALARES = ("string", "number", "boolean", "expression")


def _colapsable(v):
    """(nombre, tipo, casilla, valor) si el elemento de lista es un atributo
    escalar; None si no. `casilla` y `valor` son None cuando solo trae el tipo."""
    if not (isinstance(v, dict) and set(v) == {"name", "value"}
            and isinstance(v["name"], str)):
        return None
    val = v["value"]
    if not isinstance(val, dict) or "type" not in val or not isinstance(val["type"], str):
        return None
    resto = [k for k in val if k != "type"]
    if not resto:
        return (v["name"], val["type"], None, None)
    if len(resto) == 1 and resto[0] in _ESCALARES \
            and isinstance(val[resto[0]], (str, int, float, bool)):
        return (v["name"], val["type"], resto[0], val[resto[0]])
    return None


def _escribir_colapso(nombre, tipo, casilla, valor):
    if casilla is None:
        return "{}{}{}.".format(nombre, COLAPSO, tipo)
    marca = "" if _NATURAL.get(tipo) == casilla else "{}.{} ".format(tipo, casilla)
    return "{}{}{}{}".format(nombre, COLAPSO, marca,
                             json.dumps(valor, ensure_ascii=False))


def contar_colapsadas(o):
    """Cuantas hojas absorbe la regla 8. Lo necesita `verificar_texto`, que cuenta
    lineas contra hojas: una linea colapsada vale por 2 o 3 hojas."""
    n = 0
    if isinstance(o, dict):
        for v in o.values():
            n += contar_colapsadas(v)
    elif isinstance(o, list):
        for v in o:
            c = _colapsable(v)
            if c:
                n += 2 if c[2] is not None else 1
            else:
                n += contar_colapsadas(v)
    return n


def render(o, ind=0):
    """Arbol -> texto con sangria. Total: no filtra, no reordena, no resume.

    El paso de sangria es de 1 espacio, no 2. El arbol de un bot tiene 14 niveles
    de profundidad promedio y hasta 27, y cada linea paga la profundidad entera:
    con paso 2 los espacios son el 62% del archivo. Bajar a 1 no toca ni una
    hoja ni el orden, solo corre el texto a la izquierda.
    """
    p = " " * ind
    L = []
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, (dict, list)) and v:
                L.append("{}{}:".format(p, k))
                L.append(render(v, ind + 1))
            else:
                L.append("{}{}: {}".format(p, k, escribir_valor(v)))
    elif isinstance(o, list):
        for v in o:
            col = _colapsable(v)
            if col:
                L.append("{}- {}".format(p, _escribir_colapso(*col)))
            elif isinstance(v, (dict, list)) and v:
                sub = render(v, ind + 1).split("\n")
                L.append("{}- {}".format(p, sub[0].lstrip()))   # el 1er campo en la misma linea
                L.extend(sub[1:])
            else:
                L.append("{}- {}".format(p, escribir_valor(v)))
    else:
        L.append("{}{}".format(p, escribir_valor(o)))
    return "\n".join(x for x in L if x.strip())


def _tipos_implicitos(o):
    """Los `type` que la regla 8 NO escribe porque van implicitos en la forma del
    valor. Es lo unico que absorbe el colapso: el nombre queda como clave y el
    dato queda como valor, los dos visibles."""
    out = set()
    if isinstance(o, dict):
        for v in o.values():
            out |= _tipos_implicitos(v)
    elif isinstance(o, list):
        for v in o:
            c = _colapsable(v)
            if c and c[2] is not None and _NATURAL.get(c[1]) == c[2]:
                out.add(json.dumps(c[1], ensure_ascii=False))
            else:
                out |= _tipos_implicitos(v)
    return out


def verificar_texto(arbol, texto):
    """[(motivo, detalle)] si el texto no contiene todo el arbol.

    Dos controles, y ninguno usa `escribir_valor` para armar la aguja, porque
    entonces estaria comprobando el render contra si mismo:
      1. CONTEO : cuantas lineas con valor hay contra cuantas hojas tiene el arbol,
                  descontando las que la regla 8 mete en una sola linea
      2. AUSENTE: cada valor distinto del arbol aparece en alguna parte del texto,
                  salvo los tipos que la regla 8 deja implicitos en la forma
    """
    # Una linea ABRE un sub-bloque si termina en ':' ("attributes:", "- value:").
    # Cualquier otra linea lleva un valor. Es exacto, no una aproximacion: un
    # valor sin comillas nunca puede terminar en ':' porque _SIN_COMILLAS
    # prohibe ese caracter, y uno con comillas termina en '"'.
    hs = [v for _r, v in hojas(arbol)]                       # v ya es json.dumps(valor)
    absorbidas = contar_colapsadas(arbol)
    implicitos = _tipos_implicitos(arbol)
    lineas = [l for l in texto.split("\n") if l.strip() and not l.rstrip().endswith(":")]
    problemas = []
    if len(lineas) != len(hs) - absorbidas:
        problemas.append(("CONTEO", "{} hojas - {} colapsadas = {} esperadas, {} lineas con "
                          "valor en el texto".format(len(hs), absorbidas,
                                                     len(hs) - absorbidas, len(lineas))))
    for v in set(hs):
        crudo = json.loads(v)
        aguja = crudo if isinstance(crudo, str) else v
        if str(aguja) not in texto and v not in texto and v not in implicitos:
            problemas.append(("AUSENTE", v[:70]))
    return problemas


def verificar(orig, limpio, quitados=()):
    """[(motivo, ruta, valor, veces)] de lo que no cuadra entre los dos arboles.
    Se compara en las DOS direcciones y por ruta:
      PERDIDA   : una hoja no vacia del original no esta en el limpio
      INVENTADA : el limpio tiene una hoja que el original no tenia ahi

    `quitados` es lo que se borro A PROPOSITO (los identificadores internos):
    se descuenta del lado del original. No es "ignorar los uid": es exigir que
    lo que falta sea EXACTAMENTE esa lista, ni uno mas ni uno menos. Si la regla
    borrara de mas, la diferencia deja de cuadrar y sale como PERDIDA."""
    antes = collections.Counter(h for h in hojas(orig) if not es_vacio(json.loads(h[1])))
    antes -= collections.Counter(quitados)
    despues = collections.Counter(hojas(limpio))
    return ([("PERDIDA",) + k + (n,) for k, n in (antes - despues).items()] +
            [("INVENTADA",) + k + (n,) for k, n in (despues - antes).items()])


def limpiar_bloques(cabecera, bloques, hacer_verificar=True, portada=()):
    """Limpia EN MEMORIA. Devuelve (cabecera_nueva, bloques_texto, resumen, problemas).

    `bloques` viene de C.construir() o de C.leer_bloques(). No toca disco: el
    limpio es un paso intermedio, no un entregable.
    `extra_cabecera` son lineas que se insertan en la cabecera (las usa main.py
    para meter el inventario de requisitos externos).
    El resumen trae `arboles` = [(nombre, arbol_podado)], para que otro paso los
    recorra sin volver a parsear nada."""
    # C.construir da (titulo, meta, obj); C.leer_bloques da (titulo, meta, texto, obj).
    # Se acepta cualquiera de las dos formas para que el paso sirva igual venga de
    # memoria o de un archivo.
    bloques = [b if len(b) == 4 else (b[0], b[1], None, b[2]) for b in bloques]
    salida, problemas, borrados, arboles, defectos_bot = [], [], [], [], []
    detalles = []
    n_antes = n_despues = n_ids = n_vacios = n_ui_ref = n_off = 0
    otras_ids = {}
    for titulo, meta, _txt, obj in bloques:
        if isinstance(obj, C._NoParsea):
            raise SystemExit("El consolidado tiene un bloque ilegible ({}): {}".format(titulo, obj))
        # Regla 2 primero (los ids son valores no vacios; si se corriera despues,
        # podar ya los habria dado por buenos y daria igual, pero asi el conteo
        # de hojas separa limpio lo que quito cada regla).
        quitados = []
        sin_ids = podar_ids(obj, contar_textos(obj), quitados, otras_ids)
        n_ids += len(quitados)
        # Regla 1
        pod = podar(sin_ids, borrados)
        # Regla 6: despues de podar los vacios, porque el `value` sin dato recien
        # queda con su sola etiqueta de tipo cuando el resto del struct se fue.
        n_vac = len(quitados)
        pod = podar_valor_solo_tipo(pod, quitados)
        n_vac = len(quitados) - n_vac
        n_vacios += n_vac
        # Regla 4: se calcula sobre el arbol YA podado, para que los conteos
        # reflejen lo que de verdad queda en el documento.
        defectos = elegir_defectos(contar_pares(pod))
        antes_def = pod
        pod = podar_defectos(pod, defectos, quitados)
        # Omitir defectos puede dejar objetos vacios (si TODAS sus claves eran el
        # defecto). La poda de vacios ya corrio, asi que hay que volver a pasarla:
        # esos objetos no tienen ninguna hoja, por eso no cambia ningun conteo.
        pod = podar(pod)
        if hacer_verificar:
            problemas += [(titulo, m, "(defectos)", d, 1)
                          for m, d in verificar_defectos(antes_def, pod, defectos)]
        defectos_bot.append((titulo.split(": ")[-1], defectos))
        # Regla 7: por sub-bot, asi que `defs` se reinicia en cada bloque y ninguna
        # referencia sale de aqui.
        defs = {}
        pod = dedup_uiobjects(pod, defs)
        n_def, n_ref = contar_refs(pod)
        n_ui_ref += n_ref
        # Paso 2b: numerar los pasos y separar el detalle. `numerado` es el arbol
        # completo con `$n`; `trabajo` es lo que se lee; `det` lo que se guarda.
        subbot = titulo.split(": ")[-1]
        numerado = P.numerar(pod)
        trabajo, det = P.partir(numerado, subbot)
        detalles.append((subbot, det))
        n_off += len(det["off"])
        # El arbol EXPANDIDO es el que se compara y el que se le pasa a los pasos
        # de mas abajo: requisitos.py cuenta pasos de UI y capturas, y con las
        # referencias sin resolver contaria de menos.
        rearmado = P.desnumerar(P.unir(trabajo, det, subbot))
        exp = expandir_uiobjects(rearmado, defs) if defs else rearmado
        n_antes += sum(1 for _ in hojas(obj))
        n_despues += sum(1 for _ in hojas(trabajo))
        # Regla 3: del arbol podado al texto con sangria.
        txt = render(trabajo)
        cab_bloque = (bloque_defectos(defectos) + bloque_ui(n_vac, n_def, n_ref)
                      + P.bloque_cabecera(det, subbot, trabajo))
        if hacer_verificar:
            # (a) arbol contra arbol: exacto, por ruta. Se compara el REARMADO y
            #     EXPANDIDO: si partir/unir o dedup/expandir no fueran inversas
            #     exactas, salta aqui. Es el control de "no se perdio nada".
            problemas += [(titulo,) + p for p in verificar(obj, exp, quitados)]
            # (b) arbol contra texto: que el render no se haya comido nada
            problemas += [(titulo, m, "(render)", d, 1) for m, d in verificar_texto(trabajo, txt)]
            # (c) suficiencia: que lo que quedo en el documento alcance para auditar.
            #     El control (a) no lo puede ver, porque la suma de los dos
            #     archivos le da bien aunque el de trabajo quede inservible.
            problemas += [(titulo, m, "(suficiencia)", d, 1)
                          for m, d in P.verificar_suficiencia(numerado, trabajo, det, subbot,
                                                              "\n".join(cab_bloque))]
        salida.append((titulo, meta + ["# " + l for l in cab_bloque], txt))
        arboles.append((subbot, exp))

    # La descripcion es PROPIA; del consolidado solo se hereda lo que hay debajo
    # de MARCA_DESC (sub-bots, grafo, avisos). Heredar la descripcion entera hacia
    # que el limpio afirmara "el JSON esta COMPLETO y sin modificar".
    # `portada` la arma main.py (titulo + ficha del bot) porque los conteos salen
    # de requisitos.py, que corre despues de este paso.
    cab = list(portada) + C.datos_de_cabecera(cabecera) + NOTA_FINAL
    resumen = {"taskbots": len(bloques), "hojas_antes": n_antes, "hojas_despues": n_despues,
               "ids_quitados": n_ids, "otras_ids": otras_ids, "arboles": arboles,
               "defectos": defectos_bot, "elem_lista_borrados": sum(borrados),
               "valores_vacios": n_vacios, "ui_referencias": n_ui_ref,
               "detalles": detalles, "pasos_apagados": n_off}
    return cab, salida, resumen, problemas


def limpiar(carpeta, out=None, hacer_verificar=True, entrada=None, extra_cabecera=()):
    """CLI: lee el consolidado de disco, limpia y escribe. Solo para inspeccionar
    el intermedio a mano; el pipeline usa `limpiar_bloques`."""
    carpeta = os.path.abspath(carpeta)
    entrada = entrada or os.path.join(carpeta, "consolidado.aa.txt")
    if not os.path.exists(C._long(entrada)):
        raise SystemExit("No existe {}. Corre primero: "
                         "python exporter/consolidar.py {}".format(entrada, carpeta))
    cabecera, bloques = C.leer_bloques(entrada)
    cab, salida, resumen, problemas = limpiar_bloques(cabecera, bloques, hacer_verificar, extra_cabecera)
    out = out or os.path.join(carpeta, "limpio.aa.txt")
    C.escribir(out, cab, salida)
    resumen["bot"] = os.path.basename(carpeta.rstrip(os.sep))
    resumen["bytes_antes"] = os.path.getsize(C._long(entrada))
    resumen["bytes_despues"] = os.path.getsize(C._long(out))
    return out, resumen, problemas


def informe(out, r, problemas, verificado):
    """`out` puede ser None: en el pipeline no se escribe archivo intermedio."""
    pct = lambda a, b: 100 * (1 - b / max(a, 1))
    print("=" * 66)
    print("LIMPIEZA - {}".format(r.get("bot", "")))
    print("=" * 66)
    print("  Taskbots    : {}".format(r["taskbots"]))
    print("  Hojas JSON  : {:,} -> {:,}   (-{:.1f}%)".format(
        r["hojas_antes"], r["hojas_despues"], pct(r["hojas_antes"], r["hojas_despues"])))
    print("  Ids quitados: {:,}  (uid no referenciados)".format(r["ids_quitados"]))
    if r.get("valores_vacios"):
        print("  Casillas UI : {:,}  (`value` vacio con solo su tipo, dentro de objetos de pantalla)"
              .format(r["valores_vacios"]))
    if r.get("ui_referencias"):
        print("  Fichas UI   : {:,} referencia(s) a una ficha ya escrita en el mismo sub-bot"
              .format(r["ui_referencias"]))
    for k, n in sorted(r["otras_ids"].items()):
        print("  !! la clave '{}' tiene {} valor(es) con forma de identificador y nadie los"
              " referencia. NO se borran. Si corresponde, agregarla a CLAVES_ID.".format(k, n))
    if "bytes_antes" in r:
        print("  Chars       : {:,} -> {:,}   (-{:.1f}%)".format(
            r["bytes_antes"], r["bytes_despues"], pct(r["bytes_antes"], r["bytes_despues"])))
    if r["elem_lista_borrados"]:
        print("  !! {} elemento(s) de lista quedaron vacios y se borraron: las posiciones de"
              " esas listas se corrieron. Revisar si alguna es posicional.".format(
                  r["elem_lista_borrados"]))
    if out:
        print("  Archivo     : {}".format(out))
    if not verificado:
        print("  [verificacion] OMITIDA (--sin-verificar)")
    elif not problemas:
        print("  [verificacion] OK: toda hoja no vacia sigue en su ruta y no aparecio ninguna nueva")
    else:
        print("  [verificacion] {} PROBLEMA(S):".format(len(problemas)))
        for titulo, motivo, ruta, val, n in problemas[:20]:
            print("      {:<10} x{:<4} {:<45} {}".format(motivo, n, ruta[:45], val[:40]))
        raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser(description="Quita del consolidado las claves vacias.")
    ap.add_argument("carpeta", help="bots/<bot>/ (usa su consolidado.aa.txt)")
    ap.add_argument("--entrada", help="consolidado de entrada (default: <carpeta>/consolidado.aa.txt)")
    ap.add_argument("--out", help="salida (default: <carpeta>/limpio.aa.txt)")
    ap.add_argument("--sin-verificar", action="store_true")
    args = ap.parse_args()
    ver = not args.sin_verificar
    out, resumen, problemas = limpiar(args.carpeta, args.out, ver, args.entrada)
    informe(out, resumen, problemas, ver)


if __name__ == "__main__":
    main()
