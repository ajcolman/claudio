# Script de instalación para Windows (ejecutar como Administrador)
# Instala las dependencias de Claudio y crea su acceso directo

Write-Host "=================================================="
Write-Host "  Instalador de Claudio para Windows"
Write-Host "=================================================="

# 1. Verificar Python
try {
    $pyver = python --version 2>&1
    Write-Host "✅ Python detectado: $pyver"
} catch {
    Write-Host "❌ Python no encontrado. Descárgalo en https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}

# 2. Instalar PySide6 para la GUI de escritorio
Write-Host "`n📦 Instalando PySide6 (interfaz gráfica nativa)..."
pip install "PySide6>=6.6"
if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ PySide6 instalado. La app de escritorio estará disponible."
} else {
    Write-Host "⚠️  PySide6 no pudo instalarse. Claudio abrirá en el navegador como alternativa."
}

# 3. Crear acceso directo en el Escritorio
$desktopPath = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktopPath "Claudio.lnk"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$targetPath = Join-Path $scriptDir "claudio.bat"

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($shortcutPath)
$Shortcut.TargetPath = $targetPath
$Shortcut.WorkingDirectory = $scriptDir
$Shortcut.IconLocation = (Join-Path $scriptDir "assets\claudio.ico")
$Shortcut.Description = "Claudio - Gestor de Cuentas Claude Code"
$Shortcut.Save()

Write-Host "`n✅ Acceso directo creado en el Escritorio: Claudio.lnk"

# 4. Agregar claudio.bat al PATH del usuario (opcional)
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -notlike "*$scriptDir*") {
    [Environment]::SetEnvironmentVariable("PATH", "$userPath;$scriptDir", "User")
    Write-Host "✅ Directorio de Claudio agregado al PATH del usuario."
    Write-Host "   Ahora puedes ejecutar 'claudio' desde cualquier terminal."
}

Write-Host "`n=================================================="
Write-Host "  ✅ Instalación completada."
Write-Host "     Haz doble clic en 'Claudio' en el Escritorio"
Write-Host "     o ejecuta 'claudio' en tu terminal."
Write-Host "=================================================="
