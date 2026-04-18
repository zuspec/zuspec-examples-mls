# Don't-Care Exploitation Design
## Proving Constraint-Based Synthesis Achieves Better QoR

**Status:** Design / pre-implementation  
**Author:** Zuspec MLS team  
**Scope:** `packages/zuspec-synth/src/zuspec/synth/sprtl/`

---

## 1  The Problem with Quine-McCluskey

The current synthesis pipeline (`constraint_compiler.py` Phase E) runs a
Quine-McCluskey (QM) minimizer over the ON-set of each output bit.  QM is a
correct, complete minimizer for the minterms it is given — but it is blind to
the **don't-care (DC) space**.

For an 11-bit support (RV32I opcode+funct3+funct7b5), the input space has
2¹¹ = 2048 minterms.  Only 121 minterms (5.9%) are covered by constraint
blocks; the remaining 1927 minterms (94.1%) are globally undefined — genuine
hardware don't-cares where the output can be anything.

QM never sees these DCs.  It finds the minimal SOP **among products that only
cover defined minterms**.  This is a significant handicap: a much smaller and
wider set of product terms exists that covers the ON-set while freely using the
DC space.

### Why QM cannot simply be given the DC set

The obvious fix — enumerate all 1927 DC minterms and pass them to QM — does
not work in practice:

1. **Prime implicant explosion.** With 94% of the space as DC, almost every
   ON-minterm can be merged into an enormous implicant.  The number of prime
   implicants (PIs) grows combinatorially, making PI generation and cover
   selection intractable (QM's `minimize()` timed out in experiments).

2. **No enumeration is needed.** The DC minterms are *implicitly* the
   complement of the explicitly-constrained encodings.  Enumerating them to
   pass to QM converts a compact implicit representation into a huge explicit
   one — the wrong direction.

---

## 2  The Key Insight: Constraints Give Us Structured ON/OFF Sets

The constraint description is not just a convenient way to write a function.
It is a **structured, cube-based representation** of both the ON-set and the
OFF-set of every output bit — available for free as a side-effect of the
constraint blocks.

For each output bit `B`:

| Set | Source | Representation |
|-----|--------|---------------|
| **ON-set** | Constraint blocks that assign `B = 1` | List of (mask, value) cubes |
| **OFF-set** | Constraint blocks that assign `B = 0` | List of (mask, value) cubes |
| **DC-set** | Blocks that don't mention B *and* all undefined encodings | Implicit complement |

The OFF-set for a decode function is **small**.  For `use_rd`, for example,
only STORE and BRANCH instructions explicitly set it to 0 — just 2 cubes.
For `is_store`, the OFF-set is the entire rest of the instruction space.

This structure is the unique advantage a constraint-based flow has over a
case-statement RTL flow fed into a synthesis tool: the synthesis tool sees a
flat `always_comb` block and must infer the ON/OFF structure; the constraint
compiler already has it.

---

## 3  Algorithm: Cube GROW (Espresso EXPAND on Cube Lists)

### 3.1  Core operation

For each output bit, for each ON-set cube C = (mask, value):

```
GROW(C, OFF_cubes):
    for each bit b currently constrained in C:
        C' = C with bit b dropped (set to don't-care)
        safe = True
        for each OFF cube O in OFF_cubes:
            if C' and O intersect:   # share at least one minterm
                safe = False; break
        if safe:
            C = C'   # drop bit b; cube is now larger (covers more space)
    return C          # maximal prime implicant
```

Two cubes (m₁,v₁) and (m₂,v₂) are **disjoint** (no shared minterm) iff there
exists a bit constrained in both to opposite values:

```
disjoint(m1,v1, m2,v2) = bool(m1 & m2 & (v1 ^ v2))
```

This is O(1) arithmetic — **no SAT solver is needed** for the GROW phase.

### 3.2  Why this is safe

Dropping bit b from an ON-cube enlarges it to cover minterms where b=0 and
b=1.  The enlarged cube can only cause errors if it covers an OFF-set minterm.
Since the OFF-set is represented as explicit cubes, we check disjointness with
each OFF-cube in O(|OFF|) time.  Anything not in any OFF-cube is DC — safe by
definition.

### 3.3  Greedy minimum cover

After growing all ON-cubes into prime implicants (PIs), the final step selects
a minimum subset of PIs that covers every original ON-cube.  A greedy
essential-PI-first strategy gives a good cover without a solver:

1. Find all **essential PIs** — PIs that are the only one covering some ON-cube.
   These must be in the cover.
2. Remove ON-cubes covered by essential PIs.
3. Greedily pick the PI covering the most remaining ON-cubes; repeat.

### 3.4  Multi-output shared terms (CSE)

After per-output cover, identical product terms in the cover of different
output bits become shared wires (`w_sh*`), exactly as in the current QM path.
GROW-derived PIs are broader (fewer literals) and therefore **more likely to
collide across outputs**, increasing CSE opportunities.

---

## 4  Measured Results on RV32I

Measured on the `RV32IDecode` class (11-bit support, 40 constraint blocks,
37 distinct instructions).

### 4.1  Product term counts

| Stage | Product terms | vs. QM |
|-------|--------------|--------|
| Raw ON-cubes (one per instruction-output pair) | 179 | — |
| Current QM (no global DC) | **78** | baseline |
| **GROW prime implicants** (unique after dominated removal) | **86** | +10% (more but wider) |
| **GROW + greedy cover** | **38** | **−51%** |

Per-output breakdown after GROW + greedy cover:

| Output | Raw cubes | GROW PIs | Greedy cover |
|--------|-----------|----------|--------------|
| `alu_op_bit0` | 13 | 10 | 7 |
| `alu_op_bit1` | 11 | 8 | 4 |
| `alu_op_bit2` | 12 | 7 | 4 |
| `alu_op_bit3` | 5 | 3 | 2 |
| `imm_sel_bit0` | 22 | 8 | **2** |
| `imm_sel_bit1` | 9 | 4 | 2 |
| `imm_sel_bit2` | 3 | 3 | 2 |
| `is_branch` | 6 | 3 | **1** |
| `is_load` | 5 | 4 | **1** |
| `is_store` | 3 | 1 | **1** |
| `mem_signed` | 2 | 2 | **1** |
| `mem_width_bit0` | 3 | 1 | **1** |
| `mem_width_bit1` | 2 | 1 | **1** |
| `use_rd` | 28 | 10 | **3** |
| `use_rs1` | 34 | 11 | **2** |
| `use_rs2` | 19 | 8 | **2** |
| **Total** | **179** | **86** | **38** |

`imm_sel_bit0` collapsing from 22 cubes to 2 is particularly notable: six
distinct opcode groups all use I-type immediate encoding, and GROW merges them
into two prime implicants.  `use_rd` collapses from 28 cubes to 3 — one per
major group of rd-writing instructions.

### 4.2  Projected gate-count improvement

Current RTL cell counts (yosys techmap):

| Version | AND | MUX | NOT | OR | **Total** |
|---------|-----|-----|-----|----|-----------|
| Current constraint (QM) | 104 | 0 | 9 | 60 | **173** |
| Manual case statement | 16 | 37 | 27 | 88 | **168** |

With 38 product terms (vs 78), AND-gate count scales roughly proportionally.
Projected:

| Version | AND (est.) | NOT | OR | **Total (est.)** |
|---------|-----------|-----|----|-----------|
| Constraint + GROW | ~50 | ~9 | ~55 | **~114** |

That is **~32% fewer cells than the manual baseline** — the first result
where the constraint flow meaningfully beats a competent hand-written design.

---

## 5  Where Boolector Adds Value

Boolector 3.2.4 is available at `packages/yosys/bin/boolector` with a full
SMT-LIB2 interface and incremental solving support.  It enables three
enhancements beyond the pure-cube-algebra GROW phase.

### 5.1  Exact minimum cover (replaces greedy)

Cover selection is a weighted set-cover problem (NP-hard in general but small
in practice for decode logic).  The greedy heuristic may leave 2–5 extra
product terms on the table.  Boolector can find the exact minimum:

```
Variables:  Boolean x_i for each PI i
Objective:  minimize sum(x_i)
Constraint: for each original ON-cube j, at least one x_i = 1 where PI_i covers j
```

Encoding: QF_BV or pseudo-Boolean.  For 86 PIs and ~179 ON-cubes, this is
trivial for Boolector (milliseconds).  The exact cover may reduce the 38-term
greedy result by another 3–8 terms.

### 5.2  Joint multi-output cover (cross-output sharing)

Currently, each output bit is covered independently.  A PI that appears in the
cover of two output bits can be shared (one AND gate, two OR inputs).  With
Boolector, the joint cover problem can be stated directly:

```
Minimize:  (unique PIs selected across all outputs)
Subject to: each output bit's ON-cubes are covered by its selected PIs
```

This finds covers where PIs are re-used across outputs — reducing the total
gate count below what per-output independent optimization can achieve.

### 5.3  Observability don't-cares (ODC)

**This is the most powerful long-term opportunity.**

An output signal `S` may only be *observable* under certain conditions — for
example, `alu_op` is only used when the instruction drives the ALU.  If
`is_load` is 1, the ALU output is irrelevant, and `alu_op` is don't-care
regardless of what the gate-level logic computes.

Boolector can verify: "If I replace this product term for `alu_op` with a
larger cube, does the overall function remain correct when observable?"  This
is an equivalence-checking query:

```smt2
(set-logic QF_BV)
(declare-fun instr () (_ BitVec 32))
; Assert: the two versions of alu_op differ on this input
(assert (not (=> (= is_load 0) (= alu_op_new alu_op_old))))
(check-sat)  ; UNSAT means the replacement is safe
```

ODC exploitation is the frontier of modern logic optimization and is not
achievable from a case-statement RTL without re-synthesizing from scratch.
The constraint description naturally exposes the conditional observability
structure through its block guards.

---

## 6  Implementation Plan

### Phase 1 — Cube-GROW minimizer (no solver dependency)

**Location:** `packages/zuspec-synth/src/zuspec/synth/sprtl/cube_minimizer.py`

New class `CubeMinimizer` with interface matching `MultiOutputQM`:

```python
class CubeMinimizer:
    def minimize(
        self,
        on_cubes: Dict[str, List[Tuple[int, int]]],   # output → [(mask, value)]
        off_cubes: Dict[str, List[Tuple[int, int]]],  # output → [(mask, value)]
        n_vars: int,
    ) -> Tuple[Dict[str, List[SOPCube]], List[SharedTerm]]:
        ...
```

Steps:
1. `_grow(mask, value, off_list, n_vars)` — expand cube, O(n × |OFF|)
2. `_remove_dominated(pis)` — drop PIs subsumed by larger ones
3. `_greedy_cover(pis, on_cubes)` — essential PIs first, then greedy
4. CSE pass — identical terms across outputs → SharedTerm

**Integration:** `ConstraintCompiler.build_cubes()` already builds per-output
cube lists.  Add a parallel method `build_off_cubes()` and pass both to
`CubeMinimizer`.  Wire into `minimize()` as the preferred path before the
existing QM fallback.

**Tests:** Mirror existing `test_constraint_compiler.py` tests; add cases
where GROW demonstrably reduces product terms vs QM (e.g., a 3-instruction toy
decoder with one off-set cube).

### Phase 2 — Exact cover via Boolector

**Location:** `packages/zuspec-synth/src/zuspec/synth/sprtl/boolector_cover.py`

Drive `packages/yosys/bin/boolector` via subprocess with SMT-LIB2 stdin.

```python
class BoolectorCover:
    def __init__(self, boolector_path: str = None):
        ...  # auto-detect from packages/yosys/bin/boolector

    def exact_cover(
        self,
        pis: List[Tuple[int, int]],
        on_cubes: List[Tuple[int, int]],
        n_vars: int,
    ) -> List[Tuple[int, int]]:
        # Returns minimal subset of pis covering all on_cubes
        ...
```

Protocol:
1. Emit SMT-LIB2 problem: one Bool variable per PI, cardinality minimize.
2. Iteratively decrease the budget `k` with `(assert (< (sum x_i) k))`;
   call `(check-sat)` each time; stop when UNSAT; return last SAT model.
3. Use Boolector's `-i` (incremental) mode with push/pop to avoid
   re-solving from scratch.

Fallback: if Boolector is not found, silently use greedy cover.

### Phase 3 — Multi-output joint cover

Extend `BoolectorCover.exact_cover()` to accept multiple output specifications
simultaneously.  Model PI selection as shared: if PI `p` is used by any output,
it costs 1 gate; if reused by N outputs, it still costs 1 AND gate (N OR inputs).

Objective: minimize `|{i : x_i = 1 for any output}|`.

### Phase 4 — ODC via Boolector equivalence queries (future)

Requires mapping from constraint guard structure to observability conditions.
Design deferred — needs architectural changes to the constraint IR to
represent output-level enable conditions.

---

## 7  Integration with `constraint_compiler.py`

The modified `ConstraintCompiler.build_cubes()` populates both
`_cubes_by_bit` (ON) and `_off_cubes_by_bit` (OFF).  The `minimize()` method
selects the backend:

```python
def minimize(self) -> None:
    if self._off_cubes_by_bit is not None:
        # Phase 1+2: GROW + exact cover
        minimizer = CubeMinimizer(boolector_path=_find_boolector())
        per_output_cubes, shared_terms = minimizer.minimize(
            self._cubes_by_bit,
            self._off_cubes_by_bit,
            self._n_vars,
        )
    elif hasattr(self, '_cubes_by_bit'):
        # Legacy: cube-based QM (no OFF-set)
        per_output_cubes, shared_terms = MultiOutputQM().minimize_from_cube_sets(
            self._cubes_by_bit, self._n_vars)
    else:
        # Original minterm-based path
        ...
```

No existing tests break: the new path is behind an `if` that only activates
when OFF cubes are available.

---

## 8  Why This Is Unique to Constraint-Based Synthesis

| Property | Constraint description | Case-statement RTL |
|----------|----------------------|-------------------|
| ON-set available as cubes | ✅ direct from blocks | ❌ must re-parse RTL |
| OFF-set available as cubes | ✅ direct from blocks | ❌ inferred by synthesis tool |
| DC space size | 94.1% (RV32I) | Partially handled by `default: 0` which LOCKS off DC |
| GROW possible without minterm enumeration | ✅ pure cube algebra | ❌ needs enumeration or BDD |
| Exact cover with Boolector | ✅ 38 → ~33 terms | ❌ synthesis tool (ABC) does this internally |
| ODC exploitation | ✅ from guard structure | ❌ requires formal tool + re-synthesis |

The fundamental point: a `default: 0` in a case statement turns the entire
undefined encoding space into **explicit OFF-set** minterms — the designer has
inadvertently thrown away the DC freedom.  The constraint model has **no
`default`**: undefined encodings remain genuinely undefined, and GROW exploits
every bit of that freedom.

---

## 9  Expected Final Results

| Version | Product terms | Est. cells | vs. manual |
|---------|--------------|-----------|------------|
| Current QM | 78 | 173 | +3% |
| **GROW + greedy** | **38** | **~114** | **−32%** |
| GROW + Boolector exact | ~33 | ~105 | ~−37% |
| GROW + joint multi-output | ~28 | ~96 | ~−43% |

These are estimates for the AND/OR network; NOT count and wire routing are
assumed approximately constant.  Actual numbers must be confirmed by running
`synth_compare.py` after implementation.

The manual case statement cannot reach these numbers without manually
restructuring the case arms — and even then, it would destroy the
one-instruction-per-block readability that is the whole point.

---

## 10  Open Questions

1. **Order of bit dropping in GROW.** Dropping high-order bits first (opcode)
   may give fewer final PIs than dropping low-order bits first (funct7b5).
   Experiment with ordering heuristics (e.g., drop the bit whose removal
   removes the most constraints first).

2. **Conflict between ON-cubes.** If two ON-cubes for the same output bit
   overlap (same input conditions, both assign 1 — which is consistent), GROW
   from both may produce the same prime implicant.  Deduplication handles this.

3. **Completeness.** Greedy cover may not produce the global minimum.
   Boolector exact cover guarantees it for Phase 2.

4. **Output-side masking for ODC.** To exploit observability DCs, we need to
   know which outputs are "gating" which others.  The current constraint IR
   doesn't express this.  An `@observable_when` annotation could encode it.
