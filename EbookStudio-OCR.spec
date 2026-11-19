# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH)
datas = [(str(root / "web" / name), "web") for name in ("index.html", "editor.css", "editor.js")]
datas.extend((str(root / name), ".") for name in ("paddle_worker.py", "agent.md"))
datas += copy_metadata("trafilatura")
datas += collect_data_files("trafilatura", includes=["settings.cfg"])
binaries = []
# fontTools chooses table parsers dynamically when it opens an embedded font.
hidden = collect_submodules("fontTools.ttLib.tables")
for package in ("pypdfium2", "pypdfium2_raw"):
    package_data, package_binaries, package_imports = collect_all(package)
    datas.extend(package_data)
    binaries.extend(package_binaries)
    hidden.extend(package_imports)

analysis = Analysis(
    [str(root / "studio.py"), str(root / "studio_agent.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["numpy", "matplotlib"],
)
# The editor uses QWidget and Qt WebEngine. PyInstaller's QtQml hook also
# collects QML applications and modules with separate licensing requirements.
editor_qt_libraries = {
    "Qt6Core.dll", "Qt6Gui.dll", "Qt6Network.dll", "Qt6OpenGL.dll",
    "Qt6Pdf.dll", "Qt6Positioning.dll", "Qt6PrintSupport.dll", "Qt6Qml.dll",
    "Qt6QmlMeta.dll", "Qt6QmlModels.dll", "Qt6QmlWorkerScript.dll", "Qt6Quick.dll",
    "Qt6QuickWidgets.dll", "Qt6SerialPort.dll", "Qt6Svg.dll", "Qt6WebChannel.dll",
    "Qt6WebEngineCore.dll", "Qt6WebEngineWidgets.dll", "Qt6Widgets.dll",
}


def bundled(entry):
    destination, source, _ = entry
    parts = Path(destination).parts
    name = parts[-1]
    if parts[:2] == ("PySide6", "qml") or parts[:3] in {
        ("PySide6", "plugins", "qmltooling"),
        ("PySide6", "plugins", "platforminputcontexts"),
    }:
        return False
    if name.startswith("Qt6") and name.endswith(".dll"):
        return name in editor_qt_libraries
    # Use Windows system libraries and the separately installed VC++ runtime.
    system_name = name.lower()
    if system_name == "ucrtbase.dll" or system_name.startswith("api-ms-win-"):
        return False
    if system_name.endswith(".dll") and system_name.startswith(("msvcp140", "vcruntime140")):
        return False
    return not (system_name.startswith("icu") and "poppler" in source.lower())


analysis.binaries = [entry for entry in analysis.binaries if bundled(entry)]
analysis.datas = [entry for entry in analysis.datas if bundled(entry)]
pyz = PYZ(analysis.pure)
gui_scripts = [entry for entry in analysis.scripts if entry[0] != "studio_agent"]
agent_scripts = [entry for entry in analysis.scripts if entry[0] != "studio"]
exe = EXE(pyz, gui_scripts, [], exclude_binaries=True, name="EbookStudio-OCR", console=False, upx=False)
agent = EXE(pyz, agent_scripts, [], exclude_binaries=True, name="EbookStudio-Agent", console=True, upx=False)
coll = COLLECT(exe, agent, analysis.binaries, analysis.datas, name="EbookStudio-OCR", upx=False)
