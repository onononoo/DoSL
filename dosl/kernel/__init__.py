"""The Regulatory Evaluation Engine: parse, compile and execute policy.

Pipeline::

    .bureau text -> syntax.parse -> compiler.Compiler -> PolicyCode -> vm.Machine

:mod:`dosl.kernel.importer` bolts the first three onto Python's import system,
so a policy file can simply be imported.
"""

from .compiler import PolicyCode, PolicyCompileError, compile_source
from .opcodes import Kind, Op
from .syntax import PolicySyntaxError, parse
from .vm import Adjustment, Finding, Machine, Outcome, PolicyRuntimeError

__all__ = [
    "Adjustment", "Finding", "Kind", "Machine", "Op", "Outcome",
    "PolicyCode", "PolicyCompileError", "PolicyRuntimeError",
    "PolicySyntaxError", "compile_source", "parse",
]
