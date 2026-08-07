AA Bot Parser — Auditor de bots de Automation Anywhere (export A360), sin abrir AA.

Auditar = explicar cómo funciona el bot, detectar malas prácticas y evaluar la
migración a Python + n8n (la migración es una salida, no el objetivo). Genérico:
sirve para cualquier export; ningún nombre de bot va hardcodeado en el código.

Tres archivos por bot:
- proceso_tecnico_<CODIGO>.md: lo genera el código, determinista (Python puro, sin IA). El que se lee.
- detalle_<CODIGO>.md: el respaldo, generado junto al técnico. NO se lee de corrido: se busca
  una cosa con la clave que el técnico escribe ($off = paso apagado, $id = control).
- proceso_negocio_<CODIGO>.md: lo redacta el agente (ver rúbrica), a partir del técnico y de
  los archivos externos que consiga el usuario.

Contrato: tecnico + detalle == el export. Se verifica en cada corrida; si no cuadra,
no se genera ninguno de los dos.

=========================================================================
ANTES DE EMPEZAR
=========================================================================
- pip install -r requirements.txt  (solo "requests", para descargar del Control Room).
- Si vas a leer assets_externos/ (Excel, macros) al redactar: pip install openpyxl oletools.
- Si el bot ya esta descargado en Bots/ y usas --carpeta, no hace falta nada de lo anterior.

=========================================================================
WORKFLOW DEL AGENTE
=========================================================================
PASO 1. ARRANQUE: UN SOLO COMANDO, SIN INVESTIGAR
La carpeta en Bots/ ES el nombre del bot. No hay que deducir nada.
- Si el usuario da un NOMBRE:
      Test-Path "Bots\<nombre exacto>\export"
    true  -> python exporter/main.py --carpeta "Bots\<nombre>"   (offline, sin API)
    false -> python exporter/main.py <fileId>
- Si da un fileId: python exporter/main.py <fileId>. El comando resuelve el nombre
  solo, y si ya esta descargado no vuelve a bajarlo.
PROHIBIDO (quema tokens): listar Bots/ para "ver que hay", abrir bots/.export_index.json,
inspeccionar la carpeta de otro bot, listar exporter/.

PASO 2. MAPA DE LINEAS
main.py imprime al final el rango de lineas de cada sub-bot dentro del tecnico.
Usar esos rangos para leer por bloque; no grepear el archivo entero buscando limites.

PASO 3. ARCHIVOS EXTERNOS (por defecto, NO)
Decidir solo y decirlo en una linea. Default: seguir de largo. Pedir solo si un archivo es
IMPRESCINDIBLE (sin el una seccion entera del negocio queda en blanco, no menos precisa),
y siempre como opcion ("Si algun dia tenes a mano X, lo agrego. Mientras tanto sigo sin el").
NUNCA pedir: archivos cuyo contenido se deduce del uso que el bot les da; nada que huela a
secreto (si la variable se llama $contraseña$, $token$, $clave$ o similar, no se pide y va
directo a la seccion 4 como "no se ve desde el export"); mas de dos o tres archivos.

SIN LOS ARCHIVOS TAMBIEN SE AUDITA. De un archivo que el bot ABRE, el tecnico muestra que
busca adentro, en que orden, a que variable va cada cosa y que hace con ella. Tres niveles,
cada uno se escribe distinto:
- lo que el bot HACE con el archivo: como hecho (sale del tecnico).
- la estructura deducida del uso: marcada como deducida, de que se dedujo.
- el CONTENIDO real (valores): no se inventa. Va a la seccion 4 como pregunta abierta.
Un archivo que el bot solo mueve y nunca abre es el unico caso donde no se puede deducir
nada de su interior: decirlo explicito.

PASO 4. REDACTAR Bots/<bot>/proceso_negocio_<CODIGO>.md (ver rubrica abajo), con lo que haya.
Si el usuario consigue archivos despues, se reescriben las partes que dependian de ellos.

Si el pipeline se detiene con un problema de verificacion: NO seguir. Significa que un valor
del export no llego al documento. Reportarlo tal cual y arreglar la causa antes de auditar.

=========================================================================
COMO LEER EL proceso_tecnico
=========================================================================
Estructura: descripcion de que se quito y por que | ----- fin de la descripcion del paso -----
| INDICE (sub-bots en orden) | QUIEN LLAMA A QUIEN (el grafo) | un bloque por sub-bot:
"===== BOT 1/N: <nombre>" con # VALORES POR DEFECTO (leer siempre), # OBJETOS DE PANTALLA
(solo si tiene UI) y el arbol con sangria.
El inventario de lo externo (global values, credenciales, archivos, disparador) NO esta en el
documento: los conteos los imprime main.py por consola. Lo demas sale de leer el tecnico.

El cuerpo es el JSON del bot con sangria, nada traducido (nombres de paquete/comando reales):

  nodes:
    - commandName: trim
      packageName: String
      attributes:
        - name: sourceString
          value:
            type: STRING
            string: RP053

Como interpretarlo:
- packageName + commandName = la accion real de AA (String.trim, Email.SendMailV2).
- children = pasos de adentro; branches = ramas (else, catch, finally).
- $var$ = variable del bot; $@var$ = global value del Control Room (no esta en el export).
- Un value con lockerName/attributeName = puntero al vault: el secreto no esta.
- taskbotFile = llamada a sub-bot; taskbotInput = sus argumentos.
- "disabled: true" = paso APAGADO.
- Pasos de UI (Recorder o Keystrokes): objNode.name = el control, criteria.* = criterios.

NUMERACION DE PASOS: cada paso lleva `$n`, correlativo dentro de su sub-bot. Es la direccion
del paso: citarlo en el negocio, cruzarlo con una linea de log, ir al detalle. Plana a
proposito (el arbol llega a 15 niveles; la jerarquia la muestra la sangria). Se calcula por
posicion: si alguien inserta un paso, los `$n` de ahi para abajo se corren.

LO QUE ESTA EN EL DETALLE Y COMO LLEGAR: el tecnico no trae todo; el detalle esta
direccionado desde el propio tecnico:
- `$off: <sub-bot>#<n>  ->  N paso(s): ...` = paso apagado. Para el cuerpo: grepear
  `----- <sub-bot>#<n> -----` en el detalle.
- Un control trae `$id: <hash>`; sus coordenadas, HTML volcado y criterios NO usados estan en
  `----- pantalla <hash> -----`.
- Las casillas constantes se declaran ENTERAS en la cabecera del sub-bot; el detalle solo
  guarda el bloque original.
Cada clave es unica: se llega con un grep exacto. Antes de abrir el detalle, preguntarse si
hace falta: para el resumen, el paso a paso, las malas practicas y la migracion casi nunca
(la fragilidad de un clic sale de los criterios que SI quedaron en el tecnico).

ATRIBUTO EN UNA LINEA: un atributo con un solo dato se escribe `- nombre = valor`. El tipo va
implicito: comillas = texto, pelado = numero, true/false = booleano. Si la pareja tipo/casilla
no es la natural: `- filePath = FILE.expression "file://$sRuta-Log$"`.

VALORES POR DEFECTO: cada sub-bot declara arriba que pares clave=valor se omitieron y cuantas
veces. Si la clave no aparece en un paso, vale lo declarado. El otro valor SIEMPRE se escribe:
"disabled: true" = paso apagado; "operator: AND" = condicion compuesta; "sessionTarget:
GLOBAL" = sesion que viaja a un sub-bot.

OBJETOS DE PANTALLA (solo sub-bots con UI): un `value` ausente dentro de un uiObject = casilla
vacia STRING. Una ficha de control repetida se escribe UNA vez ($id: hash) y las demas
apariciones dicen $ref: hash. Ninguna referencia sale de su sub-bot: el bloque se lee solo.

CRITERIOS DE BUSQUEDA (lo mas util de un paso de UI): dentro de `criteria` solo se escriben
los que el bot USA (`enabled: false` es uno de los pares por defecto). Los que ves con
"enabled: true" son los que ubican el control, y de ahi sale la fragilidad del paso:
  //select[@id='CodigoRamo']            ancla en un id, robusto
  //div[@id='menu']/ul[1]/li[5]/a[1]    "el 5to item del menu", se rompe si agregan uno
  /html/body/div[26]/div[3]/...         posicion absoluta, el mas fragil de todos
  Path = 1|1|1|-1|3|1|...               sin DOM, ruta de accesibilidad de Windows
Una ficha puede traer `$sin-ancla: true`: el export no marco NI UN criterio en uso. Ahi la
regla se apaga y van los criterios COMPLETOS (ninguno al detalle), todos con enabled false.
No es un hueco del parser: es un control que el bot ubica sin ninguna ancla declarada, y
como hallazgo de fragilidad es de los mas fuertes que hay.

=========================================================================
RUBRICA DE proceso_negocio_<CODIGO>.md
=========================================================================
Lector objetivo: alguien que va a reconstruir este proceso y hoy no sabe nada de el
(entiende de sistemas, no el negocio del bot). Debe leer el resumen ejecutivo una sola vez y
quedar con el modelo mental completo: que entra, que sale y que se transforma en el medio.

Entradas: el tecnico + los archivos de assets_externos/ (Excel con openpyxl; VBA de .xlsm con
olevba) + los global values/credenciales que el usuario haya conseguido.

1) REGLAS DE VERACIDAD
- Nunca inventar: cada afirmacion se sostiene en una linea del tecnico o en un archivo externo.
- Los numeros (delays, pasos apagados, veces que se invoca un sub-bot, rutas fijas, pasos de
  UI) se CUENTAN en el tecnico, no se estiman. El bloque de requisitos ya trae varios contados.
- Naturaleza de los sistemas por evidencia: Browser, openbrowser, url=, http, taskkill chrome
  = app web; Excel, Email, Folder, File = Office/archivos. Clics sobre un navegador son UI
  sobre app web (fragil), no escritorio.
- Valores reales inline (de assets_externos) con su fuente. Lo calculado en runtime (fechas,
  respuestas de API, filas iteradas) se describe por su formula, no con un valor fijo.
- Separar SIEMPRE lo que el codigo hace de lo que parece significar. Si el bot ramifica por un
  criterio superficial (nombre de archivo, texto de celda, codigo), decirlo asi aunque exista
  una interpretacion de negocio obvia. Esa interpretacion que el codigo no sostiene NO se
  escribe como hecho: se declara como pregunta abierta en la seccion 4.
- Cada "no se ve" con su ruta concreta (es trabajo del equipo RPA, no "preguntar al equipo"):
  global values, endpoints y SMTP en Control Room > Manage > Global values; credenciales en
  Manage > Credentials; logica de una macro en el .xlsm (olevba); intencion de los clics en
  las capturas del recorder.

2) REGLAS DE EJEMPLOS
El documento se entiende por ejemplos, no por descripciones. Cada vez que un dato cambia de
forma, nombre o lugar, va acompanado de su antes y despues. Si o si: contenido de los archivos
que el proceso mueve (bloque de 2-3 lineas); estructura de carpetas que crea (arbol ASCII);
filas de Excel que lee/escribe (tabla markdown, fila del bot en negrita); renombrados (origen
--► destino); cargas/envios a un sistema (ruta local --► remota completa); filtros y
condiciones (que pasa y que no, con nombres concretos); deducciones de posicion (encontro X en
A4 -> escribe D4 y F4); los textos literales que escribe en un campo de resultado; diagrama
ASCII del flujo fuente -> destinos en el resumen.

Procedencia, en este orden: (1) valores reales de assets_externos o del tecnico, tal cual;
(2) valores que el bot construye en runtime (nombres con fecha, timestamps), ilustrados con
una fecha de ejemplo coherente en todo el documento, formula la primera vez; (3) contenido que
el bot nunca inspecciona (interior de archivos que solo mueve), SIEMPRE con nota al pie de que
es ilustrativo y que el layout real no esta en el export. Nunca mezclar niveles sin marcarlos:
un dato inventado sin marca es un error grave.

3) REGLAS DE FORMA
- No usar "—" (em dash) en ninguna parte. Usar dos puntos, punto seguido, parentesis o coma.
- Todo en bullets y tablas, salvo la narracion del resumen ejecutivo (1.1), en prosa corrida.
- Subtitulos numerados (2.1, 2.2, 3.1) para que cualquier hallazgo se pueda citar.
- Negritas solo en lo que cambia una decision. Si todo esta en negrita, nada lo esta.
- Nada de credenciales, nombres de locker, listas de global values ni rutas de log en el
  resumen ejecutivo: eso vive en el paso a paso y en la seccion 4.
- Bifurcaciones (si A -> X, si B -> Y) como tabla de decision, no parrafo.
- Prosa directa: sin "es importante notar que" ni recapitulaciones al cerrar.

4) ESTRUCTURA DEL DOCUMENTO
Encabezado: nombre y codigo del bot, subtitulo "Documento de negocio", y una linea de fuentes:
tecnico (con conteo de pasos y sub-bots), assets usados, y autoria/fechas si el codigo las
declara.

0. Vocabulario. Maximo 5 entradas. Entra un termino solo si cumple las tres: (a) aparece
varias veces y sostiene el entendimiento, (b) el lector no lo infiere del contexto, (c) es del
negocio del cliente o tecnologia especifica. NO entran nunca: conceptos del area (sub-bot,
global value, locker, credencial, log, API, script), terminos de una sola aparicion (se
explican inline) ni siglas de paso. Cada entrada: que es en 2-3 lineas + una frase que lo
aterrice a este bot.

1. Resumen ejecutivo. La seccion mas importante; se lee de corrido y deja el modelo mental.
Cinco bloques, en orden:
  1.1 Que hace, contado de corrido. Relato del dato, no del codigo: arranca por el disparador
      y la fuente, y avanza que busca, que obtiene, como se ve, donde lo deja, en que lo
      convierte, a donde lo manda. Ejemplos intercalados, cada uno pegado a la frase que
      ilustra. Si hay una decision malinterpretable, cortar el relato con una nota destacada
      de lo que el codigo realmente hace.
  1.2 El flujo en una vista (diagrama ASCII, fuente -> destinos finales).
  1.3 Ficha rapida (tabla de dos columnas): disparador, frecuencia, entradas, salidas, sistemas
      y su naturaleza, interaccion por pantalla (conteo real de pasos de UI; si es cero decirlo
      explicito, cambia toda la evaluacion de migracion), tamano (pasos AA, sub-bots).
  1.4 Volumen: que es contable y que no. Si el bot no acota el volumen, decirlo. Contar las
      veces que se invoca cada sub-bot.
  1.5 Dependencias criticas: bullets de lo que tiene que ser cierto para que el proceso corra,
      cada una redactada como condicion, no como componente.
2. El proceso paso a paso. Sub-bots en orden de ejecucion, uno por subtitulo, en lenguaje
natural y con valores reales inline. No citar numeros de paso ni direcciones: describir el
comportamiento. Aqui van los ejemplos de detalle (contenido de config, tablas de decision,
renombrados, codigos de respuesta). Si un comportamiento es tambien mala practica: describirlo
aca y remitir a la seccion 3, sin repetir el analisis.
3. Malas practicas, con numeros contados. Un subtitulo por hallazgo, con nombre descriptivo.
Cada hallazgo lleva bullets de que pasa y CIERRA SIEMPRE con un bullet "Consecuencia:" (que le
va a pasar a la operacion o a quien mantenga el bot). Un hallazgo sin consecuencia no se
escribe. Buscar al menos: secretos hardcodeados, config que existe y no se aplica, delays
fijos, esperas activas, rutas y endpoints hardcodeados, contadores con valores fijos, manejo
de error que traga el error o lo clasifica mal, reintentos que no reintentan, UI sobre app
web, sub-bots "genericos" con logica especifica adentro, archivos de escritorio como base de
datos compartida, pasos desactivados y codigo muerto, pasos sin efecto. Marcar explicito lo
que falla en silencio (no rompe, hace algo distinto): es lo mas caro de detectar.
4. Lo que no se ve desde el export, y donde buscarlo. Tabla de dos columnas (que falta / donde
esta). Incluye global values, credenciales, programacion, y sobre todo las preguntas de negocio
abiertas en el resumen (significado de un criterio, quien consume una salida, que sistema esta
del otro lado). Cada fila apunta a un lugar concreto.
5. Evaluacion de migracion a n8n + Python. El bot AA se apaga, todo se reconstruye. Sin la
dicotomia migrable/no migrable.
  5.1 Lo que se rehace en n8n nativo: tabla "Hoy en AA" / "Manana en n8n" (la izquierda
      describe el mecanismo actual, no el nombre del paso).
  5.2 Lo que conviene resolver en Python: datos, Excel/CSV, lo que hoy hace una macro,
      validaciones que hoy no existen.
  5.3 Lo que requiere reingenieria o investigacion aparte: UI sobre web (preferir la API
      detras), macros (recrear su logica), sistemas core, definiciones de negocio ausentes, y
      redisenos para varios bots a la vez.
  5.4 Porcentaje aproximado (tabla): reconstruible directo del tecnico vs lo que requiere
      investigacion externa antes de tocar codigo, con que entra en cada uno.

=========================================================================
PRINCIPIOS DEL CODIGO
=========================================================================
- Nunca inventar: el codigo hace eco del arbol, no infiere. La interpretacion la hace el
  agente, anclada al tecnico.
- Ninguna regla del pipeline nombra un comando o paquete de AA. La unica lista (los 9 pares
  omitidos en limpiar.py) dice QUE SE BORRA, no que se conserva: una clave nueva no esta en la
  lista, entonces se queda.
- Los controles corren en cada generacion y frenan el proceso. Ninguno tiene lista de claves.
  Si aparece una perdida nueva, arreglar el control antes que el sintoma.
- Nada asume el paso de sangria: se deriva del propio documento (un +1 hardcodeado rompio la
  reconstruccion en 7 de 9 bots sin que se notara hasta que el round-trip lo freno).
- Las tres reglas de UI (criterio no usado, casilla vacia con tipo, ficha repetida) ahorran
  ~15% del tecnico y dan 0% en bots sin pantallas. La verificacion re-expande las referencias:
  el control sigue siendo arbol contra arbol.
- Una regla de poda que se lleva TODO lo de una categoria se apaga sola para ese caso, en vez
  de dejar el hueco: la de criterios no usados no corre si el export no marco ninguno en uso
  (marca $sin-ancla). El control de suficiencia mide la perdida por poda, no la ausencia en el
  export: si la fuente no lo tiene, no hay nada que recuperar y frenar seria un falso positivo.
- Credenciales: del vault se muestra el puntero (locker/credencial/atributo), nunca el secreto.
  Un secreto hardcodeado en texto plano se muestra y se reporta como mala practica.
- La API key del Control Room solo en .env (gitignoreado). Los .png del Recorder se purgan al
  exportar (--keep-screenshots para conservarlos): el parser solo lee sin extension + manifest.
- proceso_tecnico y detalle son regenerables (no editar a mano) y van siempre en pareja.
  proceso_negocio y assets_externos/ se cuidan: no borrar sin revisar.

=========================================================================
FORMATO A360 (lo minimo; investigar en web lo que falte, citando fuente)
=========================================================================
- Bot en JSON, botCodeVersion 2, contentType application/vnd.aa.taskbot; el archivo del
  taskbot va sin extension.
- Bot = triggers, nodes, variables, packages, properties. Nodo = uid, commandName,
  packageName, attributes, returnTo, children, branches, disabled.
- Control: if/elseIf/else, loop.commands.start, try/catch/finally, step (agrupador). Sub-bots
  por runTask (taskbotPath); el export los resuelve recursivamente y el manifest los lista.
- $Var$ es variable; $@Var$ es global value (no esta en el export). CREDENTIAL trae
  name/lockerName/attributeName (puntero, sin secreto). manifest.globalValues suele venir vacio.
- El objeto de UI del recorder viene como JSON en base64 (clave "blob"); el pipeline lo
  decodifica.
- Un uiObject puede traer sus criterios TODOS en enabled false (visto en T046_LoginGTI del
  RP116: 23 de 23). El blob igual trae searchCriteria con indices; el control se ubica sin
  ancla declarada en criteria.

=========================================================================
REGISTRO DE CAMBIOS
=========================================================================
07/08/2026 [FIX] ficha de pantalla sin ningun criterio en uso
  ANTES: partir.py mandaba al detalle todo criterio sin enabled true. Si la ficha no tenia
    ninguno en true, se quedaba sin criteria y el control de suficiencia (b) frenaba la
    generacion con SIN ANCLA. El RP116 (T046_LoginGTI, ficha 54bfeaf98d81) no se podia
    generar; los 16 bots anteriores del parque no tenian el caso.
  AHORA: si ningun criterio esta en uso, la regla no se aplica y la ficha conserva los
    criterios completos, marcada con $sin-ancla true, y la cabecera del sub-bot lo declara
    con la lista de fichas. unir() ignora la marca, asi que el round-trip sigue exacto.
    El control (b) pasa a exigir lo que corresponde: que no se hayan ido TODOS al detalle
    teniendolos. Verificado en los 17 bots descargados, sin regresiones.
  Scripts de diagnostico: assets/sesion-07-08-2026-[FIX]-ficha-ui-sin-ancla/
