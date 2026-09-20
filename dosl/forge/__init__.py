"""The forge: where the Department manufactures its own native code.

:mod:`dosl.forge.x64`      encodes x86-64 instructions
:mod:`dosl.forge.pe`       links them into a PE32+ image
:mod:`dosl.forge.kernels`  is what actually gets encoded and linked
"""

from . import kernels, pe, x64

__all__ = ["kernels", "pe", "x64"]
