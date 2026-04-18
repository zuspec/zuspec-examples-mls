
# Implementation, Test, and Documentation Plan
# Action-Call Support in `@zdc.proc` Bodies (examples/02_action)

See `design.md` for the problem statement and architecture decisions.

---

## Work Breakdown

### Phase 1 — RT: `ActionInfra` cache

**Goal:** Eliminate per-cycle reconstruction of `PoolResolver`, `ActionRegistry`,
`ICLTable`, and `StructuralSolver` in `Action.__call__`.

---

#### Task 1.1 — New file: `rt/action_infra.py`

Create
`zuspec-dataclasses/src/zuspec/dataclasses/rt/action_infra.py`.

Contents:

```python
"""ActionInfra — per-component cache of solver infrastructure.

Built once on the first ``await action(comp)`` call and stored on
``comp._impl._action_infra``.  Subsequent calls reuse the same objects;
only the per-traversal ``ActionContext`` (seed, inline_constraints, …) is
recreated each time.
"""
from __future__ import annotations

import dataclasses as dc
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .pool_resolver import PoolResolver
    from .action_registry import ActionRegistry
    from .icl_table import ICLTable
    from .structural_solver import StructuralSolver
    from ..types import Component


@dc.dataclass
class ActionInfra:
    """Cacheable solver infrastructure derived from a component tree.

    All four fields are stateless with respect to individual traversals;
    only the ``ActionContext`` carries per-call mutable state.
    """
    resolver: "PoolResolver"
    registry: "ActionRegistry"
    icl_table: "ICLTable"
    structural_solver: "StructuralSolver"


def get_or_build_infra(comp: "Component") -> ActionInfra:
    """Return the cached ``ActionInfra`` for *comp*, building it on first use.

    The infra is stored on ``comp._impl._action_infra``.  If *comp* has no
    ``_impl`` (e.g. a bare test double), a fresh ``ActionInfra`` is built and
    returned without caching.
    """
    from .pool_resolver import PoolResolver
    from .action_registry import ActionRegistry
    from .icl_table import ICLTable
    from .structural_solver import StructuralSolver

    impl = getattr(comp, '_impl', None)
    if impl is not None and impl._action_infra is not None:
        return impl._action_infra

    resolver  = PoolResolver.build(comp)
    registry  = ActionRegistry.build(comp)
    icl_table = ICLTable.build(registry)
    solver    = StructuralSolver(icl_table, seed=0, registry=registry)
    infra     = ActionInfra(resolver, registry, icl_table, solver)

    if impl is not None:
        impl._action_infra = infra
    return infra
```

---

#### Task 1.2 — Add `_action_infra` field to `CompImplRT`

File: `zuspec-dataclasses/src/zuspec/dataclasses/rt/comp_impl_rt.py`

Add after the existing `_proc_cycle_waiters` field:

```python
_action_infra: Optional["ActionInfra"] = dc.field(default=None)
```

Add the import guard at the top of the TYPE_CHECKING block:

```python
if TYPE_CHECKING:
    from .action_infra import ActionInfra
```

---

#### Task 1.3 — Refactor `Action.__call__` in `types.py`

File: `zuspec-dataclasses/src/zuspec/dataclasses/types.py`

Replace the body of `Action.__call__` with the build-once pattern:

```python
async def __call__(self, comp: Optional['Component'] = None,
                   seed: Optional[int] = None) -> Self:
    """Traverse this action against *comp* with full inference support.

    Infrastructure (PoolResolver, ActionRegistry, ICLTable, StructuralSolver)
    is built once per component instance and cached on ``comp._impl``.
    A fresh ActionContext (with a new seed) is created for each call so that
    per-traversal state remains isolated.
    """
    from .rt.activity_runner import ActivityRunner
    from .rt.action_context import ActionContext
    from .rt.action_infra import get_or_build_infra
    import dataclasses as dc
    import random

    seed_val = seed if seed is not None else random.randrange(2**32)
    infra = get_or_build_infra(comp)

    ctx = ActionContext(
        action=None,
        comp=comp,
        pool_resolver=infra.resolver,
        seed=seed_val,
        structural_solver=infra.structural_solver,
    )
    traversed = await ActivityRunner()._traverse(type(self), [], ctx)
    try:
        for f in dc.fields(traversed):
            object.__setattr__(self, f.name, getattr(traversed, f.name))
    except TypeError:
        pass
    return self
```

Imports that are no longer needed at the call site (`PoolResolver`,
`ActionRegistry`, `ICLTable`, `StructuralSolver`) should be removed from the
import block inside `__call__`.

---

### Phase 2 — Synthesis: positional `comp` in `_parse_action_call`

**Goal:** Teach `DataModelFactory._parse_action_call` to recognise
`await IncrCount()(self)` in addition to the existing keyword form
`await IncrCount()(comp=self)`.

---

#### Task 2.1 — Extend `_parse_action_call`

File: `zuspec-dataclasses/src/zuspec/dataclasses/data_model_factory.py`

The current guard (simplified):

```python
# outer_call must have keyword(arg='comp', ...)
if not any(kw.arg == 'comp' for kw in outer_call.keywords):
    return None
```

Replace with:

```python
has_comp_keyword  = any(kw.arg == 'comp' for kw in outer_call.keywords)
has_positional    = len(outer_call.args) >= 1
if not (has_comp_keyword or has_positional):
    return None
```

Return the same `(action_name, inner_call, outer_call)` triple — `outer_call`
already carries the positional args, so callers can inspect them.

---

#### Task 2.2 — Extract comp from positional arg in `_inline_action_call`

File: `zuspec-dataclasses/src/zuspec/dataclasses/data_model_factory.py`

In `_inline_action_call`, where the comp AST node is currently extracted from
the keyword:

```python
comp_kw = next((kw for kw in outer_call.keywords if kw.arg == 'comp'), None)
comp_ast = comp_kw.value if comp_kw else None
```

Extend to fall back to the first positional arg:

```python
comp_kw  = next((kw for kw in outer_call.keywords if kw.arg == 'comp'), None)
if comp_kw is not None:
    comp_ast = comp_kw.value
elif outer_call.args:
    comp_ast = outer_call.args[0]
else:
    comp_ast = None
```

No changes are needed in `_convert_action_body` or downstream: once
`_inline_action_call` resolves the action class and comp, the inlining path
is identical for both forms.

---

### Phase 3 — Tests

All new tests live alongside existing tests in the respective `tests/` trees.
Run the full test suites after each phase to catch regressions.

---

#### Task 3.1 — Unit test: `ActionInfra` caching (zuspec-dataclasses)

New file:
`zuspec-dataclasses/tests/unit/test_action_infra.py`

Tests:

| Test name | What it verifies |
|-----------|-----------------|
| `test_get_or_build_returns_action_infra` | `get_or_build_infra(comp)` returns an `ActionInfra` instance with all four fields populated. |
| `test_infra_cached_on_impl` | A second call to `get_or_build_infra(comp)` returns the **same** object (identity check `is`). |
| `test_infra_not_cached_without_impl` | A component-like object with no `_impl` still returns a valid `ActionInfra` (no crash). |
| `test_infra_independent_per_component` | Two distinct component instances each get their own `ActionInfra`. |

---

#### Task 3.2 — Unit test: `Action.__call__` uses cached infra

New file (or extend):
`zuspec-dataclasses/tests/unit/test_rt_regression_action_call.py`

Additional tests:

| Test name | What it verifies |
|-----------|-----------------|
| `test_action_call_caches_infra` | After `await action(comp)`, `comp._impl._action_infra is not None`. |
| `test_action_call_reuses_infra` | A second `await action(comp)` call on the same component uses the same `ActionInfra` instance (captured before/after). |
| `test_action_call_in_proc_loop` | A `@zdc.proc` containing `await IncrCount()(self)` runs N cycles and `count` increments by N.  This is the primary end-to-end rt test for example 02. |
| `test_action_call_fresh_seed_each_call` | Two successive calls produce independent random field values (seed is not reused verbatim — probabilistic check). |

The `test_action_call_in_proc_loop` test is the primary regression guard
for the full rt path:

```python
@zdc.dataclass
class Counter(zdc.Component):
    count: zdc.Reg[zdc.u32] = zdc.output()

    @zdc.proc
    async def _count(self):
        while True:
            await IncrCount()(self)

@zdc.dataclass
class IncrCount(zdc.Action[Counter]):
    async def body(self):
        await self.comp.count.write(self.comp.count.read() + 1)

def test_action_call_in_proc_loop():
    async def run():
        c = Counter()
        task = asyncio.create_task(c._impl._proc_processes[0][1].method(c))
        await c._impl.wait_cycles(5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return c.count.read()

    assert asyncio.run(run()) == 5
```

---

#### Task 3.3 — Unit test: `_parse_action_call` positional form (zuspec-dataclasses)

New file:
`zuspec-dataclasses/tests/unit/test_parse_action_call.py`

Tests exercising `DataModelFactory._parse_action_call` directly via the public
`DataModelFactory` class by parsing small snippets:

| Test name | What it verifies |
|-----------|-----------------|
| `test_parse_keyword_form_still_works` | `await ActionCls()(comp=self)` → returns non-None triple. |
| `test_parse_positional_form` | `await ActionCls()(self)` → returns non-None triple with `outer_call.args[0]` being a `Name("self")`. |
| `test_parse_rejects_bare_call` | `await ActionCls()` (no outer call) → returns `None`. |
| `test_parse_rejects_non_action_name` | `await 42()(self)` → returns `None`. |

---

#### Task 3.4 — Integration test: synthesis of `action.py` (zuspec-synth)

New file:
`zuspec-synth/tests/test_action_synth.py`

Mirror of `test_counter_synth.py` but using the `action.py` component
definition (defined inline or imported from `examples/02_action`).

Tests (all share a module-scoped `action_sv` fixture that synthesizes once):

| Test name | What it verifies |
|-----------|-----------------|
| `test_has_clock_reset_ports` | SV output contains `input clock` and `input reset`. |
| `test_has_count_output_reg` | SV output contains `output reg` and `count`. |
| `test_has_always_posedge` | SV output contains `always @(posedge clock or posedge reset)`. |
| `test_reset_clears_count` | SV output contains `count <= 0`. |
| `test_body_increments_count` | SV output contains `count <=` and `count + 1`. |
| `test_sv_equivalent_to_counter` | The synthesized SV for the action example is structurally equivalent to the counter example (diff on canonical form). |

The fixture:

```python
@pytest.fixture(scope="module")
def action_sv():
    import sys
    sys.path.insert(0, 'packages/zuspec-dataclasses/src')
    sys.path.insert(0, 'packages/zuspec-synth/src')

    import zuspec.dataclasses as zdc
    from zuspec.synth import synthesize

    @zdc.dataclass
    class Counter(zdc.Component):
        count: zdc.Reg[zdc.u32] = zdc.output()

        @zdc.proc
        async def _count(self):
            while True:
                await IncrCount()(self)

    @zdc.dataclass
    class IncrCount(zdc.Action[Counter]):
        async def body(self):
            await self.comp.count.write(self.comp.count.read() + 1)

    return synthesize(Counter)
```

---

#### Task 3.5 — Regression: existing tests must still pass

Run existing test suites after each phase change:

```
# zuspec-dataclasses
cd packages/zuspec-dataclasses
python -m pytest tests/ -x -q

# zuspec-synth
cd packages/zuspec-synth
python -m pytest tests/ -x -q
```

Specifically watch:
- `test_rt_regression_action_call.py` — all existing tests unchanged
- `test_counter_synth.py` — keyword form still synthesizes correctly
- `test_action.py` — `test_action()` still passes (uses `await MyA()(top)` form)

---

### Phase 4 — Documentation

---

#### Task 4.1 — Module docstring: `rt/action_infra.py`

Already included in the file template above.  Ensure it covers:
- Purpose of caching
- Lifetime (per-component-instance, not per-call)
- How to manually invalidate (set `comp._impl._action_infra = None`)

---

#### Task 4.2 — Update `Action.__call__` docstring in `types.py`

The revised docstring (see Task 1.3 template) must explain:
- That infrastructure is built once and cached
- That each call still gets a fresh `ActionContext` with an independent seed
- That the full PSS lifecycle (pre_solve → randomize → post_solve → body/activity)
  is executed on every call

---

#### Task 4.3 — Example file: `examples/02_action/action.py`

No code changes.  Add a module-level docstring to `action.py` explaining:
- What the example demonstrates (action factoring equivalent to counter)
- The call form `await IncrCount()(self)` and what each part means
- That synthesis produces identical RTL to `examples/01_counter/counter.py`

---

#### Task 4.4 — Update `examples/02_action/design.md`

After implementation, mark open questions as resolved:
- Confirm cache invalidation path (set `_action_infra = None`).
- Confirm `activity()` override is handled by the existing `_exec_action_body`
  branch (no extra work needed).

---

## Dependency Order

```
Task 1.1  →  Task 1.2  →  Task 1.3   (Phase 1 — must complete before Phase 2 testing)
Task 2.1  →  Task 2.2                 (Phase 2 — independent of Phase 1)
Task 3.1  →  depends on 1.1/1.2
Task 3.2  →  depends on 1.3
Task 3.3  →  depends on 2.1
Task 3.4  →  depends on 2.1 + 2.2
Task 3.5  →  run after every task
Task 4.x  →  can be done in parallel with Phase 1 / 2, finalize after 3.5
```

---

## Acceptance Criteria Summary

| # | Criterion |
|---|-----------|
| RT-1 | `await IncrCount()(self)` inside a `@zdc.proc` loop increments `count` by 1 per cycle over N cycles. |
| RT-2 | `comp._impl._action_infra` is populated after the first `await action(comp)` call and is identical (same object) on subsequent calls. |
| RT-3 | All pre-existing `test_rt_regression_action_call.py` tests continue to pass. |
| RT-4 | `test_action()` in `test_action.py` continues to pass. |
| SYN-1 | `DataModelFactory` built on the `action.py` component produces a proc body IR identical to the one produced for `counter.py`. |
| SYN-2 | `synthesize(Counter)` (action form) produces SV containing `always @(posedge clock or posedge reset)`, `count <= 0`, and `count + 1`. |
| SYN-3 | `test_counter_synth.py` continues to pass unchanged. |
