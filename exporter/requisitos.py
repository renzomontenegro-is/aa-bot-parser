#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
requisitos.py - Inventario de lo que el export NO trae, CON evidencia y SIN veredicto.

Que hace y que NO
-----------------
Barre el arbol de cada taskbot y lista, exhaustivamente, los global values, las
credenciales, los archivos que algun comando toca, y los conteos (pasos, sub-bots,
pasos de UI, pasos desactivados). De cada cosa dice DONDE aparece: que taskbot,
que comando, que atributo.

Lo que NO hace, a proposito: decidir si un archivo es insumo o salida, ni decir
"esto hay que conseguirlo". Esa clasificacion la hacia el pipeline viejo y se
equivocaba: 23 de 74 archivos mal clasificados en 10 bots, corregido dos veces el
mismo dia. Fallaba porque es un JUICIO ("¿lo lee o lo escribe?") disfrazado de
regla, y se equivocaba en silencio presentandole al usuario un veredicto sin razon.

Aqui el codigo aporta lo que hace bien (barrer sin saltarse nada, contar exacto,
dar el mismo resultado siempre) y el agente aporta lo que hace bien (leer la
evidencia y decidir). Por eso cada archivo sale con el comando y el modo de
apertura al lado, en vez de con una etiqueta.

Por que no lo hace el agente solo: en RP053, 7 de los 17 global values aparecen
UNA sola vez en 72.805 tokens (entre ellos $@BucketSAMP$, que es el destino de
todo el proceso). En RP116 son 868.997 tokens. Un barrido no se los saltea; una
lectura, si.
"""
import collections
import re

# Extensiones que delatan una ruta. Sirve para DETECTAR, no para clasificar:
# lo que se detecta sale con su comando al lado y el lector juzga.
EXT = (r"txt|xlsx|xlsm|xls|csv|py|vbs|bat|json|xml|png|jpg|pdf|docx|doc|zip|log|"
       r"dll|exe|msg|html|htm|sql|crd|p12|pem")
RE_ARCHIVO = re.compile(r"[^\s\"'<>|]*\.(?:" + EXT + r")\b", re.I)
RE_GV = re.compile(r"\$@([^$]+)\$")
PAQUETES_UI = ("Recorder", "Keystrokes", "MouseClick", "Image")


def _cmd(n):
    return "{}.{}".format(n.get("packageName", "?"), n.get("commandName", "?"))


def _textos(o, raiz=True):
    """(valor_texto, ruta_de_claves) de cada hoja de texto DEL NODO, sin bajar a
    sus hijos: cada hijo se visita por su cuenta y atribuye sus propios valores.
    Sin este corte, todo se le adjudicaba al nodo mas externo (el ErrorHandler.try
    que envuelve el bot entero) y la evidencia no servia para nada."""
    def w(x, ruta, tope):
        if isinstance(x, dict):
            for k, v in x.items():
                if tope and k in ("children", "branches"):
                    continue
                yield from w(v, ruta + (k,), False)
        elif isinstance(x, list):
            for v in x:
                yield from w(v, ruta, False)
        elif isinstance(x, str):
            yield x, ruta
    return w(o, (), raiz)


def _credenciales(o, salida):
    """Punteros al vault: {name, lockerName, attributeName}. El secreto NO esta
    en el export; esto son etiquetas para buscarlo en el Control Room."""
    if isinstance(o, dict):
        if "lockerName" in o or "attributeName" in o:
            salida.append((o.get("name") or "?", o.get("lockerName") or "?",
                           o.get("attributeName") or "?"))
        for v in o.values():
            _credenciales(v, salida)
    elif isinstance(o, list):
        for v in o:
            _credenciales(v, salida)


def contar_apagados(nodos):
    """(apagados_explicitos, pasos_que_no_corren).

    Son dos numeros distintos y los dos importan: si alguien apaga un IF, sus
    hijos tampoco corren aunque no esten marcados. El tecnico viejo publicaba
    solo el segundo (25 en RP053), que dice cuanto codigo esta muerto pero no
    cuantas decisiones tomo el desarrollador (11)."""
    exp = tot = 0
    for n in nodos or []:
        if not isinstance(n, dict):
            continue
        off = bool(n.get("disabled"))
        if n.get("disabled") is True:
            exp += 1
        if off:
            tot += 1
        a, b = _apagados_hijos(n.get("children"), off)
        exp += a
        tot += b
        for br in n.get("branches") or []:
            off2 = bool(br.get("disabled")) or off
            if br.get("disabled") is True:
                exp += 1
            if off2:
                tot += 1
            a, b = _apagados_hijos(br.get("children"), off2)
            exp += a
            tot += b
    return exp, tot


def _apagados_hijos(nodos, heredado):
    exp = tot = 0
    for n in nodos or []:
        if not isinstance(n, dict):
            continue
        off = bool(n.get("disabled")) or heredado
        if n.get("disabled") is True:
            exp += 1
        if off:
            tot += 1
        a, b = _apagados_hijos(n.get("children"), off)
        exp += a
        tot += b
        for br in n.get("branches") or []:
            off2 = bool(br.get("disabled")) or off
            if br.get("disabled") is True:
                exp += 1
            if off2:
                tot += 1
            a, b = _apagados_hijos(br.get("children"), off2)
            exp += a
            tot += b
    return exp, tot


def recolectar(arboles, iter_nodos):
    """arboles = [(nombre_taskbot, arbol)]. Devuelve un dict con el inventario."""
    gv = collections.defaultdict(lambda: {"veces": 0, "bots": set(), "donde": None})
    cred = collections.defaultdict(set)
    arch = collections.defaultdict(lambda: {"bots": set(), "usos": collections.Counter()})
    cont = {"pasos": 0, "ui": 0, "off": 0, "off_total": 0, "subbots": len(arboles)}
    triggers = []

    for nombre, arbol in arboles:
        for t in (arbol.get("triggers") or []):
            triggers.append((nombre, _cmd(t) if isinstance(t, dict) else str(t)))

        # Credenciales: se buscan en el arbol entero (pueden colgar de cualquier lado)
        c = []
        _credenciales(arbol, c)
        for x in set(c):
            cred[x].add(nombre)

        e, t = contar_apagados(arbol.get("nodes"))
        cont["off"] += e
        cont["off_total"] += t
        for n in iter_nodos(arbol.get("nodes")):
            cont["pasos"] += 1
            if n.get("packageName") in PAQUETES_UI:
                cont["ui"] += 1
            cmd = _cmd(n)
            # modo de apertura, si el nodo lo declara (dato del export, no criterio nuestro)
            modo = ""
            for txt, ruta in _textos(n):
                if ruta and ruta[-1] == "fileAccessMode":
                    modo = " [{}]".format(txt)
            for txt, ruta in _textos(n):
                if ruta and ruta[-1] in ("packageName", "commandName", "fileAccessMode"):
                    continue
                for g in RE_GV.findall(txt):
                    d = gv[g.strip()]
                    d["veces"] += 1
                    d["bots"].add(nombre)
                    if d["donde"] is None:
                        d["donde"] = "{} / {}".format(cmd, ruta[-1] if ruta else "?")
                for a in RE_ARCHIVO.findall(txt):
                    a = a.strip().lstrip("/")
                    if len(a) < 5:
                        continue
                    d = arch[a]
                    d["bots"].add(nombre)
                    d["usos"]["{}{}".format(cmd, modo)] += 1
    return {"gv": gv, "cred": cred, "arch": arch, "cont": cont, "triggers": triggers}


def render(inv, avisos_consolidar=()):
    """El bloque de texto para el encabezado del proceso tecnico."""
    L = []
    c = inv["cont"]
    L.append("REQUISITOS EXTERNOS (lo que el export de AA no trae)")
    L.append("-" * 12)
    L.append("Inventario con evidencia y SIN clasificar: de cada cosa se dice donde aparece")
    L.append("y que comando la toca. Decidir cual hay que conseguir es lectura, no regla.")
    L.append("")
    L.append("Conteos: {} pasos AA | {} sub-bots | {} pasos de UI".format(
        c["pasos"], c["subbots"], c["ui"]))
    L.append("         {} pasos apagados por el desarrollador, que dejan {} pasos sin correr".format(
        c["off"], c["off_total"]))
    if c["ui"] == 0:
        L.append("         (cero interaccion por pantalla: el bot no toca ninguna UI)")
    L.append("")

    L.append("GLOBAL VALUES ({}) - Control Room > Manage > Global values".format(len(inv["gv"])))
    if not inv["gv"]:
        L.append("  (ninguno)")
    for g, d in sorted(inv["gv"].items()):
        L.append("  $@{}$".format(g))
        L.append("      {} uso(s) en {} | primero en: {}".format(
            d["veces"], ", ".join(sorted(d["bots"]))[:60], d["donde"]))
    L.append("")

    L.append("CREDENCIALES ({}) - Control Room > Manage > Credentials".format(len(inv["cred"])))
    L.append("  El export trae solo el puntero (locker / credencial / atributo), nunca el secreto.")
    if not inv["cred"]:
        L.append("  (ninguna)")
    for (nom, locker, attr), bots in sorted(inv["cred"].items()):
        L.append("  {} / locker \"{}\" / atributo \"{}\"".format(nom, locker, attr))
        L.append("      usado por: {}".format(", ".join(sorted(bots))))
    L.append("")

    L.append("ARCHIVOS QUE ALGUN COMANDO TOCA ({})".format(len(inv["arch"])))
    L.append("  Sin etiqueta de entrada/salida: al lado va el comando y, si el export lo")
    L.append("  declara, el modo de apertura. Una ruta con $Variables$ se arma en ejecucion.")
    if not inv["arch"]:
        L.append("  (ninguno)")
    for a, d in sorted(inv["arch"].items()):
        L.append("  {}".format(a))
        L.append("      {}   ({})".format(
            "  ".join("{} x{}".format(u, n) for u, n in d["usos"].most_common(4)),
            ", ".join(sorted(d["bots"]))[:60]))
    L.append("")

    L.append("DISPARADOR")
    if inv["triggers"]:
        for nombre, t in inv["triggers"]:
            L.append("  {} declara el trigger: {}".format(nombre, t))
    else:
        L.append("  Ningun taskbot declara trigger propio: se lanza desde afuera.")
        L.append("  Revisar en el Control Room la programacion (Schedule) o la cola asociada.")
    L.append("")

    if avisos_consolidar:
        L.append("AVISOS DEL EXPORT")
        for a in avisos_consolidar:
            L.append("  - {}".format(a))
        L.append("")
    return L
