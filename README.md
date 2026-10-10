# Claudio

Gestor local de cuentas, cuotas y proyectos de Claude Code, basado en `claude-swap`. La aplicación de escritorio y el panel web comparten la misma interfaz.

## Abrir la aplicación

Desde el menú de aplicaciones, abre **Claudio**, o ejecuta:

```bash
claudio desktop
```

Para abrir el panel en el navegador:

```bash
claudio web
```

También puedes ejecutar `python3 desktop.py` o `python3 server.py` desde esta carpeta. El servidor escucha únicamente en `http://localhost:5252`. En Windows usa `claudio.bat` o `claudio.bat web`.

Requisitos: Python 3.10 o superior y `claude-swap`. La ventana nativa utiliza PySide6; si no está disponible, el lanzador abre el navegador. Para iniciar sesiones necesitas Claude Code y una terminal compatible (Konsole, GNOME Terminal, Ptyxis, Alacritty, Kitty, XFCE Terminal o xterm en Linux; Terminal en macOS; PowerShell en Windows).

El acceso de escritorio instalado debe apuntar a `desktop.py` en esta carpeta. Al actualizar estos archivos, no hace falta reinstalar la interfaz. En Linux, el lanzador detecta y reemplaza automáticamente el servidor de una versión anterior de esta misma instalación, conservando la configuración de rotación. Verifica el usuario, la ruta y el puerto antes de reemplazarlo; no cierra programas ajenos. En otros sistemas, si sigue abierto un servidor anterior, detenlo y vuelve a abrir Claudio. Cerrar la ventana no detiene el servidor, que puede estar compartido con el navegador.

Para instalar o actualizar el logo y el acceso del menú en Linux:

```bash
python3 install_linux.py
```

El logo está disponible en `assets/claudio.svg`, `assets/claudio.png` y `assets/claudio.ico`. Se usa en el panel, la pestaña del navegador y la ventana nativa; el instalador de Windows lo asigna al acceso directo.

## Interfaz y funciones

- Resumen de cuentas vinculadas, cuentas en rotación, consumo alto y proyectos.
- Cuotas de cinco horas y semanales con tiempos de reinicio. Las cuotas desconocidas se muestran como **Sin datos**.
- Búsqueda por correo o alias, filtros de cuentas pausadas o con consumo alto, y orden por nombre o cuota.
- Vinculación de la sesión actual o mediante token, edición de alias y confirmación al desvincular.
- Proyectos con búsqueda por nombre o ruta, favoritos, filtros y botón para mostrar más resultados.
- Reanudación de sesiones en la carpeta elegida y sesiones paralelas con selección de cuenta.
- Tema claro/oscuro y favoritos guardados localmente en cada navegador o perfil de escritorio.
- Actualización cada 30 segundos mientras el panel está visible y sin un diálogo abierto. Si falla, conserva los últimos datos y permite reintentar.
- Interfaz adaptable a pantallas pequeñas, navegación por teclado y diálogos que se cierran con Escape.
- CSS, JavaScript y fuentes del sistema: la interfaz no descarga recursos desde CDN. Consultar cuotas sigue dependiendo de `cswap` y sus servicios.

En la aplicación nativa, el menú **Ver** permite actualizar (`F5` / `Ctrl+R`), acercar (`Ctrl++`), alejar (`Ctrl+-`) y restaurar el zoom (`Ctrl+0`). El tamaño de la ventana y el zoom se conservan entre aperturas.

## Vincular y alternar cuentas

1. Inicia sesión en Claude Code.
2. Pulsa **Vincular cuenta → Sesión actual**, asigna un alias opcional y guarda.
3. Para otra cuenta, inicia sesión con `claude login` y repite el registro. También puedes usar la pestaña **Token / API key**.
4. Pulsa **Usar cuenta**, **Usar mejor cuota** o **Siguiente cuenta**.
5. Desde un proyecto, pulsa **Reanudar** para abrir `claude --resume` en su carpeta.

La opción **Sesión paralela** ejecuta `cswap run <cuenta> --share-history -- --resume` dentro de la carpeta del proyecto. Las carpetas que ya no existen quedan identificadas y sus acciones de terminal se deshabilitan. No se realizan cambios de cuenta al marcar favoritos ni al buscar.

Los tokens se envían a `cswap` por la entrada estándar y se limpian del formulario al cerrar el diálogo. Los errores al vincular, guardar o cambiar de cuenta se muestran sin anunciar un éxito falso.

## Rotación automática

Elige un umbral de **70 a 98 %** y una estrategia:

- **Mayor cuota disponible**: prioriza la cuenta con más margen.
- **Consumir la que vence antes**: prioriza el próximo vencimiento semanal.

Pulsa **Activar rotación**. Para cambiar su configuración, detén la rotación primero. El servicio lo ejecuta `cswap`; las configuraciones se mantienen durante la vida del servidor.

## Desarrollo y comprobaciones

```text
server.py                API local, validación y ejecución de cswap
index.html               Estructura de la interfaz y diálogos
styles.css               Estilos adaptables y temas
app.js                   Estado, filtros, acciones y persistencia local
desktop.py               Ventana Qt y arranque del servidor
claudio / claudio.bat     Lanzadores
start.sh                 Acceso rápido al lanzador
install_windows.ps1      Instalador para Windows
tests/                   Pruebas con datos ficticios
```

Pruebas de servidor y arranque de escritorio (no cambian cuentas reales):

```bash
python3 -m unittest discover -s tests -v
```

Prueba de navegador con Playwright y Chromium disponibles:

```bash
node tests/ui.cjs
```

Puedes indicar el módulo de Playwright con `PLAYWRIGHT_MODULE` y el ejecutable de Chromium con `CHROMIUM_PATH`. La prueba usa un puerto local temporal y cuentas ficticias.

Prueba nativa sin ventana visible, con PySide6 instalado:

```bash
python3 tests/desktop_smoke.py
```

Esta prueba usa una carpeta temporal para la configuración y el perfil Qt. No inicia `cswap` ni modifica las credenciales del usuario. Las pruebas gráficas requieren permiso para abrir puertos locales.
# claudio
