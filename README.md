# AA Bot Parser

Audita bots de Automation Anywhere (export A360) sin abrir AA. Le das el `fileId` de un bot,
corre el proceso determinista (técnico + detalle) y recién ahí preguntás qué necesitás:
potencial de migración, malas prácticas, un bug (con su log) o el documento de negocio
completo. Genérico: sirve para cualquier export; ningún nombre de bot está hardcodeado.

## Puesta en marcha

1. Conectarse a la VPN de Interseguro. El Control Room solo resuelve dentro de la red interna;
   sin VPN la descarga falla con `NameResolutionError`. Lo único que funciona sin VPN es
   `--carpeta` sobre un bot ya descargado.
2. Instalar dependencias: `pip install -r requirements.txt` (solo `requests`, para descargar).
3. Credenciales: copiar `.env.example` a `.env` y completar `AA_CR_BASE`, `AA_CR_USER`,
   `AA_CR_KEY`, `AA_CR_WORKSPACE`. El `.env` está gitignoreado: nunca se sube.

## Uso

```
python exporter/main.py <fileId>                    # descarga del Control Room y genera
python exporter/main.py --carpeta "Bots/<bot>"      # bot ya descargado, offline
python exporter/export_bot.py --search "nombre"     # encontrar el fileId por nombre
```

Opciones de `main.py`:

| opción | efecto |
|---|---|
| `--force` | vuelve a descargar aunque el bot ya esté en `Bots/` |
| `--with-packages` | incluye los `.jar` (por defecto NO: no aportan a la auditoría y pesan GB) |
| `--keep-screenshots` | conserva los `.png` del Recorder (por defecto se purgan) |
| `--name <nombre>` | verifica que el fileId sea ese bot antes de exportar |

## Salidas

Por cada bot, en `Bots/<bot>/`:

| archivo | qué es |
|---|---|
| `proceso_tecnico_<CODIGO>.md` | el árbol del bot en texto legible. Es el que se lee |
| `detalle_<CODIGO>.md` | el respaldo: coordenadas de los clics, HTML, criterios sin usar y el cuerpo de pasos apagados. Se abre para buscar una cosa |
| `proceso_negocio_<CODIGO>.md` | el documento de negocio, redactado por el agente SOLO si se lo pedís (instrucciones y rúbrica en `CLAUDE.md`) |

`main.py` también imprime por consola el inventario de lo externo (global values, credenciales,
archivos, disparador) y el mapa de líneas de cada sub-bot dentro del técnico.

Para redactar el `proceso_negocio` con valores reales: poner los archivos que el bot usa en
`Bots/<bot>/assets_externos/` y pedirle al agente que los lea. No hace falta para empezar: el
técnico ya muestra qué busca el bot dentro de cada archivo.

## Flujo de trabajo con el agente

1. Se le pasa un `fileId` (o un nombre, si el bot ya está en `Bots/`). El agente corre
   `main.py`, que genera el técnico y el detalle y verifica el contrato.
2. Con eso listo, muestra la ficha (pasos, sub-bots, pasos de UI, global values, credenciales,
   archivos) y pregunta qué necesitás, con estas opciones por default:
   - resumen de qué hace el bot
   - potencial de migración a n8n + Python
   - malas prácticas identificadas
   - un documento con todo lo anterior (el `proceso_negocio`)
   - resolver un bug en específico (le pasás el log y cruza sus líneas con los pasos `$n`)
3. Responde en el chat, leyendo del técnico por bloques y consultando el detalle solo con
   greps dirigidos (`$off`, `$id`). El `proceso_negocio` solo se redacta si elegís la opción
   de documentarlo.

## Cómo funciona (resumen)

1. **Descargar**: exporta el bot y sus sub-bots del Control Room por API. Sin los `.jar`
   (pesan y no dicen nada) ni los screenshots del Recorder (se purgan al descomprimir).
2. **Consolidar**: junta los taskbots en orden de llamada y decodifica los objetos de pantalla
   (blobs base64).
3. **Limpiar**: quita solo lo que no aporta, con reglas que no conocen ningún comando de AA:
   campos vacíos, uid sin referenciar, los 9 pares que significan "aquí no aplica" y escribe
   con sangría en vez de llaves. `false` y `0` nunca se tocan (son datos).
4. **Listar lo que falta**: global values, credenciales y archivos que toca el bot, con
   conteos.
5. **Partir**: lo que sirve para leer queda en el técnico; el resto (cuerpos de pasos apagados,
   criterios de búsqueda sin usar, casillas constantes) va al detalle, direccionado con `$off`
   y `$id`.

## El contrato

```
proceso_tecnico_<COD>.md  +  detalle_<COD>.md  ==  el export
```

La herramienta se verifica a sí misma en cada corrida con cuatro controles (ninguno con lista
de claves): igualdad del consolidado contra el export, árbol contra árbol en las dos
direcciones, árbol contra texto, y suficiencia (que lo que quedó en el técnico alcance para
auditar). Si algo no cuadra, **no genera ninguno de los dos archivos**: un documento incompleto
que parece completo es peor que no tener documento.

## Resultado

Sobre los 16 bots del parque, el técnico pasó de 14,58 M a 6,12 M de caracteres (-58%). Un clic
sobre una pantalla pasó de ~300 a ~24 líneas, conservando tipo de control, tecnología,
navegador y los criterios con que el bot lo ubica (de ahí sale si el paso es frágil). Los bots
sin pantallas bajan menos (-28% a -33%).
