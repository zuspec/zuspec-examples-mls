# Phase 1 Implementation Plan
## Cube GROW Minimizer (no solver dependency)

**Goal:** Replace `MultiOutputQM.minimize_from_cube_sets()` with a Cube GROW
minimizer that exploits the explicit ON/OFF cube structure available from
constraint blocks.  Expected result: ~38 product terms vs 78 (QM), projecting
to ~114 cells vs 168 (manual baseline).

**No new dependencies.** Pure Python cube arithmetic.

---

## Files changed / created

| File | Action | Purpose |
|------|--------|---------|
| `packages/zuspec-synth/src/zuspec/synth/sprtl/cube_minimizer.py` | **Create** | `CubeExpandMinimizer` class |
| `packages/zuspec-synth/src/zuspec/synth/sprtl/constraint_compiler.py` | **Edit** | Populate OFF-cubes in `build_cubes()`; dispatch to `CubeExpandMinimizer` in `minimize()` |
| `packages/zuspec-synth/tests/test_cube_minimizer.py` | **Create** | Unit tests for `CubeExpandMinimizer` |
| `examples/04_constraints/expected/rv32i_decode.sv` | **Regenerate** | Fewer product terms in emitted SV |
| `examples/04_constraints/expected/synth_report.txt` | **Regenerate** | Updated cell counts |

---

## Step 1 — Extend `build_cubes()` to collect OFF-cubes

**File:** `constraint_compiler.py`  
**Function:** `build_cubes()` (starts ~line 566)

### What changes

Currently, when `(assigned_val >> b) & 1 == 0`, the cube is silently dropped.
We need to capture these as explicit OFF-cubes.

A block that explicitly assigns `use_rd = 0` under conditions `(mask, value)` 
means: "when this input pattern matches, `use_rd` must not be 1." That is an
OFF-set cube for `use_rd`.

Blocks that don't mention a field at all (`assigned_val is None`) remain DC —
they contribute nothing to the OFF-cube list.

### Concrete change

Add a parallel dict `off_cubes_by_bit` initialized the same way as
`cubes_by_bit`.  In the block distribution loop, after checking `(assigned_val
>> b) & 1` for ON:

```python
off_cubes_by_bit: Dict[str, List[Tuple[int, int]]] = {}
for fd in self.cset.output_fields:
    for b in range(fd.width):
        off_cubes_by_bit[bit_col(fd.name, b, fd.width)] = []
```

In the inner loop body (currently `if (assigned_val >> b) & 1:`):

```python
for b in range(fd.width):
    col = bit_col(fd.name, b, fd.width)
    if (assigned_val >> b) & 1:
        cubes_by_bit[col].append((cube_mask, cube_value))
    else:
        off_cubes_by_bit[col].append((cube_mask, cube_value))
```

Store as `self._off_cubes_by_bit = off_cubes_by_bit` at the end of the method.

### Docstring update

Add a note: "_off_cubes_by_bit[col] — cubes that explicitly drive this bit to 0
(used by CubeExpandMinimizer for don't-care exploitation)."

---

## Step 2 — Create `cube_minimizer.py`

**Full file path:**
`packages/zuspec-synth/src/zuspec/synth/sprtl/cube_minimizer.py`

### Module docstring

```
Cube GROW minimizer for constraint-derived logic functions.

Implements Espresso-EXPAND-style prime implicant generation using cube algebra
rather than minterm enumeration.  This allows exploiting the large don't-care
space of decode functions (94%+ for RV32I) without enumerating DC minterms.

No external solver dependency.  Pure Python cube arithmetic.
"""
```

### Imports

```python
from __future__ import annotations
from typing import Dict, List, Tuple
from ..ir.constraint_ir import SOPCube, SharedTerm
```

Reuse the helpers `_popcount` and `_mask_value_to_cube` — either import from
`qm_minimizer` or duplicate them.  Prefer importing: they are already correct.

```python
from .qm_minimizer import _mask_value_to_cube, _popcount, _cube_key
```

### Helper: `_disjoint(m1, v1, m2, v2) -> bool`

```python
def _disjoint(m1: int, v1: int, m2: int, v2: int) -> bool:
    """Return True iff two cubes share no minterm.

    Two cubes are disjoint iff there exists a bit constrained in both but
    to opposite values.  Bit formula: bool(m1 & m2 & (v1 ^ v2)).
    """
    return bool(m1 & m2 & (v1 ^ v2))
```

### Helper: `_subsumes(pi_m, pi_v, c_m, c_v) -> bool`

```python
def _subsumes(pi_m: int, pi_v: int, c_m: int, c_v: int) -> bool:
    """Return True iff cube PI covers all minterms of cube C.

    PI subsumes C iff every bit constrained in C is either don't-care in PI
    or matches PI's value.  Equivalent to: (c_m & pi_m & (pi_v ^ c_v)) == 0.
    """
    return (c_m & pi_m & (pi_v ^ c_v)) == 0
```

(This is the same relation as `QMMinimizer._cube_subsumes` — identical logic,
kept local to avoid tight coupling.)

### Core GROW function: `_grow(mask, value, off_list, n_vars)`

```python
def _grow(
    mask: int,
    value: int,
    off_list: List[Tuple[int, int]],
    n_vars: int,
) -> Tuple[int, int]:
    """Expand cube (mask, value) to its maximal prime implicant.

    Iterates over each constrained bit.  If dropping the bit makes the cube
    disjoint from every OFF-cube (i.e. the larger cube covers no OFF minterm),
    the bit is dropped permanently.

    Args:
        mask:     Constrained-bit mask (1 = bit is specified).
        value:    Bit values for constrained positions.
        off_list: OFF-set cubes (mask, value) for this output bit.
        n_vars:   Total support bits (for iteration bound).

    Returns:
        (new_mask, new_value) — the grown prime implicant.
    """
    for bit in range(n_vars):
        if not (mask >> bit) & 1:
            continue                      # already don't-care
        new_mask  = mask  & ~(1 << bit)
        new_value = value & ~(1 << bit)   # bit position becomes don't-care
        if all(_disjoint(new_mask, new_value, om, ov) for om, ov in off_list):
            mask, value = new_mask, new_value   # safe to drop
    return mask, value
```

**Order note:** Bits are tried from LSB to MSB (bit 0, 1, ..., n_vars-1).
For RV32I the support ordering is: opcode[6:0] first, then funct3[2:0], then
funct7[5].  Dropping opcode bits first (low-indexed) then funct3 bits will
tend to produce larger cubes.  This ordering is correct-by-construction; no
reordering is needed for Phase 1.

### `CubeExpandMinimizer` class

```python
class CubeExpandMinimizer:
    """Two-level SOP minimizer based on cube GROW (Espresso EXPAND).

    Unlike QM, this minimizer exploits the explicit OFF-set cubes available
    from constraint blocks.  Undefined input encodings are treated as genuine
    don't-cares without enumeration.

    Interface mirrors MultiOutputQM.minimize_from_cube_sets().
    """

    def minimize(
        self,
        on_cubes:  Dict[str, List[Tuple[int, int]]],
        off_cubes: Dict[str, List[Tuple[int, int]]],
        n_vars: int,
    ) -> Tuple[Dict[str, List[SOPCube]], List[SharedTerm]]:
        """Minimize all outputs and return (per_output_cubes, shared_terms).

        Args:
            on_cubes:  Per-output ON-set cube lists from build_cubes().
            off_cubes: Per-output OFF-set cube lists from build_cubes().
            n_vars:    Number of support bits.

        Returns:
            (per_output_cubes, shared_terms) — same shape as MultiOutputQM.
        """
        per_output: Dict[str, List[SOPCube]] = {}

        for name, on_list in on_cubes.items():
            off_list = off_cubes.get(name, [])
            per_output[name] = self._minimize_one(on_list, off_list, n_vars)

        shared_terms = self._cse(per_output)
        return per_output, shared_terms
```

#### `_minimize_one(on_list, off_list, n_vars)`

```python
    def _minimize_one(
        self,
        on_list:  List[Tuple[int, int]],
        off_list: List[Tuple[int, int]],
        n_vars:   int,
    ) -> List[SOPCube]:
        """Minimize one output bit: GROW → deduplicate → cover."""
        if not on_list:
            return []

        # Phase 1a: GROW each ON-cube to its maximal prime implicant.
        grown: List[Tuple[int, int]] = [
            _grow(m, v, off_list, n_vars) for m, v in on_list
        ]

        # Phase 1b: Deduplicate grown PIs.
        seen: set = set()
        unique_pis: List[Tuple[int, int]] = []
        for g in grown:
            if g not in seen:
                seen.add(g)
                unique_pis.append(g)

        # Phase 1c: Remove dominated PIs (PI_a dominated by PI_b iff PI_b
        # is a superset of PI_a's minterms, i.e. PI_b subsumes PI_a and is
        # strictly larger — fewer constrained bits).
        unique_pis = self._remove_dominated(unique_pis)

        # Phase 1d: Greedy essential-first cover.
        selected = self._greedy_cover(unique_pis, on_list)

        return [_mask_value_to_cube(m, v, n_vars) for m, v in selected]
```

#### `_remove_dominated(pis)`

```python
    @staticmethod
    def _remove_dominated(
        pis: List[Tuple[int, int]],
    ) -> List[Tuple[int, int]]:
        """Remove PIs dominated by (subsumed by) a larger PI in the list.

        PI_i is dominated iff there exists PI_j such that:
          - PI_j subsumes PI_i  (every minterm of PI_i is in PI_j)
          - PI_j is strictly larger (fewer constrained bits)
        """
        n = len(pis)
        dominated = [False] * n
        for i in range(n):
            if dominated[i]:
                continue
            mi, vi = pis[i]
            for j in range(n):
                if i == j or dominated[j]:
                    continue
                mj, vj = pis[j]
                # PI_j subsumes PI_i and is strictly larger (less specific).
                if _subsumes(mj, vj, mi, vi) and _popcount(mj) < _popcount(mi):
                    dominated[i] = True
                    break
        return [p for i, p in enumerate(pis) if not dominated[i]]
```

#### `_greedy_cover(pis, on_list)`

```python
    @staticmethod
    def _greedy_cover(
        pis:     List[Tuple[int, int]],
        on_list: List[Tuple[int, int]],
    ) -> List[Tuple[int, int]]:
        """Select a minimal cover of on_list using pis.

        Each original ON-cube must be subsumed by at least one selected PI.
        Uses essential-PI-first selection then greedy set-cover.

        Args:
            pis:     Prime implicant candidates (grown cubes).
            on_list: Original ON-set cubes that must be covered.

        Returns:
            Subset of pis forming the cover.
        """
        n = len(on_list)
        # Build PI → covered ON-cube indices mapping.
        pi_covers: List[List[int]] = []
        for pm, pv in pis:
            covers = [
                i for i, (om, ov) in enumerate(on_list)
                if _subsumes(pm, pv, om, ov)
            ]
            pi_covers.append(covers)

        uncovered = set(range(n))
        selected: List[Tuple[int, int]] = []

        # Essential PIs: sole cover for some ON-cube.
        cube_to_pis: Dict[int, List[int]] = {i: [] for i in range(n)}
        for pi_idx, covers in enumerate(pi_covers):
            for cube_idx in covers:
                cube_to_pis[cube_idx].append(pi_idx)

        essential: set = set()
        for cube_idx, pi_list in cube_to_pis.items():
            if len(pi_list) == 1:
                essential.add(pi_list[0])

        for pi_idx in essential:
            pm, pv = pis[pi_idx]
            selected.append((pm, pv))
            uncovered -= set(pi_covers[pi_idx])

        # Greedy remainder: pick PI with largest gain; fewest literals as
        # tiebreaker (fewer literals = wider cube = simpler gate).
        while uncovered:
            best_idx = -1
            best_gain = -1
            best_lits = 999

            for pi_idx, (pm, pv) in enumerate(pis):
                gain = len(set(pi_covers[pi_idx]) & uncovered)
                if gain == 0:
                    continue
                lits = _popcount(pm)
                if gain > best_gain or (gain == best_gain and lits < best_lits):
                    best_idx = pi_idx
                    best_gain = gain
                    best_lits = lits

            if best_idx == -1:
                break  # should not happen for a valid problem
            pm, pv = pis[best_idx]
            selected.append((pm, pv))
            uncovered -= set(pi_covers[best_idx])

        return selected
```

#### `_cse(per_output)` — common subexpression elimination

Identical logic to `MultiOutputQM._cse`; reuse the `_cube_key` helper.

```python
    @staticmethod
    def _cse(
        per_output: Dict[str, List[SOPCube]],
    ) -> List[SharedTerm]:
        """Find cubes appearing in ≥2 outputs and build SharedTerm list."""
        cube_to_outputs: Dict[tuple, List[str]] = {}
        for out_name, cubes in per_output.items():
            for cube in cubes:
                key = _cube_key(cube)
                cube_to_outputs.setdefault(key, [])
                if out_name not in cube_to_outputs[key]:
                    cube_to_outputs[key].append(out_name)

        shared: List[SharedTerm] = []
        for idx, (key, names) in enumerate(cube_to_outputs.items()):
            if len(names) >= 2:
                shared.append(SharedTerm(
                    wire_name=f"w_sh{idx}",
                    cube=SOPCube(literals=dict(key)),
                    used_by=list(names),
                ))
        return shared
```

---

## Step 3 — Update `minimize()` in `constraint_compiler.py`

**What changes:** add import, add dispatch branch.

### Import addition (top of file)

```python
from .cube_minimizer import CubeExpandMinimizer
```

### Updated `minimize()` method

Replace the existing `if hasattr(self, '_cubes_by_bit')` branch:

```python
def minimize(self) -> None:
    """Run SOP minimization and store results in cset."""
    n = self._n_vars or self.cset.support_size()

    if (hasattr(self, '_cubes_by_bit') and self._cubes_by_bit is not None
            and hasattr(self, '_off_cubes_by_bit')):
        # Preferred path: GROW minimizer with explicit OFF-cubes.
        per_output_cubes, shared_terms = CubeExpandMinimizer().minimize(
            self._cubes_by_bit,
            self._off_cubes_by_bit,
            n,
        )
    elif hasattr(self, '_cubes_by_bit') and self._cubes_by_bit is not None:
        # Fallback: cube-based QM (no OFF-set available).
        per_output_cubes, shared_terms = MultiOutputQM().minimize_from_cube_sets(
            self._cubes_by_bit, n
        )
    else:
        # Legacy: minterm-based path.
        assert hasattr(self, '_ones_by_bit'), "Call build_table() or build_cubes() first"
        outputs = {
            name: (ones, self._dontcares_by_bit[name])
            for name, ones in self._ones_by_bit.items()
        }
        per_output_cubes, shared_terms = MultiOutputQM().minimize(outputs, n)

    self.cset.sop_functions = [
        SOPFunction(output_name=name, cubes=cubes)
        for name, cubes in per_output_cubes.items()
    ]
    self.cset.shared_terms = shared_terms
```

Since `build_cubes()` now always sets `_off_cubes_by_bit`, any caller that
invokes the standard pipeline (`build_cubes()` then `minimize()`) will
automatically use the GROW path.  The legacy `build_table()` path is unchanged.

---

## Step 4 — Unit tests in `test_cube_minimizer.py`

**File:** `packages/zuspec-synth/tests/test_cube_minimizer.py`

### Test structure

All tests work directly with `CubeExpandMinimizer` using handcrafted
(mask, value) tuples.  No Zuspec dataclass setup required.

### Test 1: `test_grow_drops_irrelevant_bit`

**Setup:** 2-bit input (bits 0 and 1).  Output = 1 when input = 0b10.
ON-cube: `(0b11, 0b10)` — both bits constrained.  OFF-cube: `(0b11, 0b00)`
(input = 0b00 → output = 0).

**Expected:** Bit 1 (value = 1) can be dropped: enlarged cube `(0b01, 0b00)`
is NOT disjoint from OFF-cube `(0b11, 0b00)`.  Wait — let's work through it:

Actually the correct test setup is:
- ON-cube: `(0b11, 0b10)` = "bit1=1 AND bit0=0 → output=1"
- OFF-cube: `(0b11, 0b11)` = "bit1=1 AND bit0=1 → output=0"

Try dropping bit 0 from the ON-cube: new cube `(0b10, 0b10)` = "bit1=1 → output=1".
Check against OFF-cube: `m1=0b10, v1=0b10, m2=0b11, v2=0b11`.
`m1 & m2 & (v1 ^ v2) = 0b10 & 0b11 & (0b10 ^ 0b11) = 0b10 & 0b01 = 0`.
NOT disjoint → can't drop bit 0 safely.  Correct — dropping bit 0 would cover
the OFF-cube's minterms.

Try dropping bit 1: new cube `(0b01, 0b00)` = "bit0=0".
Check: `m1=0b01, v1=0b00, m2=0b11, v2=0b11`.
`m1 & m2 & (v1^v2) = 0b01 & 0b11 & (0b00^0b11) = 0b01 & 0b11 = 0b01 != 0`.
Disjoint — safe to drop bit 1.
Result: `(0b01, 0b00)` — only bit 0 = 0 constrained.  Correct.

**Assert:** `_grow(0b11, 0b10, [(0b11, 0b11)], 2) == (0b01, 0b00)`

### Test 2: `test_grow_no_off_cubes`

ON-cube: `(0b111, 0b101)` (3 bits constrained).  OFF-cube list: `[]`.

**Expected:** all bits can be dropped → result `(0, 0)` (tautology cube).

**Assert:** `_grow(0b111, 0b101, [], 3) == (0, 0)`

### Test 3: `test_minimize_one_output`

A 3-instruction toy decoder, 4-bit input (opcode[3:0]):

| Instruction | opcode | out_x |
|-------------|--------|-------|
| LOAD        | 0b0000 | 1     |
| STORE       | 0b0001 | 0     |
| ALU         | 0b0010 | 1     |

ON-cubes for `out_x`: `[(0b1111, 0b0000), (0b1111, 0b0010)]`
OFF-cubes for `out_x`: `[(0b1111, 0b0001)]`

**Expected after GROW:** 
- Cube `(0b1111, 0b0000)`: try dropping bit 1, 2, 3 — all safe since OFF-cube
  only constrains opcode=0b0001. Drop bits 1,2,3 → `(0b0001, 0b0000)`.
- Cube `(0b1111, 0b0010)`: OFF-cube constrains bit0=1 but this cube has bit0=0;
  they are already disjoint on bit0. Drop bits 2,3 → `(0b0011, 0b0010)`.

**Cover:** both PIs together cover both ON-cubes; 2 product terms.

**Assert:** `len(result) <= 2` and both ON-cubes are subsumed by a result cube.

### Test 4: `test_cse_across_outputs`

Two output bits with identical product terms in their covers.

ON-cubes for `out_a`: `[(0b11, 0b10)]` → single PI `(0b10, 0b10)` after GROW (only bit1=1)
ON-cubes for `out_b`: same.
OFF-cubes for both: `[(0b11, 0b00)]`.

**Expected:** `shared_terms` has exactly 1 entry used by both `out_a` and `out_b`.

### Test 5: `test_full_pipeline_grow_beats_qm`

Use a toy 3-instruction decoder (from `test_constraint_compiler.py` fixture
`_MinimalNamed`) and run the full pipeline.  Verify that:
- The minimize step produces valid SV (existing assertion)
- Product term count is ≤ what QM would produce (we can compute both)

### Test 6: `test_full_pipeline_rv32i`

Import `rv32i_decode.RV32IDecode` from `examples/04_constraints` (add to
sys.path).  Run the full pipeline.  Assert:
- Total product terms across all outputs ≤ 50 (currently 78 from QM)
- SV contains `assign` for each expected output

Note: this test may need `pytest.mark.slow` or to be in a separate integration
test file since it touches the example directory.

---

## Step 5 — Verify correctness against `build_table()` ground truth

The existing `build_table()` method produces a full minterm truth table which
can be used to verify the GROW cover is correct.

Add a helper function to `test_cube_minimizer.py`:

```python
def _verify_cover_correct(on_cubes, off_cubes, cover_cubes, n_vars):
    """Verify the cover agrees with the explicit ON/OFF sets on all minterms."""
    for m in range(1 << n_vars):
        covered = any(
            all((m >> b) & 1 == v for b, v in enumerate(...) if v is not None)
            ...
        )
        # Simplest: use SOPCube.covers()
        covered_by_result = any(cube.covers(m) for cube in cover_cubes)
        in_off = any(...)
        if in_off:
            assert not covered_by_result, f"Cover hits OFF minterm {m}"
```

Use this in `test_minimize_one_output` and the RV32I test.

---

## Step 6 — Regenerate expected files

After tests pass, run:

```bash
cd examples/04_constraints
python synth_compare.py
```

This will regenerate:
- `expected/rv32i_decode.sv` (fewer product terms in the wire assignments)
- `expected/synth_report.txt` (updated cell counts for constraint version)

Update `examples/04_constraints/DESIGN.md` §6.5 with the new numbers.
Update `examples/04_constraints/README.md` Teaching Point 2.

---

## Edge cases and gotchas

### 1. Empty OFF-cube list

When a bit has no explicit OFF-cubes (e.g., `is_store` where only STORE
instructions set it to 1, and no other block explicitly sets it to 0), `_grow`
will expand every ON-cube to the tautology `(0, 0)`.  This is correct: if no
OFF-cube exists, the bit's ON-set can span the entire input space.  The greedy
cover will then select the single tautology cube.

**HOWEVER:** This is only valid if the ON-cubes themselves don't conflict.
For decode functions, the constraint blocks are mutually exclusive (validated
by Phase C), so this is safe.

To be safe, verify: if the tautology cube `(0, 0)` is selected, it should
subsume ALL original ON-cubes (it will, trivially).

### 2. Bit-dropping order sensitivity

GROW is a greedy algorithm — the final prime implicant may depend on which
bit is dropped first.  Bits are dropped in order 0, 1, ..., n_vars-1.

For RV32I: support vector is `[opcode[6:0], funct3[2:0], funct7b5]` with
opcode bits at positions 0–6, funct3 at 7–9, funct7b5 at 10.  Dropping opcode
bits first (low indices) tends to be effective since opcode is the primary
decode field.

No multi-pass / best-of-N ordering is needed for Phase 1.

### 3. ON-cubes with identical (mask, value) after GROW

If two ON-cubes grow into the same prime implicant (as expected for
instructions sharing an opcode group), `seen` set in `_minimize_one` handles
deduplication.

### 4. Subsumption direction in `_remove_dominated`

PI_i is dominated (can be removed) iff there is a PI_j with MORE minterms
(FEWER constrained bits) that covers all of PI_i's minterms.

Formula: PI_j subsumes PI_i iff `_subsumes(mj, vj, mi, vi)` — "PI_j is a
superset of PI_i's minterms".  And PI_j must be strictly larger: `_popcount(mj)
< _popcount(mi)` (fewer constrained bits = larger cube).

Do NOT remove PI_i if PI_j subsumes PI_i but they are the same size — they
might be needed as alternatives for cover selection.

### 5. `_cube_key` import from `qm_minimizer`

`_cube_key` is currently a module-level function in `qm_minimizer.py` (not
prefixed with double underscore).  Importing it is safe.  If the import seems
overly coupled, duplicate the 2-line function — it's trivial.

---

## Expected test outcomes

| Test | Assertion |
|------|-----------|
| `test_grow_drops_irrelevant_bit` | Bit dropped, result = `(0b01, 0b00)` |
| `test_grow_no_off_cubes` | Tautology cube `(0, 0)` |
| `test_minimize_one_output` | ≤ 2 product terms, no OFF-minterm covered |
| `test_cse_across_outputs` | 1 shared term |
| `test_full_pipeline_grow_beats_qm` | PT count ≤ QM count, valid SV |
| `test_full_pipeline_rv32i` | ≤ 50 product terms total |

---

## Verification: run existing tests after changes

```bash
cd packages/zuspec-synth
python -m pytest tests/test_constraint_compiler.py -v   # must all pass unchanged
python -m pytest tests/test_cube_minimizer.py -v        # new tests must all pass
python -m pytest tests/ -v --ignore=tests/test_riscv_cores.py   # broad regression
```

No existing test should regress — the new minimizer is only invoked when
`_off_cubes_by_bit` is set, which only happens after `build_cubes()` (the
existing tests already use this path and will now get better results, which
must still satisfy their current assertions).
