"""
dosl.forge.kernels -- the machine-code bodies exported by bureau_entropy.dll.

Each kernel appears twice: once as hand-encoded x86-64, and once as a plain
Python reference implementation. The two are required to agree bit-for-bit,
which is how we find out that a ModR/M byte was wrong without owning a
debugger.
"""

from __future__ import annotations

from .pe import DllImage, Export
from .x64 import RAX, RCX, RDX, R8, Assembler, Cond

U32 = 0xFFFF_FFFF
U64 = 0xFFFF_FFFF_FFFF_FFFF

#: Bumped whenever a kernel's observable behaviour changes. The Python side
#: refuses to use a DLL whose ABI it does not recognise.
ABI_VERSION = 0x0001_0003

GOLDEN_GAMMA = 0x9E37_79B9_7F4A_7C15
SPLITMIX_A = 0xBF58_476D_1CE4_E5B9
SPLITMIX_B = 0x94D0_49BB_1331_11EB
MURMUR_A = 0xFF51_AFD7_ED55_8CCD
MURMUR_B = 0xC4CE_B9FE_1A85_EC53
FNV_OFFSET = 0x811C_9DC5
FNV_PRIME = 0x0100_0193
VERDICT_SCRAMBLE = 0x2545_F491


# --------------------------------------------------------------------------
# DllMain
# --------------------------------------------------------------------------

def dllmain() -> bytes:
    """``BOOL DllMain(HINSTANCE, DWORD, LPVOID)`` -- unconditionally succeed.

    Returning zero here makes ``LoadLibrary`` fail with a spectacularly
    unhelpful error, so we do not do that.
    """
    a = Assembler()
    a.mov_imm32(RAX, 1)
    a.ret()
    return a.assemble()


# --------------------------------------------------------------------------
# uint64_t bureau_splitmix64(uint64_t seed)
# --------------------------------------------------------------------------

def splitmix64_code() -> bytes:
    a = Assembler()
    a.mov(RAX, RCX)                 # rax = seed
    a.movabs(RDX, GOLDEN_GAMMA)
    a.add(RAX, RDX)                 # rax += 0x9E3779B97F4A7C15
    a.mov(RDX, RAX)
    a.shr(RDX, 30)
    a.xor(RAX, RDX)                 # rax ^= rax >> 30
    a.movabs(RDX, SPLITMIX_A)
    a.imul(RAX, RDX)
    a.mov(RDX, RAX)
    a.shr(RDX, 27)
    a.xor(RAX, RDX)                 # rax ^= rax >> 27
    a.movabs(RDX, SPLITMIX_B)
    a.imul(RAX, RDX)
    a.mov(RDX, RAX)
    a.shr(RDX, 31)
    a.xor(RAX, RDX)                 # rax ^= rax >> 31
    a.ret()
    return a.assemble()


def splitmix64_ref(seed: int) -> int:
    z = (seed + GOLDEN_GAMMA) & U64
    z = ((z ^ (z >> 30)) * SPLITMIX_A) & U64
    z = ((z ^ (z >> 27)) * SPLITMIX_B) & U64
    return z ^ (z >> 31)


# --------------------------------------------------------------------------
# uint32_t bureau_fnv1a(const uint8_t *data, size_t length)
# --------------------------------------------------------------------------

def fnv1a_code() -> bytes:
    a = Assembler()
    a.mov_imm32(RAX, FNV_OFFSET)    # hash = 2166136261
    a.test(RDX, RDX)                # length == 0 ?
    a.jcc(Cond.E, "done")
    a.label("loop")
    a.movzx_byte(R8, RCX)           # r8d = *data
    a.xor(RAX, R8, w=False)         # hash ^= byte
    a.imul_imm32(RAX, RAX, FNV_PRIME)
    a.inc(RCX)
    a.dec(RDX)
    a.jcc(Cond.NE, "loop")
    a.label("done")
    a.ret()
    return a.assemble()


def fnv1a_ref(data: bytes) -> int:
    h = FNV_OFFSET
    for byte in data:
        h = ((h ^ byte) * FNV_PRIME) & U32
    return h


# --------------------------------------------------------------------------
# uint32_t bureau_verdict(uint64_t entropy, uint32_t clearance)
# --------------------------------------------------------------------------

def verdict_code() -> bytes:
    a = Assembler()
    a.mov(RAX, RCX)
    a.mov(R8, RAX)
    a.shr(R8, 32)
    a.xor(RAX, R8, w=False)         # fold the high half into the low half
    a.add(RAX, RDX, w=False)        # apply clearance
    a.imul_imm32(RAX, RAX, VERDICT_SCRAMBLE)
    a.shr(RAX, 24, w=False)         # keep the top byte: 0..255
    a.ret()
    return a.assemble()


def verdict_ref(entropy: int, clearance: int) -> int:
    folded = (entropy ^ (entropy >> 32)) & U32
    scrambled = ((folded + clearance) & U32) * VERDICT_SCRAMBLE & U32
    return scrambled >> 24


# --------------------------------------------------------------------------
# uint64_t bureau_mix2(uint64_t a, uint64_t b)
# --------------------------------------------------------------------------

def mix2_code() -> bytes:
    a = Assembler()
    a.mov(RAX, RCX)
    a.xor(RAX, RDX)
    a.movabs(RDX, MURMUR_A)
    a.imul(RAX, RDX)
    a.mov(RDX, RAX)
    a.shr(RDX, 33)
    a.xor(RAX, RDX)
    a.movabs(RDX, MURMUR_B)
    a.imul(RAX, RDX)
    a.mov(RDX, RAX)
    a.shr(RDX, 33)
    a.xor(RAX, RDX)
    a.ret()
    return a.assemble()


def mix2_ref(x: int, y: int) -> int:
    h = (x ^ y) & U64
    h = (h * MURMUR_A) & U64
    h ^= h >> 33
    h = (h * MURMUR_B) & U64
    h ^= h >> 33
    return h


# --------------------------------------------------------------------------
# uint32_t bureau_abi_version(void)
# --------------------------------------------------------------------------

def abi_version_code() -> bytes:
    a = Assembler()
    a.mov_imm32(RAX, ABI_VERSION)
    a.ret()
    return a.assemble()


# --------------------------------------------------------------------------
# Assembly of the image itself
# --------------------------------------------------------------------------

KERNELS: tuple[tuple[str, object, str, str], ...] = (
    ("bureau_abi_version", abi_version_code,
     "uint32_t bureau_abi_version(void)",
     "Reports the kernel ABI the Python side must agree with."),
    ("bureau_splitmix64", splitmix64_code,
     "uint64_t bureau_splitmix64(uint64_t seed)",
     "SplitMix64. Turns a boring seed into a convincing one."),
    ("bureau_fnv1a", fnv1a_code,
     "uint32_t bureau_fnv1a(const uint8_t *data, size_t length)",
     "FNV-1a over a byte range; the only kernel with a branch in it."),
    ("bureau_verdict", verdict_code,
     "uint32_t bureau_verdict(uint64_t entropy, uint32_t clearance)",
     "Folds entropy against a clearance level into a 0..255 score."),
    ("bureau_mix2", mix2_code,
     "uint64_t bureau_mix2(uint64_t a, uint64_t b)",
     "Combines two hashes using the MurmurHash3 64-bit finaliser."),
)


def build_dll(name: str = "bureau_entropy.dll") -> bytes:
    """Assemble every kernel and link them into a complete PE32+ DLL."""
    image = DllImage(name)
    image.set_entry(dllmain())
    for symbol, emit, signature, doc in KERNELS:
        image.add_export(Export(name=symbol, code=emit(), signature=signature, doc=doc))
    return image.build()
