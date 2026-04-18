# Example 04 — Constraints: Instruction Decode

**Status:** Draft for Review  
**Audience:** Zuspec contributors, example users, tooling integrators

---

## 1. The Goal

This example demonstrates why constraints are a **superior design construct** for
instruction decode logic compared to traditional if/else or case statement trees.  
Three concrete claims drive the design:

1. **Simpler description** — Each instruction is specified independently, in isolation.
   There is no monolithic decode tree to read or reason about.

2. **Better synthesis** — The synthesizer sees a complete truth table and applies global
   multi-output Boolean minimization (Quine-McCluskey with CSE), finding shared terms
   across instructions automatically.  A human writing the same logic by hand would need
   to perform the same analysis manually to reach the same quality.  **No external
   minimizer tool is required** — `zuspec-synth` includes a self-contained QM
   implementation in pure Python.

3. **Zero-touch extensibility** — Adding a new instruction requires adding exactly one
   new `@constraint` block.  No existing code is touched.

---

## 2. Why Instruction Decode?

Instruction decode is the canonical combinational-logic design problem:

* The input is a fixed-width instruction word.
* The outputs are a set of control signals consumed by the datapath.
* The mapping is a pure Boolean function — no state, no timing, no protocol.
* The input space is large enough (37 + instructions) that manual optimization is
  non-trivial, but small enough to be fully enumerable, making the synthesizer's
  job tractable and the result verifiable.

Instruction decode is also **familiar** to a wide audience of hardware designers,
making the contrast between the traditional and constraint-based approaches immediately
legible.

---

## 3. Instruction Format: RISC-V RV32I

We use the **RISC-V RV32I base integer instruction set** (37 instructions).
RISC-V is open, thoroughly documented, and already present in the repository
(`src/org/zuspec/example/mls/riscv/`).  Its encoding was explicitly designed for
clean decode, which means a well-optimized human decoder *is* achievable — making
it a fair benchmark.

### 3.1 Encoding Fields

| Bits   | Field    | Description                                 |
|--------|----------|---------------------------------------------|
| [6:0]  | `opcode` | Instruction format category                 |
| [11:7] | `rd`     | Destination register (not used for decode)  |
| [14:12]| `funct3` | Operation discriminator within opcode group |
| [19:15]| `rs1`    | Source register 1 (not used for decode)     |
| [24:20]| `rs2`    | Source register 2 (not used for decode)     |
| [31:25]| `funct7` | Extended discriminator (R-type only)        |

Decode depends only on `opcode`, `funct3`, and `funct7[5]` (17 bits effectively).
The remaining bits are register addresses and immediate data — pure datapath wiring,
not decode.

### 3.2 Opcode Map

| `opcode[6:2]` | Format  | Instructions                              |
|---------------|---------|-------------------------------------------|
| `01101`       | U       | LUI                                       |
| `00101`       | U       | AUIPC                                     |
| `11011`       | J       | JAL                                       |
| `11001`       | I       | JALR                                      |
| `11000`       | B       | BEQ, BNE, BLT, BGE, BLTU, BGEU           |
| `00000`       | I       | LB, LH, LW, LBU, LHU                     |
| `01000`       | S       | SB, SH, SW                               |
| `00100`       | I       | ADDI, SLTI, SLTIU, XORI, ORI, ANDI, SLLI, SRLI, SRAI |
| `01100`       | R       | ADD, SUB, SLL, SLT, SLTU, XOR, SRL, SRA, OR, AND |

> **Note:** `opcode[1:0] == 2'b11` for all 32-bit RV32I instructions; these two bits
> carry no useful decode information and are excluded from synthesis inputs.

---

## 4. Traditional Approach and Its Problems

A typical RTL engineer writes a nested case statement:

```systemverilog
always_comb begin
    // Defaults
    alu_op    = ALU_ADD;
    imm_sel   = IMM_I;
    use_rs2   = 0;
    is_load   = 0;
    is_store  = 0;
    is_branch = 0;
    // ... more defaults ...

    case (opcode)
        7'b0110011: begin   // R-type
            use_rs2 = 1;
            imm_sel = IMM_NONE;
            case (funct3)
                3'b000: alu_op = funct7[5] ? ALU_SUB : ALU_ADD;
                3'b001: alu_op = ALU_SLL;
                3'b010: alu_op = ALU_SLT;
                // ...
            endcase
        end
        7'b0010011: begin   // I-type ALU
            // ...
        end
        // ... 7 more opcode arms ...
    endcase
end
```

**Problems with this approach:**

| Problem | Impact |
|---------|--------|
| **Monolithic structure** | Adding ADDI requires opening the `0010011` arm and editing existing code |
| **Hidden defaults** | A missing assignment silently infers a latch or keeps the default — bugs are non-local |
| **Manual optimization required** | The engineer must notice that `is_load` = `opcode[6:2] == 5'b00000` — the tool only minimizes what it gets |
| **Implicit coupling** | Instructions in the same `funct3` group share an arm; a change to one can affect another |
| **Readability degrades** | With 37 instructions spread across 9 opcode groups, the code becomes hard to audit |

---

## 5. Constraint-Based Decode

### 5.1 Component and Action Definition

`RV32IDecode` is a `zdc.Action[RV32Core]`.  A lightweight `RV32Core` component
stub provides a `fetch` method port; the core's `proc` fetches instruction words
and invokes the decode action for each one.  This is the natural integration
point for the synthesis flow and matches the rest of the Zuspec Action model.

**`rv32_core.py` — stub component:**

```python
import zuspec.dataclasses as zdc

@zdc.dataclass
class RV32Core(zdc.Component):
    fetch: zdc.InPort[zdc.u32] = zdc.in_port()

    @zdc.proc
    async def _run(self):
        while True:
            instr = await self.fetch()
            await RV32IDecode(instr=instr)(self)
```

**`rv32i_decode.py` — the decode action:**

```python
import zuspec.dataclasses as zdc
from zuspec.dataclasses import constraint
from rv32_core import RV32Core

@zdc.dataclass
class RV32IDecode(zdc.Action[RV32Core]):
    """RISC-V RV32I instruction decode action."""

    # -----------------------------------------------------------------------
    # Primary input: raw instruction word, passed by the caller
    # -----------------------------------------------------------------------
    instr: zdc.u32 = zdc.input()

    # -----------------------------------------------------------------------
    # Internal derived fields — constrained to be bit-slices of instr
    # -----------------------------------------------------------------------
    opcode  : zdc.u7 = zdc.rand()   # instr[6:0]
    funct3  : zdc.u3 = zdc.rand()   # instr[14:12]
    funct7b5: zdc.u1 = zdc.rand()   # instr[30]

    # -----------------------------------------------------------------------
    # Decode outputs (rand — solved by constraints, synthesized to wires)
    # -----------------------------------------------------------------------
    alu_op    : zdc.u4 = zdc.rand()
    imm_sel   : zdc.u3 = zdc.rand()
    use_rs1   : zdc.u1 = zdc.rand()
    use_rs2   : zdc.u1 = zdc.rand()
    use_rd    : zdc.u1 = zdc.rand()
    is_load   : zdc.u1 = zdc.rand()
    is_store  : zdc.u1 = zdc.rand()
    is_branch : zdc.u1 = zdc.rand()
    is_jal    : zdc.u1 = zdc.rand()
    is_jalr   : zdc.u1 = zdc.rand()
    mem_width : zdc.u2 = zdc.rand()   # 0=byte, 1=half, 2=word
    mem_signed: zdc.u1 = zdc.rand()   # 1 = sign-extend load

    # -----------------------------------------------------------------------
    # Field extraction constraints — bind derived fields to instr bit-slices
    # Synthesized to plain wire assignments; never generate logic
    # -----------------------------------------------------------------------
    @constraint
    def c_extract_fields(self):
        assert self.opcode   == (self.instr & 0x7F)
        assert self.funct3   == ((self.instr >> 12) & 0x7)
        assert self.funct7b5 == ((self.instr >> 30) & 0x1)

    async def body(self):
        pass  # decode is pure combinational; body is a no-op in simulation
```

### 5.2 ALU Op and Immediate Format Enumerations

```python
# ALU operation codes
ALU_ADD  = 0;  ALU_SUB  = 1;  ALU_XOR  = 2;  ALU_SLT  = 3
ALU_SLTU = 4;  ALU_SRL  = 5;  ALU_SRA  = 6;  ALU_OR   = 7
ALU_AND  = 8;  ALU_SLL  = 9;  ALU_PASS = 10  # PASS: forward imm as result (LUI)

# Immediate format selector
IMM_NONE = 0;  IMM_I = 1;  IMM_S = 2;  IMM_B = 3
IMM_U    = 4;  IMM_J = 5
```

### 5.3 Per-Instruction Constraints

Each instruction is one self-contained `@constraint` block.  The guard tests the
derived fields (`opcode`, `funct3`, `funct7b5`) — which are themselves constrained
to be bit-slices of `instr`, so constraining `instr` from outside is sufficient to
select a specific instruction.  The body asserts the decode outputs.  No instruction
block touches another:

The `if` form separates the **encoding guard** (input pattern) from the **decode
consequent** (output values) cleanly.  This is semantically equivalent to
`implies(condition, body)` — the constraint only applies when the guard holds.

```python
    # -------------------------------------------------------------------
    # R-type ALU
    # -------------------------------------------------------------------

    @constraint
    def c_add(self):
        if self.opcode == 0b0110011 and self.funct3 == 0b000 and self.funct7b5 == 0:
            assert self.alu_op    == ALU_ADD
            assert self.imm_sel   == IMM_NONE
            assert self.use_rs1   == 1
            assert self.use_rs2   == 1
            assert self.use_rd    == 1
            assert self.is_load   == 0
            assert self.is_store  == 0
            assert self.is_branch == 0
            assert self.is_jal    == 0
            assert self.is_jalr   == 0

    @constraint
    def c_sub(self):
        if self.opcode == 0b0110011 and self.funct3 == 0b000 and self.funct7b5 == 1:
            assert self.alu_op    == ALU_SUB   # ← only difference from c_add
            assert self.imm_sel   == IMM_NONE
            assert self.use_rs1   == 1
            assert self.use_rs2   == 1
            assert self.use_rd    == 1
            assert self.is_load   == 0
            assert self.is_store  == 0
            assert self.is_branch == 0
            assert self.is_jal    == 0
            assert self.is_jalr   == 0

    # ... (one block per instruction: AND, OR, XOR, SLL, SRL, SRA, SLT, SLTU,
    #      ADDI, ANDI, ORI, XORI, SLLI, SRLI, SRAI, SLTI, SLTIU,
    #      LB, LH, LW, LBU, LHU, SB, SH, SW,
    #      BEQ, BNE, BLT, BGE, BLTU, BGEU,
    #      LUI, AUIPC, JAL, JALR) ...

    # -------------------------------------------------------------------
    # Example: adding a hypothetical new instruction later
    # -------------------------------------------------------------------

    # @constraint
    # def c_my_new_insn(self):
    #     if self.opcode == 0b0001011 and self.funct3 == 0b000:  # custom-0
    #         assert self.alu_op  == ALU_MY_OP
    #         assert self.use_rs1 == 1
    #         assert self.use_rd  == 1
    #         # (uncomment — nothing else changes)
```

**Structural properties of this representation:**

* Every block is **independent** — no instruction reads or modifies another's fields.
* The encoding and the decode outputs are **co-located** — the full specification of
  ADD is in `c_add`, not scattered across multiple case arms.
* There are **no defaults to manage** — each block fully specifies every output.
  Missing an output field in one block is a statically-detectable error.

---

## 6. Synthesis Strategy and Expected RTL Quality

### 6.1 The `zuspec-synth` ConstraintCompiler Pipeline

`zuspec-synth` contains a self-contained `ConstraintCompiler` class
(`packages/zuspec-synth/src/zuspec/synth/sprtl/constraint_compiler.py`) that
translates `@constraint` methods directly to SystemVerilog `wire` + `assign`
statements.  **No external minimizer tool (espresso, QM, ABC) is required.**
The entire pipeline is pure Python.

The pipeline has six phases:

| Phase | Name              | What it does                                                       |
|-------|-------------------|--------------------------------------------------------------------|
| A     | `extract`         | Parse `@constraint` ASTs → `ConstraintBlock` records (conditions + assignments) |
| B     | `compute_support` | Union all `BitRange` conditions → ordered support-bit vector       |
| C     | `validate`        | Mutual-exclusion check via `PropertyAnalyzer` (no overlapping guards) |
| D     | `build_cubes`     | Sparse minterm enumeration per block; records both ON-cubes and explicit OFF-cubes |
| E     | `minimize`        | **Cube GROW** (primary): exploits don't-care space via prime-implicant expansion; `MultiOutputQM` (fallback) |
| F     | `emit_sv`         | Emit named `wire` declarations + `assign` statements for every output bit |

### 6.2 The Cube GROW Minimizer

`cube_minimizer.py` implements a don't-care–aware prime implicant expansion (Espresso EXPAND
variant) in pure Python — no external tool required:

* `_grow(cube, off_cubes)` — for each bit in the input cube's mask, attempt to drop the bit
  constraint; accept the drop iff the enlarged cube remains disjoint from every OFF-cube.
  The result is the maximal prime implicant (PI) for that ON-cube.
* `CubeExpandMinimizer.minimize(on_cubes_by_bit, off_cubes_by_bit)` — expands all ON-cubes
  to PIs, removes dominated PIs (`_remove_dominated`), then runs a greedy set-cover
  (`_greedy_cover`) to select the minimal subset covering every ON-minterm.
* `MultiOutputQM.minimize_from_cube_sets()` — joint CSE across outputs: shared product terms
  become named intermediate wires, reducing gate count further.

**Key insight:** when `build_cubes` records explicit OFF-cubes (from `output = 0` assignments),
the GROW algorithm can distinguish genuine don't-cares from must-be-0 space, unlocking
prime implicants that span multiple instruction groups.

### 6.3 The QM Minimizer (Fallback)

`qm_minimizer.py` implements a complete Quine-McCluskey algorithm in pure Python:

* `QMMinimizer.minimize()` — standard minterm-based PI generation + essential-PI
  selection + greedy set-cover.
* `QMMinimizer.minimize_from_cubes()` — fast path that avoids minterm expansion
  (preferred for this use case).
* `MultiOutputQM.minimize_from_cube_sets()` — joint minimization across all output
  bits simultaneously, with common-subexpression elimination (CSE): product terms
  shared by multiple outputs become named intermediate wires.

**Safety valve:** if `n_vars > 20`, the minimizer skips optimization and returns
raw minterms with a warning.  For RV32I decode, the support is:
* `instr[6:0]` — 7 bits (opcode, but `[1:0]` are always `11`)
* `instr[14:12]` — 3 bits (funct3)
* `instr[30]` — 1 bit (funct7[5])

**Total: 11 support bits** — well within the 20-bit limit.  The minimizer runs
in milliseconds at this scale.

### 6.3 From Constraints to Truth Table

The per-instruction constraints map to rows in a sparse multi-output truth table
whose inputs are the support-bit slices of `instr`:

| instr[6:2] | instr[14:12] | instr[30] | alu_op | imm_sel | use_rs2 | is_load | ... |
|------------|--------------|-----------|--------|---------|---------|---------|-----|
| 01100      | 000          | 0         | ADD    | NONE    | 1       | 0       | ... |
| 01100      | 000          | 1         | SUB    | NONE    | 1       | 0       | ... |
| 01100      | 111          | 0         | AND    | NONE    | 1       | 0       | ... |
| 00000      | 010          | —         | ADD    | I       | 0       | 1       | ... |
| ...        | ...          | ...       | ...    | ...     | ...     | ...     | ... |

Phase D's sparse enumeration visits at most 2^(11 − k) minterms per block
(where k is the number of constrained bits in the guard), avoiding a full 2^11
truth-table build.

### 6.4 Minimization Opportunities and CSE

Several control signals reduce to single-gate expressions after minimization:

| Signal      | Minimized expression                       | Description                    |
|-------------|-------------------------------------------|--------------------------------|
| `is_load`   | `instr[6:2] == 5'b00000`                 | All loads share one opcode     |
| `is_store`  | `instr[6:2] == 5'b01000`                 | All stores share one opcode    |
| `is_branch` | `instr[6:2] == 5'b11000`                 | All branches share one opcode  |
| `use_rs2`   | `is_r_type \| is_store \| is_branch`     | CSE: shared product term       |
| `use_rd`    | `~(is_store \| is_branch)`               | All non-store/branch write rd  |
| `mem_signed`| `~instr[14]` (when `is_load`)            | LBU/LHU have funct3[2] set     |

CSE promotes repeated product terms (e.g., `instr[6:2] == 01100` for all R-type)
to named intermediate wires in the emitted SV.  A human designer who notices all
of these shared terms is skilled; `MultiOutputQM` finds them automatically across
all 37 instructions simultaneously.

### 6.5 Synthesis Results (Measured)

Measured with `yowasp-yosys` using `proc; opt; memory_map; opt; techmap; opt`
(generic cell library, no ABC technology mapping).  `memory_map` is required
to decompose any ROM that yosys infers from `always_comb case` constant
assignments before `techmap`, ensuring a fair apples-to-apples comparison.
Run `python synth_compare.py` to reproduce.

| Metric                | Constraint-based | Hand-coded case stmt | Ratio |
|-----------------------|-----------------|----------------------|-------|
| Total cells           | 80              | 168                  | 47%   |
| Wires                 | 96              | 104                  | 92%   |
| Wire bits             | 155             | 316                  | 49%   |
| Lines of RTL          | ~73             | ~120                 | 61%   |
| External tool (QM)    | None (built-in) | N/A                  | —     |
| MUX cells             | 0               | 37                   | —     |
| ROM inference         | None            | None (decomposed)    | —     |
| Product terms (SOP)   | 38 (GROW)       | 78 (QM)              | 49%   |

**Cell-type breakdown:**

| Type      | Constraint | Manual |
|-----------|-----------|--------|
| `$_AND_`  | 52        | 16     |
| `$_MUX_`  | 0         | 37     |
| `$_NOT_`  | 8         | 27     |
| `$_OR_`   | 20        | 88     |

**The constraint version uses 47% of the manual gate count — 53% fewer cells.**
This improvement comes from the **Cube GROW minimizer** exploiting the 94% don't-care
space in the RV32I encoding: 2048 total minterms, only 121 defined by instructions.

The GROW algorithm expands each instruction's cube into the don't-care space to find
maximal prime implicants that span multiple instruction groups.  A single PI can cover
all I-type instructions (for `is_load`, for example), where QM would produce one term
per instruction.

**Cell-type analysis:**

- **Constraint SOP**: 52 AND + 8 NOT + 20 OR (flat sum-of-products, no MUX).
  38 prime implicants, many shared across outputs via CSE.  The GROW algorithm finds
  PIs that cover multiple instructions per term, requiring far fewer AND/OR cells.

- **Manual case statement**: 16 AND + 37 MUX + 27 NOT + 88 OR (hierarchical).
  Yosys factors the opcode decode once and builds a MUX tree for the inner
  funct3/funct7b5 decode, reusing the opcode check across all instructions in
  the same opcode group.  Despite the hierarchical factoring, total cell count
  is 2× higher because don't-cares are not exploited.

The constraint approach's advantages:

1. **Area: 53% fewer gates** — Cube GROW exploits the 94% don't-care space that
   a hand-coded case statement cannot access.
2. **Extensibility** — adding an instruction is one new block; no existing code
   is touched.
3. **Correctness** — each block is independently readable and verifiable.
4. **Automation** — minimization is automatic; the engineer writes intent, not gates.
5. **No monolithic decision tree** — the synthesiser assembles the logic from
   independent, composable blocks.

> **The critical path is 2 LUT levels** because each output bit is a sum-of-products
> of the support bits: one AND level (product terms) and one OR level (sum).
> This is the theoretical minimum for any combinational decode function.  The manual
> case statement reaches the same depth after synthesis optimization, but at 2× the cell count.
> The GROW minimizer achieves this theoretical minimum while also minimizing gate count,
> something hand-coded RTL cannot do without manual cube analysis.

### 6.6 Compiler Input-Field Requirements

The `ConstraintCompiler` detects the synthesis **input field** by finding a
`zdc.input()`-annotated field (or, as a fallback, a non-rand field) that appears
in subscript conditions in the constraint guards.  Guard conditions must use
**bit-slice subscript notation** (`self.instr[6:0]`) so the compiler can extract
`BitRange` objects from the AST.

**Current limitation:** The compiler's `_parse_conditions()` method only handles
`subscript` AST nodes — it does not recognize field-equality conditions
(`self.opcode == X`) where `opcode` is a derived rand field.

**Decision (from review):** Extend `_parse_conditions()` to also accept
field-equality conditions when the field is bound to a bit-slice of the input by
a `c_extract_fields` constraint (Option B).  This is ~50 lines of compiler work
in `constraint_compiler.py` and will be broadly useful beyond this example.
The `zdc.input()` annotation on `instr` gives the compiler an unambiguous marker
for the primary input field, avoiding fragile "non-rand field detection" heuristics.

Until the extension is merged, the example can use bit-slice guards temporarily:

```python
# Synthesis-compatible interim form (bit-slice guards)
@constraint
def c_add(self):
    if self.instr[6:0] == 0b0110011 and self.instr[14:12] == 0b000 and self.instr[30] == 0:
        assert self.alu_op == ALU_ADD
        ...

# Target form after compiler extension (named derived fields)
@constraint
def c_add(self):
    if self.opcode == 0b0110011 and self.funct3 == 0b000 and self.funct7b5 == 0:
        assert self.alu_op == ALU_ADD
        ...
```

### 6.7 Formal Verification Hook

Because the constraint struct is the **specification**, formal equivalence checking
between the specification and the synthesized RTL is straightforward:

```
prove: for all instr[31:0],
    RTL_outputs == constraint_solve(instr)
```

This is not possible with a hand-written case statement as the specification,
because the case statement IS the implementation.

---

## 7. Extensibility: Adding RISC-V M Extension

The M extension adds 8 multiply/divide instructions (MUL, MULH, MULHSU, MULHU,
DIV, DIVU, REM, REMU).  All share `opcode == 0b0110011` (same as the base R-type
group) and are distinguished by `funct7 == 0b0000001`, so `funct7b0` (bit 25 of
`instr`) becomes the discriminator.

### 7.1 Inheritance as the Extension Mechanism

The M extension is implemented as a **subclass** of `RV32IDecode`:

```python
# rv32m_decode.py
from rv32i_decode import RV32IDecode

ALU_MUL  = 11;  ALU_MULH   = 12;  ALU_MULHSU = 13;  ALU_MULHU = 14
ALU_DIV  = 15;  ALU_DIVU   = 16;  ALU_REM    = 17;  ALU_REMU  = 18

@zdc.dataclass
class RV32MDecode(RV32IDecode):
    """RV32I + M extension decode — inherits all 37 base constraints."""

    # Additional discriminator: funct7[0] (bit 25 of instr)
    funct7b0: zdc.u1 = zdc.rand()   # instr[25]

    @constraint
    def c_extract_funct7b0(self):
        assert self.funct7b0 == ((self.instr >> 25) & 0x1)

    # 8 new instruction constraints — zero changes to base class:

    @constraint
    def c_mul(self):
        if self.opcode == 0b0110011 and self.funct3 == 0b000 and self.funct7b0 == 1:
            assert self.alu_op  == ALU_MUL
            assert self.use_rs1 == 1;  assert self.use_rs2 == 1;  assert self.use_rd == 1
            assert self.is_load == 0;  assert self.is_store == 0
            assert self.is_branch == 0

    @constraint
    def c_div(self):
        if self.opcode == 0b0110011 and self.funct3 == 0b100 and self.funct7b0 == 1:
            assert self.alu_op  == ALU_DIV
            assert self.use_rs1 == 1;  assert self.use_rs2 == 1;  assert self.use_rd == 1
            assert self.is_load == 0;  assert self.is_store == 0
            assert self.is_branch == 0

    # ... c_mulh, c_mulhsu, c_mulhu, c_divu, c_rem, c_remu (one block each)
```

**Size:** 8 instruction blocks × ~12 lines each = ~100 lines total for the
extension file.  The base `rv32i_decode.py` is not modified in any way.

### 7.2 Why Inheritance Works Here

Because each `@constraint` block is independent and scoped to a specific
encoding (guard conditions are mutually exclusive), subclass constraints simply
add new rows to the truth table — they cannot conflict with inherited rows as
long as no base instruction uses `funct7b0 == 1` with `opcode == 0b0110011`.
The `ConstraintCompiler.validate()` phase confirms mutual exclusion statically.

This is the **Open/Closed Principle** applied to hardware: the base decode is
closed to modification and open to extension via subclassing.

---

## 8. Example Structure

```
examples/04_constraints/
├── DESIGN.md              ← this document
├── rv32_core.py           ← RV32Core component stub with Fetch port
├── rv32i_decode.py        ← RV32IDecode action + all 37 base instruction constraints
├── rv32m_decode.py        ← RV32MDecode(RV32IDecode): 8 M-extension constraints
├── test_rv32i_decode.py   ← simulation tests (37 instructions, parametrized)
├── synth_compare.py       ← drives synthesis flow; generates comparison report
└── expected/
    ├── rv32i_decode.sv         ← synthesized RTL via ConstraintCompiler (synthesis flow)
    ├── rv32i_decode_manual.sv  ← hand-coded case statement (for comparison)
    └── synth_report.txt        ← generated comparison table (gates, LUTs, lines)
```

### 8.1 `rv32i_decode.py` Sketch

```python
"""Example 04 — Constraint-Based RV32I Instruction Decode.

Shows how @constraint blocks replace a monolithic if/else decode tree.
Each instruction is described independently; synthesis derives optimal RTL.
"""
import zuspec.dataclasses as zdc
from zuspec.dataclasses import constraint, rand

# ALU op codes (4 bits — 11 ops for RV32I base)
ALU_ADD, ALU_SUB, ALU_XOR, ALU_SLT, ALU_SLTU = 0, 1, 2, 3, 4
ALU_SRL, ALU_SRA, ALU_OR, ALU_AND, ALU_SLL   = 5, 6, 7, 8, 9
ALU_PASS                                       = 10  # forward immediate

# Immediate format codes (3 bits)
IMM_NONE, IMM_I, IMM_S, IMM_B, IMM_U, IMM_J = 0, 1, 2, 3, 4, 5


@zdc.dataclass
class RV32IDecode(zdc.Struct):
    # --- Primary input (set externally; hardware port) ---
    instr     : zdc.u32 = zdc.input()

    # --- Internal derived fields (bit-slices of instr) ---
    opcode    : zdc.u7  = zdc.rand()
    funct3    : zdc.u3  = zdc.rand()
    funct7b5  : zdc.u1  = zdc.rand()

    # --- Decode outputs ---
    alu_op    : zdc.u4 = zdc.rand()
    imm_sel   : zdc.u3 = zdc.rand()
    use_rs1   : zdc.u1 = zdc.rand()
    use_rs2   : zdc.u1 = zdc.rand()
    use_rd    : zdc.u1 = zdc.rand()
    is_load   : zdc.u1 = zdc.rand()
    is_store  : zdc.u1 = zdc.rand()
    is_branch : zdc.u1 = zdc.rand()
    is_jal    : zdc.u1 = zdc.rand()
    is_jalr   : zdc.u1 = zdc.rand()
    mem_width : zdc.u2 = zdc.rand()
    mem_signed: zdc.u1 = zdc.rand()

    # --- Field extraction (synthesizes to assign wires) ---
    @constraint
    def c_extract_fields(self):
        assert self.opcode   == (self.instr & 0x7F)
        assert self.funct3   == ((self.instr >> 12) & 0x7)
        assert self.funct7b5 == ((self.instr >> 30) & 0x1)

    # --- 37 instruction constraint blocks ---

    @constraint
    def c_add(self):
        if self.opcode == 0b0110011 and self.funct3 == 0b000 and self.funct7b5 == 0:
            assert self.alu_op == ALU_ADD
            assert self.imm_sel == IMM_NONE
            assert self.use_rs1 == 1; assert self.use_rs2 == 1; assert self.use_rd == 1
            assert self.is_load == 0; assert self.is_store == 0
            assert self.is_branch == 0; assert self.is_jal == 0; assert self.is_jalr == 0

    @constraint
    def c_sub(self):
        if self.opcode == 0b0110011 and self.funct3 == 0b000 and self.funct7b5 == 1:
            assert self.alu_op == ALU_SUB
            assert self.imm_sel == IMM_NONE
            assert self.use_rs1 == 1; assert self.use_rs2 == 1; assert self.use_rd == 1
            assert self.is_load == 0; assert self.is_store == 0
            assert self.is_branch == 0; assert self.is_jal == 0; assert self.is_jalr == 0

    # ... (one per instruction) ...
```

### 8.2 `rv32i_decode_tb.py` Sketch

```python
"""Testbench: constrain instr to each encoding, verify decode outputs."""

from zuspec.dataclasses import randomize_with
from rv32i_decode import RV32IDecode, ALU_ADD, ALU_SUB, IMM_NONE

ADD_ENCODING = 0b000000000000_00000_000_00000_0110011  # ADD x0, x0, x0

def test_add():
    d = RV32IDecode()
    with randomize_with(d):
        assert d.instr == ADD_ENCODING          # fix the instruction word
    # solver derives all decode outputs from the constraints
    assert d.alu_op   == ALU_ADD
    assert d.use_rs2  == 1
    assert d.is_load  == 0
    assert d.imm_sel  == IMM_NONE

# pytest parametrize across all 37 instructions
```

---

## 9. Key Messages for Readers

1. **Constraints are specifications, not implementations.**  
   `c_add` says *what ADD is*, not *how to detect ADD*.  The synthesizer derives the
   detection logic automatically.

2. **Global optimization for free.**  
   The synthesizer minimizes all 37 outputs simultaneously, sharing product terms
   across instructions.  A human can only do this exhaustively with a Karnaugh map
   for each output bit — 15+ Karnaugh maps for this design.

3. **Open/Closed Principle in hardware.**  
   The design is open to extension (add a constraint block) and closed to modification
   (existing blocks are untouched).  This is the same principle that makes software
   plugin architectures maintainable, applied to RTL.

4. **The spec IS the test.**  
   The constraint blocks used for synthesis are the same ones used to generate
   stimulus and check responses in the testbench.  There is no separate "golden
   model" to maintain.

---

## 10. Decisions

The following questions arose during design and were resolved in review.

1. **Synthesis target** — Use Yosys + generic target.  `ConstraintCompiler.emit_sv()`
   produces standard SV as the primary output; Yosys provides a secondary step for
   concrete cell counts and waveform-compatible netlists.

2. **Scope** — Full 37 RV32I base instructions.  Partial coverage weakens the
   optimization argument; a reader who knows RV32I will notice missing instructions.

3. **M extension** — Include as `rv32m_decode.py` using **inheritance**
   (`RV32MDecode(RV32IDecode)`).  This is the cleanest demonstration of the
   Open/Closed Principle: 8 new blocks, zero changes to the base file.
   See §7 for details and size estimate (~100 lines).

4. **Comparison RTL** — Include `expected/rv32i_decode_manual.sv` alongside
   the synthesized `rv32i_decode.sv`.  Readers can diff them directly.  The
   manual version should be "sensible but unoptimized" — a straightforward case
   statement without manual Karnaugh-map work — so the comparison is honest.

5. **Constraint mutual exclusion** — `ConstraintCompiler.validate()` is a
   **synthesis-time** check: it confirms that no two guard conditions can be
   simultaneously true for any input pattern, ensuring the truth table has no
   conflicting rows.  For simulation, mutual exclusion is enforced naturally by
   the RISC-V encoding spec itself — no legal instruction word satisfies two
   guards simultaneously.  No explicit `insn_type` selector field is needed.

6. **Guard syntax** — Adopt **Option B**: extend `ConstraintCompiler._parse_conditions()`
   to recognize field-equality conditions (`self.opcode == X`) when the field is
   bound to a bit-slice of the `zdc.input()` field by a `c_extract_fields`
   constraint.  The `zdc.input()` annotation (which already exists in the framework)
   gives the compiler an unambiguous marker for the primary input, replacing the
   fragile "find the non-rand field" heuristic.  This work belongs in
   `packages/zuspec-synth` and benefits all future examples.
