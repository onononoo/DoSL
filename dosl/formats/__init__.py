"""Binary file formats.

:mod:`dosl.formats.struct_dsl`  declarative fixed-layout records
:mod:`dosl.formats.sdwx`        the ``.sdwx`` dossier container
"""

from .sdwx import Dossier, Flags, Section, SdwxError, marshal_policy, unmarshal_policy
from .struct_dsl import BinaryStruct, StructError

__all__ = [
    "BinaryStruct", "Dossier", "Flags", "Section", "SdwxError", "StructError",
    "marshal_policy", "unmarshal_policy",
]
