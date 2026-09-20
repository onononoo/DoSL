# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller specification for the Department of Sandwich Legitimacy.

Produces two single-file executables from one analysis:

    dist/DoSL-Counter.exe   windowed -- double-click opens the counter
    dist/dosl.exe           console  -- the full command line

The two names differ by more than case on purpose: NTFS is case-insensitive,
so ``DoSL.exe`` and ``dosl.exe`` are one file, and the second EXE target
silently overwrites the first.

The ``.bureau`` policy files are shipped as data rather than compiled in:
the import hook finds them on ``dosl.policies.__path__``, which inside a
frozen bundle points at ``sys._MEIPASS/dosl/policies``. Shipping the source
also means a curious user can read the regulations, which is more than the
real thing usually manages.

``bureau_entropy.dll`` is deliberately NOT bundled. The forge rebuilds it at
runtime from the machine code baked into ``dosl.forge.kernels``, so the exe
carries an assembler instead of a binary.

    pyinstaller --noconfirm DoSL.spec
"""

import os

block_cipher = None
PROJECT = os.path.abspath(os.getcwd())
ICON = os.path.join(PROJECT, "assets", "dosl.ico")

analysis = Analysis(
    ["dosl/__main__.py"],
    pathex=[PROJECT],
    binaries=[],
    datas=[
        ("dosl/policies/*.bureau", "dosl/policies"),
        ("README.md", "."),
    ],
    hiddenimports=[
        # Reached through importlib and the meta-path hook, so the static
        # analyser has no way to see them.
        "dosl.policies",
        "dosl.ui.gui",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # None of this is used, and all of it is enormous.
        "numpy", "pandas", "matplotlib", "PIL", "scipy", "setuptools",
        "pip", "pydoc_data", "test", "unittest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=block_cipher)

counter = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="DoSL-Counter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,           # the counter is a window, not a terminal
    disable_windowed_traceback=False,
    icon=ICON,
    version=None,
)

command_line = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="dosl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,            # `dosl submit`, `dosl audit`, `dosl forge`
    disable_windowed_traceback=False,
    icon=ICON,
    version=None,
)
