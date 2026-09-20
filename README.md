# the department of sandwich legitimacy

a windows desktop application that decides whether you are allowed to eat your
sandwich.

you fill in **form 27-b/6**. five departments convene. each one evaluates your
lunch against compiled policy bytecode, files objections at a declared severity,
and adjusts your legitimacy score. the directorate of nomenclature can decline
jurisdiction entirely if your sandwich turns out to be a wrap. you receive a
numbered, sealed certificate, and the outcome is written into a hash-chained
permanent record that you cannot quietly edit afterwards.

```
--------------------------------------------------------------------------
                     * * *   a p p r o v e d   * * *
                                 approved
--------------------------------------------------------------------------

  composite legitimacy score   [###############################.]  96.16

  departmental findings
    directorate of nomenclature           92.00  (w1.4) veto
    bureau of structural integrity       100.00  (w1.2) veto
    office of condiment affairs          100.00  (w1.0)
    temporal compliance division         100.00  (w0.8)
    committee on culinary ethics          91.00  (w1.1)
```

none of this needed to be built this way. that is the joke. everything under
the joke is real and works.

---

## what is actually in here

| | |
|---|---|
| **an x86-64 assembler** | `dosl/forge/x64.py` encodes machine code by hand: rex prefixes, modr/m bytes, labels, rel32 backpatching. |
| **a pe32+ linker** | `dosl/forge/pe.py` writes a complete, loadable windows dll from scratch — dos stub, coff header, 64-bit optional header, section table, export directory, base relocations. |
| **a real dll** | `build/bureau_entropy.dll` is produced at runtime by the two modules above and loaded with `ctypes`. **there is no c compiler involved anywhere.** |
| **a programming language** | `.bureau` — lexer, pratt parser, ast, compiler, 40-opcode stack vm, disassembler. |
| **an import hook** | `import dosl.policies.condiment` finds `condiment.bureau`, compiles it, and caches the bytecode in `__bureaucache__/`. |
| **a binary container** | `.sdwx` — fixed header, per-section crc-32, whole-file sha-256, zlib/lzma per section, directory at the tail, trailer word. |
| **a struct dsl** | declare a binary record with annotations; a metaclass compiles it to one `struct` format and validating descriptors. |
| **a hash-chained ledger** | append-only jsonl where every entry carries the hash of the one above it. |
| **an icon** | `tools/make_icon.py` writes a six-size `.ico` with no imaging library. |
| **80 self-tests** | including one that checks the hand-assembled machine code against a python reference across a thousand random inputs. |

zero third-party dependencies at runtime. pyinstaller is needed only to freeze
the `.exe`.

---

## running it

### from source

```bash
python -m dosl
```

that opens the counter. or use the command line:

```bash
python -m dosl submit --applicant "r. milquetoast" --bread rye --fillings "pastrami,pickle" --condiments mustard --cut diagonal --toasted
```

### building the executables

```powershell
.\build.ps1
```

five stages — venv, icon, forge, test, exe — and it will not freeze anything if
the tests fail. output:

| | |
|---|---|
| `dist\DoSL-Counter.exe` | windowed; double-click to open the counter |
| `dist\dosl.exe` | console; the full command line |

single stages: `.\build.ps1 -Stage forge`. start over: `.\build.ps1 -Clean`.

the dll is **not** bundled into the `.exe`. the executable carries the
assembler and re-forges `bureau_entropy.dll` on first run.

---

## the command line

```
dosl submit      submit form 27-b/6            --full --json --dossier PATH
dosl audit       disassemble a policy          dosl audit ethics --source
dosl record      read the permanent record     --verify --stats --tail N
dosl inspect     describe a .sdwx dossier      --extract certificate.txt
dosl forge       build and describe the dll    --disassemble
dosl policies    departments, fields, intrinsics
dosl doctor      report on every moving part
dosl support     donation address and source links
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

## the `.bureau` language

each department's regulations are a real compiled program. here is part of
`dosl/policies/condiment.bureau`:

```
policy condiment
version 3
regulation "dosl-7.4(a) -- on the application of spreadable matter"

let wet = condiment_count

require wet > 0
    else deny "a dry sandwich is a cracker with ambitions." severity 7

when "ketchup" in condiments and bread matches "brioche" then
    penalize 26 "ketchup on brioche is a cry for help."
    note "referred to the sub-committee on regrettable pairings."
end

require not has(condiments, "gravy")
    else deny "gravy is not a condiment. gravy is a weather system." severity 9

assess 96 - 2 * max(0, wet - 2)
```

`dosl audit condiment` shows what that becomes:

```
 0004  02 00 00        LOAD 0                  ; condiments
 0007  50 00 00 01 00  CALL 0 1                ; count/1
 0012  03 01 00        STORE 1                 ; wet
 0022  42 21 00        JUMP_IF_TRUE 33         ; -> 0033
 0025  01 02 00        CONST 2                 ; 'a dry sandwich is a cracker...'
 0028  60 07 00 02 00  FINDING 7 2             ; severity 7, DENIAL
```

**statements:** `policy` `version` `regulation` `let` `require…else`
`when…then…otherwise…end` `penalize` `award` `assess` `note` `cite`

**operators:** `+ - * / %`, `== != < <= > >=`, `in` `contains` `matches`,
`and` `or` `not` — `and`/`or` short-circuit, and `not` binds exactly like
python's `not` (below comparisons, above `and`), which it did not on the first
attempt.

**intrinsics:** `count` `len` `has` `distinct` `words` `any` `all` `abs` `min`
`max` `sum` `clamp` `round` `lower` `upper` `text` `number` `entropy` `sign`
`verdict` — the last three call into the dll.

keywords are matched case-insensitively, so `require`, `REQUIRE` and `Require`
are one word. identifiers stay case-sensitive.

policies see the form's fields and nothing else. no builtins, no module
globals, no filesystem, no loops, and a step limit in case someone hand-writes
bytecode that has one.

edit any `.bureau` file and rerun — the import hook notices the source digest
changed and recompiles. **engine → recompile all policies** does it without
restarting.

---

## about that dll

there is no c compiler on the machine this was written on. rather than add one,
`dosl/forge` builds the dll itself.

`kernels.py` defines five functions twice — once as hand-encoded x86-64, once
as plain python. `x64.py` encodes the instructions. `pe.py` links the result
into a pe32+ image with an export directory whose name table is sorted, because
`GetProcAddress` binary-searches it. `bridge.py` writes it to `build/`, loads it
with `ctypes.CDLL`, and checks the abi version the dll reports against the one
python expects.

`tests/test_forge.py::test_native_matches_reference` runs both implementations
over a thousand random inputs and requires identical bits. that is how a wrong
modr/m byte gets found without a debugger.

if anything fails — not windows, not x86-64, dll blocked by policy — the bridge
falls back to the python kernels and `dosl doctor` says so. the application
does not notice.

```
$ dosl doctor
engine          native: bureau_entropy.dll @ abi 0x00010003
dll             ...\build\bureau_entropy.dll
pe image        2560 bytes on disk, 0x4000 mapped, 5 exports
sections        .text@0x1000, .rdata@0x2000, .reloc@0x3000
record          14 entries, intact
```

---

## layout

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
  support.py   the donation and source details, in one place
tools/         make_icon.py
tests/         80 tests, run_all.py
build.ps1      venv -> icon -> forge -> test -> exe
DoSL.spec      two executables from one analysis
```

data lives in `%LOCALAPPDATA%\DoSL\permanent-record.jsonl`. override with
`DOSL_HOME`, or `--ledger PATH`.

---

## notes from the build

things that were wrong and are recorded here so they stay fixed:

- `not` originally bound tighter than comparisons, so
  `not bread matches "bowl"` meant `(not bread) matches "bowl"` — a test that
  always passed. `not` now has its own binding power between `and` and the
  comparisons.
- `assess` used to overwrite the running score, so any `award` above it was
  silently discarded. it now sets a baseline and adjustments accumulate
  separately.
- `DoSL.exe` and `dosl.exe` are the same file on ntfs. the second pyinstaller
  target was overwriting the first. hence `DoSL-Counter.exe`.
- ledger verification originally carried the *stored* hash forward, so editing
  one entry broke one link. it now carries the *recomputed* hash, so an edit
  invalidates everything below it.
- "surprise the department" could roll a one-slice 80mm sandwich, which the
  form's own cross-check rejects. it now redraws until the paperwork is valid.
- the lowercase pass turned up two words it could not reach: python renders
  booleans as `True`/`False`, and `datetime.isoformat()` puts a `T` in the
  middle of the timestamp. schedule c now prints yes/no, and the timestamp
  uses a space separator, which is still valid iso 8601.

---

## licence

mit. no sandwich was consulted during the drafting of these regulations.

---

## support + source

this and all of my other projects are open source, so please donate to keep
everything up!

> ### btc
> ```
> bc1qs4z04ltddh6vaqd4stu3p4vekv253ht4cwqma4
> ```

my other projects: **https://github.com/onononoo**
