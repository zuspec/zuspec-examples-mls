
# Design: Action-Call Support in `@zdc.proc` Bodies

## Overview

`examples/02_action/action.py` uses an `Action` (`IncrCount`) invoked via
`await IncrCount()(self)` inside a `@zdc.proc` loop.  The functional intent is
identical to the direct register access in `examples/01_counter/counter.py`:

```
# 01_counter — direct
await self.count.write(self.count.read() + 1)

# 02_action — via action call
await IncrCount()(self)          # body() does the same write
```

For this to be fully supported, two subsystems must be updated: the **runtime
(rt)** and the **synthesis** path.

---

## Call Mechanics

`IncrCount()` can be constructed with no arguments because `zdc.field()` always
injects a `default=None`, so the inherited `comp: T = field()` from
`Action[Counter]` is optional at construction time.

`IncrCount()(self)` calls `Action.__call__(comp=<Counter instance>)`.  The
current implementation in `types.py` creates a full `ActivityRunner` context
(PoolResolver, ActionRegistry, ICLTable, StructuralSolver) and runs the
complete PSS lifecycle (pre_solve → randomize → post_solve → body/activity).

---

## Runtime (rt)

### Current State

`Action.__call__` is designed for standalone scenario execution (e.g., from
`ScenarioRunner`).  When called from inside a `@zdc.proc` coroutine it
rebuilds **all** of the infrastructure on every call:

```python
# types.py — Action.__call__  (current, executed every simulation cycle)
resolver          = PoolResolver.build(comp)        # walks component tree
registry          = ActionRegistry.build(comp)      # indexes action types
icl_table         = ICLTable.build(registry)        # Phase-E ICL inference
structural_solver = StructuralSolver(icl_table, …)  # structural solver
ctx = ActionContext(…)
await ActivityRunner()._traverse(type(self), [], ctx)
```

For the counter, this runs on every clock edge — an unbounded loop — so the
per-call allocation cost accumulates indefinitely.

Note: `ScenarioRunner` already demonstrates the correct pattern.  It builds
the same four objects **once** in `__init__` and reuses them across multiple
`await runner.run(…)` calls.  `Action.__call__` needs the same treatment.

### What Needs to Change

#### 1. `ActionInfra` — bundle the cacheable objects

Add a small container to hold the four infra objects that are derived purely
from the component tree structure (which does not change during simulation):

```python
# rt/action_infra.py  (new file)
@dataclasses.dataclass
class ActionInfra:
    resolver:          PoolResolver
    registry:          ActionRegistry
    icl_table:         ICLTable
    structural_solver: StructuralSolver
```

#### 2. `CompImplRT._action_infra` — lazy cache field

Add one optional field to `CompImplRT`:

```python
# rt/comp_impl_rt.py
_action_infra: Optional[ActionInfra] = dc.field(default=None)
```

#### 3. `Action.__call__` — build-once, reuse pattern

Replace the per-call infrastructure construction with a lazy lookup on the
component:

```python
async def __call__(self, comp, seed=None):
    from .rt.action_infra import ActionInfra, get_or_build_infra
    import random

    seed_val = seed if seed is not None else random.randrange(2**32)
    infra = get_or_build_infra(comp)          # cached on comp._impl

    ctx = ActionContext(
        action=None,
        comp=comp,
        pool_resolver=infra.resolver,
        seed=seed_val,
        structural_solver=infra.structural_solver,
    )
    traversed = await ActivityRunner()._traverse(type(self), [], ctx)
    # copy fields back onto self (existing logic)
    …
```

`get_or_build_infra` is a small helper:

```python
def get_or_build_infra(comp) -> ActionInfra:
    impl = getattr(comp, '_impl', None)
    if impl is not None and impl._action_infra is not None:
        return impl._action_infra
    # First call: build and cache
    resolver  = PoolResolver.build(comp)
    registry  = ActionRegistry.build(comp)
    icl_table = ICLTable.build(registry)
    solver    = StructuralSolver(icl_table, seed=0, registry=registry)
    infra = ActionInfra(resolver, registry, icl_table, solver)
    if impl is not None:
        impl._action_infra = infra
    return infra
```

The infra is built at most **once per component instance**, on the first
`await action(comp)` call, then reused for all subsequent calls on the same
component.  A fresh `ActionContext` (with a new seed) is created for each
call, so per-traversal state remains isolated.

**Acceptance criterion (rt):** Running the `action.py` example under the
existing simulation harness should produce the same per-cycle register increment
as the `counter.py` example, with the infra built exactly once per component.

---

## Synthesis

### Current State

`data_model_factory.py::_parse_action_call` recognizes a specific AST pattern:

```python
# Recognized form
await ActionCls(kw1=v1, kw2=v2)(comp=self.some_comp_field)
```

AST shape:
```
Await(
  Call(                              # outer: invocation
    func=Call(                       # inner: constructor
      func=Name("ActionCls"),
      keywords=[keyword(arg="kw1"), ...]
    ),
    keywords=[keyword(arg="comp", value=...)]   ← comp must be a keyword
  )
)
```

### What Needs to Change

#### 1. Extend `_parse_action_call` — positional `comp` argument

`await IncrCount()(self)` produces:

```
Await(
  Call(                              # outer
    func=Call(                       # inner
      func=Name("IncrCount"),
      args=[], keywords=[]
    ),
    args=[Name("self")],             # positional, not keyword
    keywords=[]
  )
)
```

`_parse_action_call` must also accept outer calls where `comp` is passed as the
first positional argument instead of as `comp=...`.  The detection logic should
be updated to return success when *either* condition holds:

- `outer_call.keywords` contains `keyword(arg="comp", ...)`  ← existing
- `outer_call.args` is a non-empty list (first arg is comp)  ← **new**

The caller (`_inline_action_call`) already receives the `outer_call` node and
can extract the comp expression from `outer_call.args[0]` when the keyword form
is absent.

#### 2. `_inline_action_call` — resolve comp from positional arg

When the positional form is used, the comp expression is
`outer_call.args[0]`.  Inside a `@zdc.proc` body, `self` refers to the
component itself, so the comp resolves to the component being synthesized.
`_inline_action_call` should convert this AST expression (via
`_convert_ast_expr`) and map it to the component reference just as the keyword
`comp=self` form does today.

No changes are needed downstream: once `_inline_action_call` resolves the comp
and extracts the action class, it calls `_convert_action_body`, which inlines
`IncrCount.body()` into the parent proc's IR.  The inlined stmts are:

```
await self.comp.count.write(self.comp.count.read() + 1)
```

which synthesis already knows how to lower.

#### 3. Expected synthesis output

After inlining, the synthesized RTL for `action.py` must be **identical** to
that of `counter.py`:

```systemverilog
module Counter(
    input               clock,
    input               reset,
    output reg[31:0]    count);
  always @(posedge clock or posedge reset) begin
      if (reset) count <= 0;
      else       count <= count + 1;
  end
endmodule
```

**Acceptance criterion (synthesis):** Running the synthesis pass on `action.py`
produces the same IR and emits the same (or equivalent) SystemVerilog as
`counter.py`.

---

## File Change Summary

| File | Change |
|------|--------|
| `zuspec-dataclasses/src/zuspec/dataclasses/rt/action_infra.py` | **New file.** `ActionInfra` dataclass bundling `PoolResolver`, `ActionRegistry`, `ICLTable`, `StructuralSolver`.  `get_or_build_infra(comp)` helper does the lazy build-and-cache. |
| `zuspec-dataclasses/src/zuspec/dataclasses/rt/comp_impl_rt.py` | Add `_action_infra: Optional[ActionInfra] = dc.field(default=None)` to `CompImplRT`. |
| `zuspec-dataclasses/src/zuspec/dataclasses/types.py` | Replace per-call infra construction in `Action.__call__` with `get_or_build_infra(comp)` lookup; create a fresh `ActionContext` per call reusing the cached infra. |
| `zuspec-dataclasses/src/zuspec/dataclasses/data_model_factory.py` | Extend `_parse_action_call` to recognize positional comp form (`await ActionCls()(self)`).  Update `_inline_action_call` to extract comp from `outer_call.args[0]` when keyword form is absent. |
| `examples/02_action/action.py` | No changes expected — this is the target spec. |

---

## Open Questions — Resolution

> Status: **RESOLVED** (implementation complete, all tests passing).

1. **`activity()` override** — The cached `StructuralSolver` carries mutable
   RNG state that advances on each traversal (consistent with how `ScenarioRunner`
   works).  This is correct: per-call randomness comes from `ActionContext.seed`
   (independent, per-call) plus the solver's advancing RNG state.

2. **Positional `comp` form in synthesis** — After investigation,
   `_parse_action_call` already accepts the positional form (`await ActionCls()(self)`)
   because it does not gate on the `comp=` keyword.  The `_inline_action_call`
   function also does not extract comp as an expression (it uses
   `comp_field_indices` from the outer scope).  **No changes were needed** in
   the synthesis path.

3. **Cache invalidation** — Documented in `rt/action_infra.py`.  Invalidation
   is performed by setting `comp._impl._action_infra = None` before the next
   call.  Not needed for current MLS examples.

## Implementation Status

| Task | Status |
|------|--------|
| `rt/action_infra.py` — `ActionInfra` + `get_or_build_infra` | ✅ Done |
| `rt/comp_impl_rt.py` — `_action_infra` field | ✅ Done |
| `types.py` — `Action.__call__` refactor | ✅ Done |
| `data_model_factory.py` — synthesis (no changes needed) | ✅ No-op |
| `tests/unit/test_rt_regression_action_call.py` — caching tests | ✅ Done |
| `tests/test_action_synth.py` — synthesis integration test | ✅ Done |
| `examples/02_action/action.py` — module docstring | ✅ Done |

