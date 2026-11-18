# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['launcher\\FFLauncher.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('launcher\\assets\\ff.ico', 'assets'),
        ('runtime\\ff.png', 'assets'),
        ('runtime\\ff_craft.png', 'assets'),
        ('VERSION', '.'),
        ('launcher\\ff_version.txt', '.'),
        ('tools\\data\\expected-ff100.json', 'tools\\data'),
        ('tools\\extract_nup.py', 'tools'),
    ],
    hiddenimports=['PIL._tkinter_finder'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='FFLauncher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['launcher\\assets\\ff.ico'],
)
