# The Department of Sandwich Legitimacy

A Windows desktop application that decides whether you are allowed to eat your
sandwich.

You fill in **Form 27-B/6**. Five departments convene. Each one evaluates your
lunch against compiled policy bytecode, files objections at a declared severity,
and adjusts your legitimacy score. The Directorate of Nomenclature can decline
jurisdiction entirely if your sandwich turns out to be a wrap. You receive a
numbered, sealed certificate, and the outcome is written into a hash-chained
Permanent Record that you cannot quietly edit afterwards.

```
--------------------------------------------------------------------------
                     * * *   A P P R O V E D   * * *
                                 APPROVED
--------------------------------------------------------------------------

  Composite legitimacy score   [######################################..]  96.16

  DEPARTMENTAL FINDINGS
    Directorate of Nomenclature           92.00  (w1.4) VETO
    Bureau of Structural Integrity       100.00  (w1.2) VETO
    Office of Condiment Affairs          100.00  (w1.0)
    Temporal Compliance Division         100.00  (w0.8)
    Committee on Culinary Ethics          91.00  (w1.1)
```

None of this needed to be built this way. That is the joke. Everything under
the joke is real and works.

---

## What is actually in here

| | |
|---|---|
| **An x86-64 assembler** | `dosl/forge/x64.py` encodes machine code by hand: REX prefixes, ModR/M bytes, labels, rel32 backpatching. |
| **A PE32+ linker** | `dosl/forge/pe.py` writes a complete, loadable Windows DLL from scratch — DOS stub, COFF header, 64-bit optional header, section table, export directory, base relocations. |
| **A real DLL** | `build/bureau_entropy.dll` is produced at runtime by the two modules above and loaded with `ctypes`. **There is no C compiler involved anywhere.** |
| **A programming language** | `.bureau` — lexer, Pratt parser, AST, compiler, 40-opcode stack VM, disassembler. |
| **An import hook** | `import dosl.policies.condiment` finds `condiment.bureau`, compiles it, and caches the bytecode in `__bureaucache__/`. |
| **A binary container** | `.sdwx` — fixed header, per-section CRC-32, whole-file SHA-256, zlib/LZMA per section, directory at the tail, trailer word. |
| **A struct DSL** | Declare a binary record with annotations; a metaclass compiles it to one `struct` format and validating descriptors. |
| **A hash-chained ledger** | Append-only JSONL where every entry carries the hash of the one above it. |
| **An icon** | `tools/make_icon.py` writes a six-size `.ico` with no imaging library. |
| **77 self-tests** | Including one that checks the hand-assembled machine code against a Python reference across a thousand random inputs. |

Zero third-party dependencies at runtime. PyInstaller is needed only to freeze
the `.exe`.

---

## Running it

### From source

```bash
python -m dosl
```

That opens the counter. Or use the command line:

```bash
python -m dosl submit --applicant "R. Milquetoast" --bread rye --fillings "pastrami,pickle" --condiments mustard --cut diagonal --toasted
```

### Building the executables

```powershell
.\build.ps1
```

Five stages — venv, icon, forge, test, exe — and it will not freeze anything if
the tests fail. Output:

| | |
|---|---|
| `dist\DoSL-Counter.exe` | windowed; double-click to open the counter |
| `dist\dosl.exe` | console; the full command line |

Single stages: `.\build.ps1 -Stage forge`. Start over: `.\build.ps1 -Clean`.

The DLL is **not** bundled into the `.exe`. The executable carries the
assembler and re-forges `bureau_entropy.dll` on first run.

---

## The command line

```
dosl submit      submit Form 27-B/6            --full --json --dossier PATH
dosl audit       disassemble a policy          dosl audit ethics --source
dosl record      read the Permanent Record     --verify --stats --tail N
dosl inspect     describe a .sdwx dossier      --extract certificate.txt
dosl forge       build and describe the DLL    --disassemble
dosl policies    departments, fields, intrinsics
dosl doctor      report on every moving part
dosl gui         open the counter
```

`dosl forge --disassemble` prints the machine code, if you want to check my
working:

```
  uint64_t bureau_splitmix64(uint64_t seed)
    00000000  48 89 c8 48 ba 15 7c 4a 7f b9 79 37 9e 48 01 d0  |H..H..|J..y7.H..|
    00000010  48 89 c2 48 c1 ea 1e 48 31 d0 48 ba b9 e5 e4 1c  |H..H...H1.H.....|
```

---

## The `.bureau` language

Each department's regulations are a real compiled program. Here is part of
`dosl/policies/condiment.bureau`:

```
POLICY condiment
VERSION 3
REGULATION "DoSL-7.4(a) -- On the Application of Spreadable Matter"

LET wet = condiment_count

REQUIRE wet > 0
    ELSE DENY "A dry sandwich is a cracker with ambitions." SEVERITY 7

WHEN "ketchup" IN condiments AND bread MATCHES "brioche" THEN
    PENALIZE 26 "Ketchup on brioche is a cry for help."
    NOTE "Referred to the Sub-Committee on Regrettable Pairings."
END

REQUIRE NOT has(condiments, "gravy")
    ELSE DENY "Gravy is not a condiment. Gravy is a weather system." SEVERITY 9

ASSESS 96 - 2 * max(0, wet - 2)
```

`dosl audit condiment` shows what that becomes:

```
 0004  02 00 00        LOAD 0                  ; condiments
 0007  50 00 00 01 00  CALL 0 1                ; count/1
 0012  03 01 00        STORE 1                 ; wet
 0022  42 21 00        JUMP_IF_TRUE 33         ; -> 0033
 0025  01 02 00        CONST 2                 ; 'A dry sandwich is a cracker...'
 0028  60 07 00 02 00  FINDING 7 2             ; severity 7, DENIAL
```

**Statements:** `POLICY` `VERSION` `REGULATION` `LET` `REQUIRE…ELSE`
`WHEN…THEN…OTHERWISE…END` `PENALIZE` `AWARD` `ASSESS` `NOTE` `CITE`

**Operators:** `+ - * / %`, `== != < <= > >=`, `IN` `CONTAINS` `MATCHES`,
`AND` `OR` `NOT` — `AND`/`OR` short-circuit, and `NOT` binds exactly like
Python's `not` (below comparisons, above `AND`), which it did not on the first
attempt.

**Intrinsics:** `count` `len` `has` `distinct` `words` `any` `all` `abs` `min`
`max` `sum` `clamp` `round` `lower` `upper` `text` `number` `entropy` `sign`
`verdict` — the last three call into the DLL.

Policies see the form's fields and nothing else. No builtins, no module
globals, no filesystem, no loops, and a step limit in case someone hand-writes
bytecode that has one.

Edit any `.bureau` file and rerun — the import hook notices the source digest
changed and recompiles. **Engine → Recompile all policies** does it without
restarting.

---

## About that DLL

There is no C compiler on the machine this was written on. Rather than add one,
`dosl/forge` builds the DLL itself.

`kernels.py` defines five functions twice — once as hand-encoded x86-64, once
as plain Python. `x64.py` encodes the instructions. `pe.py` links the result
into a PE32+ image with an export directory whose name table is sorted, because
`GetProcAddress` binary-searches it. `bridge.py` writes it to `build/`, loads it
with `ctypes.CDLL`, and checks the ABI version the DLL reports against the one
Python expects.

`tests/test_forge.py::test_native_matches_reference` runs both implementations
over a thousand random inputs and requires identical bits. That is how a wrong
ModR/M byte gets found without a debugger.

If anything fails — not Windows, not x86-64, DLL blocked by policy — the bridge
falls back to the Python kernels and `dosl doctor` says so. The application
does not notice.

```
$ dosl doctor
engine          native: bureau_entropy.dll @ ABI 0x00010003
dll             ...\build\bureau_entropy.dll
pe image        2560 bytes on disk, 0x4000 mapped, 5 exports
sections        .text@0x1000, .rdata@0x2000, .reloc@0x3000
record          14 entries, intact
```

---

## Layout

```
dosl/
  forge/       x64.py assembler, pe.py linker, kernels.py the code itself
  native/      bridge.py: build, load, verify, fall back
  kernel/      opcodes syntax compiler vm importer intrinsics
  formats/     struct_dsl.py metaclass, sdwx.py container
  bureau/      forms departments ledger certificate authority
  policies/    five .bureau files -- the actual regulations
  ui/gui.py    the counter
  cli.py       the command line
tools/         make_icon.py
tests/         77 tests, run_all.py
build.ps1      venv -> icon -> forge -> test -> exe
DoSL.spec      two executables from one analysis
```

Data lives in `%LOCALAPPDATA%\DoSL\permanent-record.jsonl`. Override with
`DOSL_HOME`, or `--ledger PATH`.

---

## Notes from the build

Things that were wrong and are recorded here so they stay fixed:

- `NOT` originally bound tighter than comparisons, so
  `NOT bread MATCHES "bowl"` meant `(NOT bread) MATCHES "bowl"` — a test that
  always passed. `NOT` now has its own binding power between `AND` and the
  comparisons.
- `ASSESS` used to overwrite the running score, so any `AWARD` above it was
  silently discarded. It now sets a baseline and adjustments accumulate
  separately.
- `DoSL.exe` and `dosl.exe` are the same file on NTFS. The second PyInstaller
  target was overwriting the first. Hence `DoSL-Counter.exe`.
- Ledger verification originally carried the *stored* hash forward, so editing
  one entry broke one link. It now carries the *recomputed* hash, so an edit
  invalidates everything below it.
- "Surprise the Department" could roll a one-slice 80mm sandwich, which the
  form's own cross-check rejects. It now redraws until the paperwork is valid.

---

## Licence

MIT. No sandwich was consulted during the drafting of these regulations.
