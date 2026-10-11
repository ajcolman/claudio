# Claudio

Gestor local de cuentas, cuotas y proyectos de Claude Code. La aplicación de escritorio y el panel web comparten la misma interfaz.

## Puesta en marcha

### 1. Requisitos

- **Python 3.10 o superior.** En macOS, el Python incluido con el sistema puede ser más antiguo; instala uno actual desde python.org o con Homebrew.
- **Claude Code** (`claude`) y una terminal compatible, para iniciar y reanudar sesiones: Konsole, GNOME Terminal, Ptyxis, Alacritty, Kitty, XFCE Terminal o xterm en Linux; Terminal en macOS; PowerShell en Windows.
- **PySide6** (opcional), para la ventana nativa. Si no está disponible, Claudio se abre en el navegador.

### 2. Descargar e instalar

En Linux y macOS:

```bash
git clone https://github.com/ajcolman/claudio.git
cd claudio
```

Claudio solo usa la biblioteca estándar de Python, así que no hay dependencias obligatorias. Para la ventana nativa instala PySide6:

```bash
python3 -m pip install "PySide6>=6.6"
```

Si `pip` rechaza la instalación global, como ocurre en Debian, Ubuntu o con el Python de Homebrew, instala PySide6 con el gestor de paquetes de tu sistema o en un entorno virtual, o prescinde de PySide6 y usa el navegador.

En Windows, el instalador se encarga de PySide6, crea el acceso directo **Claudio** en el Escritorio y agrega esta carpeta al `PATH`. Ejecútalo en PowerShell como Administrador:

```powershell
git clone https://github.com/ajcolman/claudio.git
cd claudio
powershell -ExecutionPolicy Bypass -File .\install_windows.ps1
```

### 3. Abrir Claudio

Desde la carpeta del proyecto, en Linux y macOS:

```bash
./claudio          # ventana nativa
./claudio web      # panel en el navegador
```

En Windows usa `claudio.bat` y `claudio.bat web`, o el acceso directo del Escritorio.

El panel queda disponible en `http://localhost:5252`; el servidor escucha únicamente en esa dirección local. Sin entorno gráfico, por ejemplo en una sesión SSH, `./claudio` inicia solo el servidor. También puedes ejecutar `python3 desktop.py` o `python3 server.py` directamente.

### 4. Comando `claudio` y acceso en el menú (opcional)

Para usar `claudio` desde cualquier carpeta en Linux y macOS, enlaza el lanzador, desde la carpeta del proyecto, en una carpeta que esté en tu `PATH`:

```bash
mkdir -p ~/.local/bin
ln -sf "$PWD/claudio" ~/.local/bin/claudio
```

Después puedes ejecutar `claudio`, `claudio desktop` o `claudio web`.

Para instalar o actualizar el logo y el acceso del menú en Linux:

```bash
python3 install_linux.py
```

En macOS, para crear o actualizar la aplicación `~/Applications/Claudio.app` (disponible en Launchpad y Spotlight):

```bash
python3 install_macos.py
```

Ejecútalo con el Python que tenga PySide6: la aplicación queda ligada a ese intérprete. Si lo cambias, vuelve a ejecutar el instalador.

El logo está disponible en `assets/claudio.svg`, `assets/claudio.png` y `assets/claudio.ico`. Se usa en el panel, la pestaña del navegador y la ventana nativa; el instalador de Windows lo asigna al acceso directo y el de macOS genera el icono de la aplicación a partir del PNG.

### Servidor y actualizaciones

El acceso de escritorio instalado debe apuntar a `desktop.py` en esta carpeta. Al actualizar estos archivos, no hace falta reinstalar la interfaz. En Linux, el lanzador detecta y reemplaza automáticamente el servidor de una versión anterior de esta misma instalación, conservando la configuración de rotación. Verifica el usuario, la ruta y el puerto antes de reemplazarlo; no cierra programas ajenos. En otros sistemas, si sigue abierto un servidor anterior, detenlo y vuelve a abrir Claudio. Cerrar la ventana no detiene el servidor, que puede estar compartido con el navegador.

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
- CSS, JavaScript y fuentes del sistema: la interfaz no descarga recursos desde CDN. Consultar cuotas depende de los servicios de Anthropic.

En la aplicación nativa, el menú **Ver** permite actualizar (`F5` / `Ctrl+R`), acercar (`Ctrl++`), alejar (`Ctrl+-`) y restaurar el zoom (`Ctrl+0`). El tamaño de la ventana y el zoom se conservan entre aperturas.

## Vincular y alternar cuentas

1. Inicia sesión en Claude Code.
2. Pulsa **Vincular cuenta → Sesión actual**, asigna un alias opcional y guarda.
3. Para otra cuenta, inicia sesión con `claude login` y repite el registro. También puedes usar la pestaña **Token / API key**.
4. Pulsa **Usar cuenta**, **Usar mejor cuota** o **Siguiente cuenta**.
5. Desde un proyecto, pulsa **Reanudar** para abrir `claude --resume` en su carpeta.

La opción **Sesión paralela** abre Claude Code con otra cuenta solo en esa terminal, dentro de la carpeta del proyecto. Usa un perfil aislado que comparte tu configuración, y en Linux y macOS también tu historial de conversaciones; las cuentas de API key no la admiten. Las carpetas que ya no existen quedan identificadas y sus acciones de terminal se deshabilitan. No se realizan cambios de cuenta al marcar favoritos ni al buscar.

Los tokens se guardan en el almacén privado de Claudio, nunca se devuelven a la interfaz y se limpian del formulario al cerrar el diálogo. Los errores al vincular, guardar o cambiar de cuenta se muestran sin anunciar un éxito falso.

## Dónde se guardan las cuentas

Claudio gestiona las cuentas por sí mismo, sin herramientas externas. Guarda el registro y una copia de la sesión de cada cuenta en una carpeta privada, legible solo por tu usuario:

| Sistema | Carpeta |
| :--- | :--- |
| Linux | `~/.local/share/claudio` |
| macOS | `~/Library/Application Support/Claudio/accounts` |
| Windows | `%APPDATA%\Claudio\accounts` |

Al cambiar de cuenta, Claudio reemplaza la sesión de Claude Code (un archivo en Linux y Windows, el llavero en macOS) y conserva los accesos de tus conectores MCP. La sesión de la cuenta activa la renueva Claude Code; Claudio solo renueva las de las cuentas inactivas, y guarda la copia más reciente de la activa antes de cambiar.

Si ya usabas `claude-swap`, la primera vez Claudio importa sus cuentas sin modificar los datos originales. A partir de ahí conviene usar solo Claudio: cada renovación de sesión invalida la copia anterior, así que la que conserva `claude-swap` deja de servir y volver a usarlo exigiría iniciar sesión de nuevo en esas cuentas.

Si una cuenta muestra que necesita iniciar sesión de nuevo, entra con ella en Claude Code (`claude` y luego `/login`) y pulsa **Vincular cuenta → Sesión actual**.

## Rotación automática

Elige un umbral de **70 a 98 %** y una estrategia:

- **Mayor cuota disponible**: prioriza la cuenta con más margen.
- **Consumir la que vence antes**: prioriza el próximo vencimiento semanal.

Pulsa **Activar rotación**. Para cambiar su configuración, detén la rotación primero. La rotación corre dentro del servidor de Claudio: revisa las cuotas cada minuto y, cuando la cuenta activa alcanza el umbral, cambia a una cuenta que esté al menos 5 puntos por debajo. No cambia más de una vez cada 5 minutos, salvo que la cuenta activa esté agotada, y nunca elige cuentas en pausa. La configuración se mantiene durante la vida del servidor.

## Desarrollo y comprobaciones

```text
server.py                API local y validación
accounts/                Cuentas, credenciales, cuotas, rotación y sesiones paralelas
index.html               Estructura de la interfaz y diálogos
styles.css               Estilos adaptables y temas
app.js                   Estado, filtros, acciones y persistencia local
desktop.py               Ventana Qt y arranque del servidor
claudio / claudio.bat     Lanzadores
start.sh                 Acceso rápido al lanzador
install_linux.py         Acceso e icono del menú en Linux
install_macos.py         Aplicación Claudio.app para macOS
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

Esta prueba usa una carpeta temporal para la configuración y el perfil Qt. No modifica las cuentas ni las credenciales del usuario. Las pruebas gráficas requieren permiso para abrir puertos locales.

## Créditos

El manejo de cuentas se escribió para Claudio tomando como referencia [claude-swap](https://github.com/realiti4/claude-swap) de Onur Cetinkol, publicado bajo licencia MIT. Su aviso de licencia está en `NOTICE.md`.
