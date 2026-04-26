# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for RTESEditor

a = Analysis(
    ['../main.py'],
    pathex=['..'],
    binaries=[],
    datas=[
        ('../tes3/format/tes3_format.json', 'tes3/format'),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
)
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name='RTESEditor',
    console=False,
    icon=None,
)
