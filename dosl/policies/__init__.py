"""
The Department's policy library.

The ``.bureau`` files in this directory are the actual regulations. They are
not Python, but importing this package installs the meta-path hook that makes
them behave as though they were::

    from dosl.policies import condiment
    condiment.POLICY.listing()

:data:`SHIPPED` lists them in the order the departments are convened.
"""

from ..kernel.importer import discover, install

install()

#: Convened in this order. Nomenclature goes first because if your lunch is
#: not a sandwich, the remaining four departments have nothing to discuss.
SHIPPED: tuple[str, ...] = (
    "nomenclature",
    "structural",
    "condiment",
    "temporal",
    "ethics",
)

__all__ = ["SHIPPED", "discover", "install"]
