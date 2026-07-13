# Plugins de Lilith IDE

Lilith IDE soporta plugins escritos en Python que se cargan al arrancar la
aplicación. Esta página documenta el contrato estable de la API de plugins
(`lilith_cli.ide.plugins`, versión de contrato `PLUGIN_API_VERSION = "1.0"`),
el mecanismo de descubrimiento y un tutorial paso a paso.

## Descubrimiento y carga

- **Dónde viven:** archivos `*.py` en **`.yggdrasil/plugins/`** dentro de la
  raíz del proyecto que abriste con el IDE (no es un directorio global: cada
  proyecto tiene sus propios plugins).
- **Qué se ignora:** archivos que empiezan con `_` (por ejemplo
  `__init__.py` o `_helpers.py`), así podés tener módulos auxiliares privados
  al lado de tus plugins.
- **Orden:** los plugins se cargan ordenados alfabéticamente por nombre de
  archivo.
- **Cuándo:** en `LilithIDEApp.on_mount` la app ejecuta
  `plugin_manager.load_all()` y luego `plugin_manager.register_all(app)`.
  Si algún plugin cargó, el chat muestra `Plugins cargados: …`.
- **Inspección:** el comando `/plugins` en el chat lista los plugins cargados
  y la ruta del directorio de plugins.

No hay soporte de *entry points* de paquetes instalados: el único mecanismo
es el directorio `.yggdrasil/plugins/`.

## El contrato

Un plugin es un módulo Python que declara su punto de entrada de **una** de
estas tres formas. El manager las resuelve en este orden (gana la primera):

1. **Objeto `plugin`** a nivel de módulo con un método `on_load(app)`
   invocable (puede ser subclase de `LilithPlugin` o duck-typing).
2. **Clase `Plugin`** a nivel de módulo (se instancia sin argumentos; debe
   tener `on_load(app)`).
3. **Función `register(app)`** a nivel de módulo — la forma más simple.

### Hooks de ciclo de vida

| Hook | Firma | Estado |
| --- | --- | --- |
| `on_load` / `register` | `on_load(app)` / `register(app)` | **Activo.** La app lo invoca una vez al montar el IDE. |
| `on_file_open` | `on_file_open(app, path)` | *Reservado.* Definido en la API; la app todavía no emite el evento. |
| `on_file_save` | `on_file_save(app, path)` | *Reservado.* Ídem. |
| `on_unload` | `on_unload(app)` | *Reservado.* Ídem. |

Los hooks reservados se despachan con `PluginManager.emit(evento, app, ...)`
(que busca `on_<evento>` en la instancia del plugin o como función a nivel de
módulo). Implementarlos hoy es inocuo y *forward-compatible*: empezarán a
dispararse cuando la app agregue las llamadas a `emit()` correspondientes.

### Metadatos (opcionales, estilo clase)

```python
class Plugin(LilithPlugin):
    name = "mi-plugin"        # nombre para /plugins (default: nombre del archivo)
    version = "1.0.0"          # informativo
    description = "Qué hace."  # informativo
```

### Qué recibe el plugin: el objeto `app`

`app` es la aplicación Textual en ejecución (`LilithIDEApp`). Superficie
estable y útil para plugins:

| Miembro | Descripción |
| --- | --- |
| `app.root` | Raíz del proyecto (`pathlib.Path` resuelto). |
| `app._chat_system(texto)` | Escribe una línea de sistema en el chat (acepta markup de Rich). |
| `app.notify(msg, title=..., severity=...)` | Muestra un toast (`severity`: `information`, `warning`, `error`). |
| `app.current_file` | Archivo enfocado en el editor (`Path \| None`). |
| `app.plugin_manager` | El `PluginManager` (podés inspeccionar `errors`, `list()`, `get(nombre)`). |

Todo lo demás de la app (widgets internos, mixins) **no** es contrato
estable y puede cambiar sin aviso.

### Manejo de errores: un plugin roto no tira el IDE

Todas las fases están aisladas por plugin:

- **Import roto** (syntax error, excepción al importar) → se loguea y se
  saltea; los demás plugins cargan igual.
- **Sin punto de entrada** (ni `plugin`, ni `Plugin`, ni `register`) → se
  registra el motivo y se saltea.
- **`on_load`/`register` que lanza** → se loguea, se registra y se sigue con
  el próximo plugin.
- **Hook de `emit()` que lanza** → ídem; el evento llega igual al resto.

Los fallos quedan disponibles en `app.plugin_manager.errors` (lista de
`PluginError(plugin, phase, message)`) y se loguean con el logger
`lilith.plugins` en nivel `WARNING`.

## Tutorial paso a paso

### 1. Creá el directorio de plugins

Desde la raíz de tu proyecto:

```bash
mkdir -p .yggdrasil/plugins
```

### 2. Escribí tu primer plugin (forma función)

`.yggdrasil/plugins/hello.py`:

```python
def register(app):
    app._chat_system("[bold]hello[/] cargado — proyecto: " + app.root.name)
```

### 3. Probalo

Abrí el IDE en el proyecto. En el chat deberías ver
`Plugins cargados: hello`. Ejecutá `/plugins` para listar lo cargado.

### 4. Pasá a la forma clase cuando necesites estado o metadatos

`.yggdrasil/plugins/contador.py`:

```python
from lilith_cli.ide.plugins import LilithPlugin

class Plugin(LilithPlugin):
    name = "contador"
    version = "1.0.0"
    description = "Cuenta archivos Python del proyecto."

    def on_load(self, app):
        total = sum(1 for _ in app.root.rglob("*.py"))
        app.notify(f"{total} archivos .py", title=self.name)
```

Alternativa equivalente con instancia explícita:

```python
plugin = Plugin()   # el manager la prefiere por sobre la clase
```

### 5. Prepará hooks futuros (opcional)

```python
class Plugin(LilithPlugin):
    def on_load(self, app): ...
    def on_file_save(self, app, path):
        app.notify(f"Guardaste {path.name}")  # se activará cuando la app emita el evento
```

### 6. Depurar un plugin que no carga

1. Corré `/plugins` — si no aparece, no cargó.
2. Revisá `app.plugin_manager.errors` (o el log del logger
   `lilith.plugins`): ahí está la fase (`load`, `register`, `hook:<evento>`)
   y el mensaje de la excepción.
3. Verificá que el archivo no empiece con `_` y que declare un punto de
   entrada válido.

## Ejemplos incluidos

En `examples/plugins/` del repo hay dos plugins funcionales listos para
copiar a `.yggdrasil/plugins/`:

- **`skald_greeting.py`** (trivial, forma función): saluda al arrancar con
  estadísticas básicas de la raíz del proyecto.
- **`todo_runes.py`** (útil, forma clase): recorre el proyecto, cuenta
  marcadores `TODO`/`FIXME`/`HACK`/`XXX` y publica en el chat el total y los
  5 archivos más cargados. También implementa el hook reservado
  `on_file_save`.

```bash
cp examples/plugins/skald_greeting.py examples/plugins/todo_runes.py .yggdrasil/plugins/
```

## Referencia rápida de la API

```python
from lilith_cli.ide.plugins import (
    PLUGIN_API_VERSION,  # "1.0"
    LilithPlugin,        # clase base / protocolo
    LoadedPlugin,        # metadata de un plugin cargado
    PluginError,         # fallo registrado (plugin, phase, message)
    PluginManager,       # descubrimiento, carga y dispatch
)

mgr = PluginManager(root)      # root: Path del proyecto
mgr.plugin_dir()               # root/.yggdrasil/plugins
mgr.discover()                 # list[Path] candidatos
mgr.load_all()                 # importa y resuelve entry points
mgr.register_all(app)          # fase on_load; devuelve nombres OK
mgr.emit("file_save", app, p)  # despacha on_file_save a quien lo tenga
mgr.list()                     # list[LoadedPlugin]
mgr.get("todo_runes")          # LoadedPlugin | None
mgr.errors                     # list[PluginError]
```
