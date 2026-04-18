# Example 04 — Implementation, Test, and Documentation Plan

**Status:** Ready for Review  
**Prerequisite reading:** `DESIGN.md`

---

## 0. Overview

This document describes the concrete work required to build Example 04.  It is
organized into five tracks that can proceed in dependency order:

```
Track 1: Compiler extension (zuspec-synth)
    ↓
Track 2: rv32i_decode.py   Track 3: rv32i_decode_manual.sv
    ↓                              ↓
Track 4: Synthesis comparison script + expected/ outputs
    ↓
Track 5: Documentation
```

---

## 1. Track 1 — Compiler Extension (`zuspec-synth`)

**Why first:** All other tracks depend on `ConstraintCompiler` accepting named
derived-field guards (`self.opcode == X`) in addition to bit-slice guards
(`self.instr[6:0] == X`).  Without this, `rv32i_decode.py` cannot be fed to
the synthesizer in its readable form.

### 1.1 Extend `constraint_compiler.py`: recognize `zdc.input()` fields

File: `packages/zuspec-synth/src/zuspec/synth/sprtl/constraint_compiler.py`

**Current behavior** (Phase A, `extract()`):
- Input field is detected by finding a non-rand field whose name appears in
  subscript conditions (`self.instr[6:0] == X`).
- The detection heuristic searches `cond_field_names` built from `_collect_subscript_fields()`.

**Required change:**
- After building `output_fields`, also scan dataclass fields for any field whose
  `dataclasses.field` default_factory is `Input` (the marker used by `zdc.input()`).
- If found, use it as `input_field_name` *unconditionally*, replacing the heuristic.
- This eliminates the need for subscript-guard scanning to detect the input.

```python
# In extract(), replace the input-detection block with:
from zuspec.dataclasses.decorators import Input  # existing class

for f in dataclasses.fields(self._cls):
    if callable(f.default_factory) and f.default_factory is Input:
        input_field_name = f.name
        w = (f.metadata or {}).get('width', None)
        if isinstance(w, int):
            input_field_width = w
        break
```

### 1.2 Extend `_parse_conditions()`: resolve derived-field guards

**Current behavior:**
`_parse_conditions()` only produces `{BitRange: value}` entries from subscript
nodes (`self.instr[6:0] == X`).  It returns `{}` for attribute comparisons
(`self.opcode == X`).

**Required change:**
When a condition is of the form `self.<field_name> == <value>`, look up
`<field_name>` in a pre-built map of `field_name → BitRange` that was populated
from `c_extract_fields` (or any constraint that asserts `self.<derived> == self.<input>[hi:lo]`).

Implementation sketch:
1. Add a pre-pass in `extract()` that walks all `@constraint` methods looking for
   patterns of the form `assert self.<derived> == <expr involving instr>`.  When
   `<expr>` reduces to a bit-slice of the input field, record
   `self._derived_to_bitrange[derived_name] = BitRange(msb, lsb)`.
2. In `_parse_conditions()`, add a branch for `type == 'compare'` where `left` is
   an attribute node (`self.<field_name>`): look up `field_name` in
   `_derived_to_bitrange`; if found, return `{BitRange: value}` as before.

Extraction constraint patterns to detect (all are `assert`s with no `if` guard):
- `self.opcode == (self.instr & 0x7F)` → `BitRange(6, 0)`
- `self.funct3 == ((self.instr >> 12) & 0x7)` → `BitRange(14, 12)`
- `self.funct7b5 == ((self.instr >> 30) & 0x1)` → `BitRange(30, 30)`

The pattern is: `(self.<input> >> <lsb>) & <mask>` where `mask == (1 << width) - 1`,
or equivalently `self.<input>[hi:lo]` in subscript form.  Both must be handled.

### 1.3 Add unit tests for the extension

File: `packages/zuspec-synth/tests/test_constraint_compiler.py` (new)

Test cases (each as a small self-contained class):

| Test | What it checks |
|------|----------------|
| `test_input_field_detected_from_zdc_input` | `extract()` finds input field via `zdc.input()` without any subscript guard |
| `test_derived_field_guard_resolves_to_bitrange` | `_parse_conditions()` maps `self.opcode == X` → `BitRange(6,0): X` |
| `test_extract_fields_mask_shift_parsed` | pre-pass reads `(self.instr >> 12) & 0x7` → `BitRange(14,12)` |
| `test_extract_fields_subscript_parsed` | pre-pass reads `self.instr[14:12]` → `BitRange(14,12)` |
| `test_full_pipeline_named_fields` | 3-instruction mini-decode with named guards → correct SV emitted |
| `test_full_pipeline_bitslice_fields` | same mini-decode with bit-slice guards → identical SV output |

Verification criterion: both named-field and bit-slice forms produce bit-for-bit
identical SV output from `emit_sv()`.

---

## 2. Track 2 — `rv32i_decode.py`

Files: `examples/04_constraints/rv32_core.py` and `examples/04_constraints/rv32i_decode.py`

### 2.1 Component and Action definition

`RV32IDecode` is a `zdc.Action[RV32Core]`, not a Struct.  This integrates
naturally with the Zuspec simulation and synthesis flow: the component owns a
`fetch` method port that the `proc` calls to get instruction words, then invokes
the decode action for each one.

**`rv32_core.py` — stub component:**

```python
"""RV32 Core stub — provides the Fetch method port consumed by RV32IDecode."""
import zuspec.dataclasses as zdc

@zdc.dataclass
class RV32Core(zdc.Component):
    """Minimal processor core stub.  Real fetch logic would live here."""

    # Method port: called by the proc to obtain the next instruction word.
    fetch: zdc.InPort[zdc.u32] = zdc.in_port()

    @zdc.proc
    async def _run(self):
        while True:
            instr = await self.fetch()
            await RV32IDecode(instr=instr)(self)
```

**`rv32i_decode.py` — the decode action:**

```python
"""Example 04 — Constraint-based RV32I instruction decode.

Primary teaching point: each instruction is described independently;
the synthesizer derives optimized RTL automatically.
"""
import zuspec.dataclasses as zdc
from zuspec.dataclasses import constraint
from rv32_core import RV32Core

# ALU operations (4 bits)
ALU_ADD, ALU_SUB, ALU_SLL, ALU_SLT, ALU_SLTU = 0, 1, 2, 3, 4
ALU_XOR, ALU_SRL, ALU_SRA, ALU_OR,  ALU_AND  = 5, 6, 7, 8, 9
ALU_PASS                                      = 10  # forward immediate (LUI)

# Immediate format (3 bits)
IMM_NONE, IMM_I, IMM_S, IMM_B, IMM_U, IMM_J = 0, 1, 2, 3, 4, 5

@zdc.dataclass
class RV32IDecode(zdc.Action[RV32Core]):
    # Primary input — the raw instruction word, passed in by the caller
    instr    : zdc.u32 = zdc.input()

    # Decode-relevant sub-fields (constrained to be bit-slices of instr)
    opcode   : zdc.u7  = zdc.rand()
    funct3   : zdc.u3  = zdc.rand()
    funct7b5 : zdc.u1  = zdc.rand()

    # Decode outputs (rand — solved by constraints; synthesized to wires)
    alu_op   : zdc.u4  = zdc.rand()
    imm_sel  : zdc.u3  = zdc.rand()
    use_rs1  : zdc.u1  = zdc.rand()
    use_rs2  : zdc.u1  = zdc.rand()
    use_rd   : zdc.u1  = zdc.rand()
    is_load  : zdc.u1  = zdc.rand()
    is_store : zdc.u1  = zdc.rand()
    is_branch: zdc.u1  = zdc.rand()
    is_jal   : zdc.u1  = zdc.rand()
    is_jalr  : zdc.u1  = zdc.rand()
    mem_width : zdc.u2 = zdc.rand()   # 0=byte, 1=half, 2=word
    mem_signed: zdc.u1 = zdc.rand()   # 1 = sign-extend load result

    @constraint
    def c_extract_fields(self):
        assert self.opcode   == (self.instr & 0x7F)
        assert self.funct3   == ((self.instr >> 12) & 0x7)
        assert self.funct7b5 == ((self.instr >> 30) & 0x1)

    # 37 per-instruction @constraint blocks follow (§2.2)

    async def body(self):
        pass  # decode is pure combinational; body is a no-op in simulation
```

### 2.2 Instruction enumeration

All 37 RV32I base instructions, grouped by opcode class.  Each block uses only
`if` guards on `self.opcode`, `self.funct3`, `self.funct7b5`.

**R-type (`opcode=0x33`, 10 instructions):**

| Constraint | funct3 | funct7b5 | alu_op |
|------------|--------|----------|--------|
| `c_add`    | 000    | 0        | ADD    |
| `c_sub`    | 000    | 1        | SUB    |
| `c_sll`    | 001    | 0        | SLL    |
| `c_slt`    | 010    | 0        | SLT    |
| `c_sltu`   | 011    | 0        | SLTU   |
| `c_xor`    | 100    | 0        | XOR    |
| `c_srl`    | 101    | 0        | SRL    |
| `c_sra`    | 101    | 1        | SRA    |
| `c_or`     | 110    | 0        | OR     |
| `c_and`    | 111    | 0        | AND    |

All R-type: `use_rs1=1, use_rs2=1, use_rd=1, imm_sel=IMM_NONE, is_load=0, is_store=0, is_branch=0, is_jal=0, is_jalr=0`

**I-type ALU (`opcode=0x13`, 9 instructions):**

| Constraint | funct3 | funct7b5 | alu_op | note |
|------------|--------|----------|--------|------|
| `c_addi`   | 000    | —        | ADD    |      |
| `c_slti`   | 010    | —        | SLT    |      |
| `c_sltiu`  | 011    | —        | SLTU   |      |
| `c_xori`   | 100    | —        | XOR    |      |
| `c_ori`    | 110    | —        | OR     |      |
| `c_andi`   | 111    | —        | AND    |      |
| `c_slli`   | 001    | 0        | SLL    | funct7b5 distinguishes |
| `c_srli`   | 101    | 0        | SRL    | funct7b5 distinguishes |
| `c_srai`   | 101    | 1        | SRA    | funct7b5 distinguishes |

All I-ALU: `use_rs1=1, use_rs2=0, use_rd=1, imm_sel=IMM_I, is_load=0, is_store=0, is_branch=0, is_jal=0, is_jalr=0`

**Load (`opcode=0x03`, 5 instructions):**

| Constraint | funct3 | mem_width | mem_signed |
|------------|--------|-----------|------------|
| `c_lb`     | 000    | 0 (byte)  | 1          |
| `c_lh`     | 001    | 1 (half)  | 1          |
| `c_lw`     | 010    | 2 (word)  | 1          |
| `c_lbu`    | 100    | 0 (byte)  | 0          |
| `c_lhu`    | 101    | 1 (half)  | 0          |

All loads: `alu_op=ADD, use_rs1=1, use_rs2=0, use_rd=1, imm_sel=IMM_I, is_load=1, is_store=0, is_branch=0, is_jal=0, is_jalr=0`

**Store (`opcode=0x23`, 3 instructions):**

| Constraint | funct3 | mem_width |
|------------|--------|-----------|
| `c_sb`     | 000    | 0 (byte)  |
| `c_sh`     | 001    | 1 (half)  |
| `c_sw`     | 010    | 2 (word)  |

All stores: `alu_op=ADD, use_rs1=1, use_rs2=1, use_rd=0, imm_sel=IMM_S, is_load=0, is_store=1, is_branch=0, is_jal=0, is_jalr=0, mem_signed=0`

**Branch (`opcode=0x63`, 6 instructions):**

| Constraint | funct3 | alu_op |
|------------|--------|--------|
| `c_beq`    | 000    | XOR (zero-test) |
| `c_bne`    | 001    | XOR    |
| `c_blt`    | 100    | SLT    |
| `c_bge`    | 101    | SLT    |
| `c_bltu`   | 110    | SLTU   |
| `c_bgeu`   | 111    | SLTU   |

All branches: `use_rs1=1, use_rs2=1, use_rd=0, imm_sel=IMM_B, is_load=0, is_store=0, is_branch=1, is_jal=0, is_jalr=0, mem_width=0, mem_signed=0`

**Upper-immediate and jump (`opcode` unique per instruction, 4 instructions):**

| Constraint | opcode  | note |
|------------|---------|------|
| `c_lui`    | 0x37    | `alu_op=PASS, imm_sel=IMM_U, use_rs1=0, use_rs2=0, use_rd=1` |
| `c_auipc`  | 0x17    | `alu_op=ADD, imm_sel=IMM_U, use_rs1=0, use_rs2=0, use_rd=1` |
| `c_jal`    | 0x6F    | `imm_sel=IMM_J, use_rs1=0, use_rs2=0, use_rd=1, is_jal=1` |
| `c_jalr`   | 0x67    | `imm_sel=IMM_I, use_rs1=1, use_rs2=0, use_rd=1, is_jalr=1` |

All four: `is_load=0, is_store=0, is_branch=0, mem_width=0, mem_signed=0`

> **Total: 10 + 9 + 5 + 3 + 6 + 4 = 37 constraints.**

### 2.3 `rv32m_decode.py` — M extension subclass

File: `examples/04_constraints/rv32m_decode.py`

Subclasses `RV32IDecode` — itself a `zdc.Action[RV32Core]`.  Adds one new derived
field (`funct7b0`) and 8 instruction constraints.  Zero changes to the base class.
constraints.  Zero changes to the base class.

```python
from rv32i_decode import RV32IDecode

ALU_MUL, ALU_MULH, ALU_MULHSU, ALU_MULHU = 11, 12, 13, 14
ALU_DIV, ALU_DIVU, ALU_REM,    ALU_REMU  = 15, 16, 17, 18

@zdc.dataclass
class RV32MDecode(RV32IDecode):
    funct7b0 : zdc.u1 = zdc.rand()   # instr[25]

    @constraint
    def c_extract_funct7b0(self):
        assert self.funct7b0 == ((self.instr >> 25) & 0x1)

    # 8 instruction blocks: c_mul, c_mulh, c_mulhsu, c_mulhu,
    #                       c_div, c_divu, c_rem, c_remu
    # Guard: opcode == 0x33 and funct3 == <N> and funct7b0 == 1
```

**Size estimate:** ~110 lines.

---

## 3. Track 3 — `expected/rv32i_decode_manual.sv`

File: `examples/04_constraints/expected/rv32i_decode_manual.sv`

A **straightforward case statement** for all 37 instructions — the code a
competent engineer would write on first pass, without Karnaugh-map optimization:

```systemverilog
module rv32i_decode_manual (
    input  logic [31:0] instr,
    output logic [3:0]  alu_op,
    output logic [2:0]  imm_sel,
    output logic        use_rs1, use_rs2, use_rd,
    output logic        is_load, is_store, is_branch, is_jal, is_jalr,
    output logic [1:0]  mem_width,
    output logic        mem_signed
);

logic [6:0] opcode;
logic [2:0] funct3;
logic       funct7b5;

assign opcode   = instr[6:0];
assign funct3   = instr[14:12];
assign funct7b5 = instr[30];

always_comb begin
    // Defaults (no latches; every output driven on every path)
    alu_op    = 4'd0;  imm_sel = 3'd0;
    use_rs1   = 1'b0;  use_rs2 = 1'b0;  use_rd  = 1'b0;
    is_load   = 1'b0;  is_store = 1'b0; is_branch = 1'b0;
    is_jal    = 1'b0;  is_jalr  = 1'b0;
    mem_width = 2'd0;  mem_signed = 1'b0;

    case (opcode)
        7'h33: begin  // R-type
            use_rs1 = 1; use_rs2 = 1; use_rd = 1;
            case ({funct7b5, funct3})
                4'b0_000: alu_op = ALU_ADD;
                4'b1_000: alu_op = ALU_SUB;
                // ... all 10 R-type
            endcase
        end
        7'h13: begin  // I-type ALU
            use_rs1 = 1; use_rd = 1; imm_sel = IMM_I;
            // ...
        end
        // ... etc for each opcode group
    endcase
end
endmodule
```

**Authoring guidelines:**
- Nested `case` on `{funct7b5, funct3}` inside opcode groups (natural structure)
- Default assignments cover all outputs (no latches)
- No hand-minimization of `use_rs2`, `use_rd`, etc. — assign them in each arm
- This represents "correct-but-unoptimized" RTL, not adversarial bad code

---

## 4. Track 4 — Synthesis Comparison

### 4.1 Synthesis script: `synth_compare.py`

File: `examples/04_constraints/synth_compare.py`

Automates the full comparison workflow:

```
Step 1: Run ConstraintCompiler pipeline on RV32IDecode
        → write examples/04_constraints/expected/rv32i_decode.sv

Step 2: Run yowasp-yosys on rv32i_decode.sv
        → capture: cell count, logic depth, wire count

Step 3: Run yowasp-yosys on rv32i_decode_manual.sv
        → capture: same metrics

Step 4: Print side-by-side comparison table

Step 5: Optionally write expected/synth_report.txt
```

The Yosys invocation for each:
```tcl
# synthesis script (run with yowasp-yosys -p '...')
read_verilog rv32i_decode.sv
synth -top rv32i_decode -flatten
opt -full
stat           # cell count + area
ltp -noff      # logic timing path (depth)
```

Key metrics to extract from `stat` output:
- `Number of cells` (total gate count after `opt`)
- `Longest topological path` (from `ltp`) = logic depth in cell stages

Comparison table format:
```
Metric                     Constraint-synthesized   Hand-coded case
-----------------------------------------------------------------
Cells after opt            XX                       XX
Logic depth (stages)       XX                       XX
Wires                      XX                       XX
Generated by               ConstraintCompiler        Manual
```

### 4.2 Generating `expected/rv32i_decode.sv`

`ConstraintCompiler` operates on IR and emits wire-level SV logic, not a complete
module.  Module wrapping is the responsibility of the larger synthesis flow (the
same flow that handles `RV32Core` and other components).  `synth_compare.py`
therefore drives the synthesis flow on `RV32Core` with `RV32IDecode` registered,
which produces a fully-wrapped module as part of its normal output.

The ConstraintCompiler pipeline (invoked internally by the synthesis flow) is:
```python
cc = ConstraintCompiler(RV32IDecode, prefix='')
cc.extract()
cc.compute_support()
cc.validate()
cc.build_cubes()   # preferred fast path for sparse decode tables
cc.minimize()
lines = cc.emit_sv()  # wire-level SV; module wrapper added by synthesis flow
```

### 4.3 Metrics validation

The comparison script should assert (not just print) that the synthesized form is
no worse than the manual form on logic depth — i.e., the constraint-based approach
meets its claimed "2 LUT levels" guarantee.  This makes `synth_compare.py` usable
as a regression test.

---

## 5. Track 5 — Tests

### 5.1 Simulation test: `test_rv32i_decode.py`

File: `examples/04_constraints/test_rv32i_decode.py`

**One test function per instruction** (37 total), parametrized with `pytest.mark.parametrize`.
Each test:
1. Builds a canonical encoding for the instruction (all register fields = 0)
2. Invokes `RV32IDecode(instr=<encoding>)` against a stub `RV32Core` instance
3. Asserts every decode output field matches the expected value

```python
import pytest
from zuspec.dataclasses import simulate
from rv32_core import RV32Core
from rv32i_decode import RV32IDecode, ALU_ADD, ALU_SUB, IMM_NONE, IMM_I  # etc.

# Instruction encodings: {name: (instr_word, expected_outputs_dict)}
CASES = {
    'ADD':  (0x00000033, {'alu_op': ALU_ADD,  'use_rs2': 1, 'is_load': 0}),
    'SUB':  (0x40000033, {'alu_op': ALU_SUB,  'use_rs2': 1, 'is_load': 0}),
    'ADDI': (0x00000013, {'alu_op': ALU_ADD,  'use_rs2': 0, 'imm_sel': IMM_I}),
    'LW':   (0x00002003, {'is_load': 1, 'mem_width': 2, 'mem_signed': 1}),
    # ... all 37
}

@pytest.mark.parametrize('name,instr_word,expected', [
    (n, v[0], v[1]) for n, v in CASES.items()
])
def test_decode(name, instr_word, expected):
    core = RV32Core()
    action = RV32IDecode(instr=instr_word)
    action(core)                          # invoke action against the core
    for field, val in expected.items():
        assert getattr(action, field) == val, f"{name}: {field} mismatch"
```

> **Note:** The exact invocation API (`action(core)` vs `await action(core)` vs
> a simulation harness call) must be confirmed against the `zuspec-dataclasses`
> test infrastructure before writing the final test file.

**Additional tests:**
- `test_mutual_exclusion`: two different encodings produce different `alu_op` values
- `test_undefined_opcode_no_crash`: an unused opcode encoding does not raise

### 5.2 Synthesis test (within `synth_compare.py`)

`synth_compare.py --test` mode:
- Runs full synthesis pipeline on both files
- Asserts constraint-synthesized logic depth ≤ manual logic depth
- Asserts constraint-synthesized cell count ≤ manual cell count × 1.1
  (allow 10% slack for metric variance, flag anything worse)
- Returns exit code 0 on pass, 1 on fail

### 5.3 Compiler extension tests (Track 1)

See §1.3 above.  Run with: `cd packages/zuspec-synth && pytest tests/test_constraint_compiler.py`

---

## 6. Documentation

### 6.1 Update `DESIGN.md`

After implementation:
- Replace estimated cell counts (§6.5 table) with actual measured values from
  `synth_compare.py` output
- Update §6.6 "Compiler Input-Field Requirements" from decision language to past
  tense ("was extended to...")
- Remove "Status: Draft" header

### 6.2 `README.md` for the example

File: `examples/04_constraints/README.md`

Sections:
1. **What this example shows** (2–3 sentences)
2. **Running the simulation tests** (`pytest test_rv32i_decode.py`)
3. **Running synthesis comparison** (`python synth_compare.py`)
4. **Reading the output** (how to interpret the cell-count table)
5. **Extending the design** (point to `rv32m_decode.py`)

---

## 7. File Inventory

```
examples/04_constraints/
├── DESIGN.md                    existing — update metrics after impl
├── PLAN.md                      this document
├── rv32_core.py                 Track 2 — RV32Core component stub with Fetch port
├── rv32i_decode.py              Track 2 — RV32IDecode action, 37 constraints
├── rv32m_decode.py              Track 2 — RV32MDecode subclass, 8 M-ext constraints
├── test_rv32i_decode.py         Track 5 — 37-instruction simulation test
├── synth_compare.py             Track 4 — synthesis pipeline + comparison
├── README.md                    Track 6 — user-facing tutorial
└── expected/
    ├── rv32i_decode.sv          Track 4 — ConstraintCompiler output (via synth flow)
    ├── rv32i_decode_manual.sv   Track 3 — hand-coded case statement
    └── synth_report.txt         Track 4 — generated comparison table

packages/zuspec-synth/
├── src/zuspec/synth/sprtl/
│   └── constraint_compiler.py  Track 1 — extend input detection + derived guards
└── tests/
    └── test_constraint_compiler.py  Track 1 — 6 new unit tests
```

---

## 8. Dependency Order / Suggested Work Sequence

1. **Track 1** (compiler extension + its tests) — blocks everything else
2. **Track 3** (manual SV) — can be written in parallel with Track 1, no deps
3. **Track 2** (`rv32i_decode.py`) — after Track 1 is merged and tested
4. **Track 4** (`synth_compare.py` + `expected/`) — after Tracks 2 and 3
5. **Track 5** (simulation tests) — after Track 2
6. **Track 6** (README + DESIGN.md update) — last, after measured results exist

---

## 9. Decisions from Review

1. **`RV32IDecode` is an Action, not a Struct.**  A `RV32Core` component stub
   provides a `fetch` method port.  The core `proc` calls `fetch()` to obtain an
   instruction word, then invokes `RV32IDecode(instr=word)(self)`.  This is the
   natural integration point for the synthesis flow and matches the rest of the
   Zuspec Action model.  A new file `rv32_core.py` holds the component stub.

2. **`ConstraintCompiler` works on IR only.**  Module wrapping (the `module`
   declaration, port list, `endmodule`) is the responsibility of the larger
   synthesis flow.  `synth_compare.py` drives the synthesis flow on `RV32Core`,
   which internally invokes `ConstraintCompiler` and wraps the result.  No
   `wrap_module()` helper is needed in the example.

3. **`build_cubes` vs `build_table` — heuristic-driven selection.**  Good
   heuristics for selecting the appropriate path are needed in the compiler;
   future work should also support annotations/pragmas to give users explicit
   control.  For this example, `build_cubes` (sparse path) is the correct choice
   for 37 constraints over 11 support bits.

4. **Omitting a condition = don't-care (confirmed).**  When a constraint's `if`
   guard does not mention `funct7b5` (e.g., ADDI, SLTI, etc.), the compiler
   treats that bit as a don't-care in the SOP table.  No explicit `funct7b5`
   wildcard is needed.

