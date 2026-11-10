from pathlib import Path

root = Path(SPECPATH).resolve().parent
bundle = root / "build/installer-bundle"
resources = root / "nodera/resources"
common = [
    (str(resources / "nodera.ico"), "nodera/resources"),
    (str(resources / "nodera.svg"), "nodera/resources"),
    (str(resources / "pro-trusted-keys.json"), "nodera/resources"),
    (str(bundle / "build-info.json"), "installer"),
    (str(bundle / "manifest.json"), "installer"),
    (str(bundle / "LICENSE"), "installer"),
    (str(bundle / "license-ru.txt"), "installer"),
]
a = Analysis([str(root / "packaging/installer_main.py")], pathex=[str(root)],
             binaries=[], datas=common, hiddenimports=[], hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=[], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
setup = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    [("engine/Nodera-Engine.exe", str(bundle / "Nodera-Engine.exe"), "DATA")],
    name="Nodera-Setup", console=False, uac_admin=True, upx=False,
    icon=str(resources / "nodera.ico"), version=str(bundle / "version-info.txt"),
)
