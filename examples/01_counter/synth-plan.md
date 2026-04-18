# Synthesis Plan: counter.py → Synthesizable Verilog

## Goal

Synthesize `counter.py` to the following Verilog module:

```verilog
module Counter(
    input clock,
    input reset,
    output reg[31:0] count);

  always @(posedge clock or posedge reset) begin
      if (reset) begin
        count <= 0;
      end else begin
        count <= count + 1;
      end
  end

endmodule
```

## Source

```python
@zdc.dataclass
class Counter(zdc.Component):
    count: zdc.Reg[zdc.b32] = zdc.output()

    @zdc.proc
    async def _count(self):
        while True:
            await self.count.write(self.count.read()+1)
```

---

## Current State (as-is)

Running `zuspec.synth.synthesize(Counter)` today produces:

```verilog
module Counter(
  output logic count
);
  always_comb begin
    count = '0;
  end
endmodule
```

This is wrong in every dimension: wrong port list, wrong logic style, wrong process kind.

### Root-cause trace

**Symptom 1 — `@zdc.proc` is not wired to the synthesis path.**

`@zdc.proc` wraps the method as `ExecProc`, which `DataModelFactory` stores in
`comp.functions` as a `Process` node. The synthesizer's `_synthesize_sprtl()` only
inspects `comp.sync_processes`. Finding none, it falls through to the
pure-comb skeleton. The `Process` IR node is never visited by the synthesizer.

**Symptom 2 — `Reg[b32]` type information is lost in the IR.**

`DataModelFactory._annotation_to_datatype()` does not handle `Reg[T]`
(a generic alias). It falls back to `DataTypeRef(ref_name='Reg')`, discarding
the element type `b32`. Consequently:
- The field has `kind=FieldKind.Field` (not output) and `direction=None`
- The field has no bit-width information
- The field has no reset value

**Symptom 3 — `self.count.read()` / `await self.count.write(expr)` are not lowered.**

The IR correctly captures these as:
```
ExprAwait → ExprCall(ExprAttribute(ExprRefField[0], 'write'),
              ExprBin(ExprCall(ExprAttribute(ExprRefField[0], 'read')), +, 1))
```
But neither the SPRTL transformer (`SPRTLTransformer`) nor the simple-sync
emitter (`_synthesize_simple_sync`) knows how to lower:
- `ExprAttribute(..., 'read')` + `ExprCall` on a `Reg` field → signal read
- `ExprAwait(ExprCall(ExprAttribute(..., 'write'), val))` → non-blocking assign
  `<= val` in an `always_ff`

**Symptom 4 — No default clock/reset domain.**

`Counter` declares no explicit clock, reset, or `ClockDomain`. `design.md` says
the process "binds to the default clock and reset domains", but neither
`DataModelFactory` nor the synthesizer injects implicit `clock`/`reset` ports
when none are declared.

---

## Required Changes

### A. `zuspec-dataclasses` — IR and DataModelFactory

#### A1. Resolve `Reg[T]` subscript in `_annotation_to_datatype`

File: `src/zuspec/dataclasses/data_model_factory.py`

`get_origin(ann) is Reg` (or its runtime alias) must be detected. Extract the
type argument `T` via `get_args(ann)[0]` and produce an IR node that carries:
- element type / bit-width (from `T`)
- a marker that this field is a `Reg` (synthesizable register with read/write API)

Options:
1. Add `DataTypeReg(element_type: DataType)` to `ir/data_type.py` and produce
   that node instead of `DataTypeRef`.
2. Re-use `DataTypeInt` directly (the register is just a fixed-width integer
   storage) and tag the field with `is_reg=True` in `Field`.

**Recommendation:** option 2 is simpler and sufficient for this case. Annotate
`Field` with `is_reg: bool = False`. When annotation is `Reg[T]`, resolve T to
a `DataTypeInt`, set `field.is_reg = True`, and propagate reset_value=0.

#### A2. Fix `output()` metadata on `Reg` fields

When a field has `Reg[T]` type + `zdc.output()` default_factory, the factory
must:
- Set `field.direction = SignalDirection.OUTPUT`
- Set `field.kind = FieldKind.Field` (it is an internal reg that is output)
- Set `field.reset_value = 0` (default; override if `output(reset=N)` supplied)

#### A3. Mark `@process` methods as synthesis candidates

File: `src/zuspec/dataclasses/data_model_factory.py`

When building a `Process` node from `ExecProc`, inspect the AST body:
- If the body is `while True: <only await stmts>` on Reg fields, tag the
  `Process` metadata with `"is_synthesizable_reg_process": True`.
- Alternatively, always tag `Process` nodes with `"is_process": True` (already
  done for parameterized ones) and let the synthesizer decide.

No structural IR change is strictly required here; the synthesizer can inspect
the process body itself.

#### A4. Default clock/reset domain injection

File: `src/zuspec/dataclasses/data_model_factory.py` or
`src/zuspec/dataclasses/domain.py`

When `DataTypeComponent.clock_domain is None` and the component has `Process`
nodes whose bodies contain `await reg.write(...)`, add an implicit default
`ClockDomain` with:
- clock signal name: `"clock"`
- reset signal name: `"reset"`
- reset style: async active-high (to match the expected output)

This can be deferred to the synthesizer if preferred — see B4.

---

### B. `zuspec-synth` — Synthesis path

#### B1. Route `@process` nodes through the synthesizer

File: `src/zuspec/synth/__init__.py`, function `_synthesize_sprtl`

After checking `sync_processes`, check `comp.functions` for `Process` nodes
whose body is the `while True / await reg.write` pattern:

```python
proc_nodes = [f for f in component_ir.functions
              if type(f).__name__ == 'Process']
```

For each such node, treat it as a single-cycle sync process and hand it to a
new lowerer (see B2).

#### B2. `Reg.read()` / `Reg.write()` lowering in code generation

A new pass or extension to the existing simple-sync emitter must recognize:

| IR Pattern | SV Output |
|---|---|
| `ExprCall(ExprAttribute(ExprRefField[i], 'read'), [])` | `<signal_name>` |
| `ExprAwait(ExprCall(ExprAttribute(ExprRefField[i], 'write'), [val]))` | `<signal_name> <= <val>;` |

These lowerings are applied inside an `always @(posedge clock or posedge reset)`
block. The `await` on `write()` marks the clock-edge synchronisation boundary
(non-blocking assignment timing).

This can be implemented as an extension to `_ir_expr_to_sv` /
`_ir_stmts_to_sv` in `zuspec/synth/__init__.py`, or as a dedicated
`RegAccessLowerer` pass before SV emission.

#### B3. Simple `always_ff` emitter for reg-process pattern

The target output does not use an FSM; it is a simple one-block `always_ff`.
The existing `_synthesize_simple_sync` or a new `_synthesize_reg_process`
function must:

1. Emit module header with `input clock`, `input reset`, and `output reg[N:0]`
   ports for each `Reg[T]` output field.
2. Emit a single `always @(posedge clock or posedge reset)` block.
3. Inside: `if (reset) begin ... <= 0; end else begin ... <= <body>; end`
4. The `<body>` comes from lowering the while-loop body (B2).

Note: use Verilog-2005 style (`reg`, `always`, non-blocking `<=`) for Yosys
compatibility per existing `mls.py` comments.

#### B4. Inject default clock/reset ports

File: `src/zuspec/synth/__init__.py`

When synthesizing a component whose IR has no explicit clock/reset signals
(i.e., no `ClockDomain`, no `ExecSync` clock/reset exprs), inject:
- `input clock` port
- `input reset` port (async active-high)

as the implicit default. This handles the `@process` + default-domain case
from `design.md`.

---

## Work Breakdown

### Phase 1 — IR Fixes (`zuspec-dataclasses`)

| # | Task | File(s) |
|---|------|---------|
| 1.1 | Add `is_reg` flag to `Field` IR node | `ir/fields.py` |
| 1.2 | Detect `Reg[T]` subscript in `_annotation_to_datatype`, extract width, set `is_reg`, `direction`, `reset_value` | `data_model_factory.py` |
| 1.3 | Write unit test: `DataModelFactory().build(Counter)` → field `count` has correct width, direction, is_reg | `tests/` |

### Phase 2 — Synthesis path for `@process` (`zuspec-synth`)

| # | Task | File(s) |
|---|------|---------|
| 2.1 | Detect `Process` nodes in `_synthesize_sprtl` alongside `sync_processes` | `synth/__init__.py` |
| 2.2 | Inject default `clock`/`reset` ports when no domain is declared | `synth/__init__.py` |
| 2.3 | Implement `Reg.read()` expression lowering (`_ir_expr_to_sv` extension) | `synth/__init__.py` or new `reg_lower.py` |
| 2.4 | Implement `await Reg.write(val)` statement lowering → `<=` non-blocking assign | same |
| 2.5 | Implement `_synthesize_reg_process`: emit `always @(posedge clock or posedge reset)` block with reset/body sections | `synth/__init__.py` |
| 2.6 | Write end-to-end test: `synthesize(Counter)` produces correct SV | `tests/` |

### Phase 3 — Verification (optional but recommended)

| # | Task |
|---|------|
| 3.1 | Run generated Verilog through Yosys (`read_verilog; hierarchy; synth`) to confirm it is synthesizable |
| 3.2 | Add iverilog simulation smoke-test: check count increments on posedge clock, resets to 0 |

---

---

## Runtime Support (Python Simulation)

The following analysis covers what is needed to *run* the Counter at the Python
simulation level (as opposed to synthesizing it). The target usage is:

```python
async with zdc.simulate(Counter) as c:
    await c.wait(zdc.cycles(10))
    print(c.count)  # should be 10
```

### RT Root Causes

**RT-1: `Reg[T]` standalone field on Component is not recognized by `mkComponent`.**

`obj_factory.py / mkComponent()` iterates `dc.fields(cls)`. When it sees
`default_factory is Output` it marks the field as a *signal* and attaches a
`SignalDescriptor`, giving it an integer (0) value. It does **not** check
whether the annotation is `Reg[T]`. Result: `c.count` is `0` (plain `int`),
not a `RegRT` or any register-like object.

The check order in `mkComponent()` (lines 212–284) must be extended:
before testing `is Output`, test `get_origin(field_type) is Reg`. If true,
create a `RegProcRT` (see RT-3) instance instead of a `SignalDescriptor`.

**RT-2: `RegRT.read()` is `async` but `Reg.read()` is sync.**

`Reg[T]`'s type stub declares `read()` as a sync method (returns `T`
directly). Counter uses it synchronously: `self.count.read() + 1`.

`RegRT` in `regfile_rt.py` defines `async def read()` (because it is designed
for register-file bus access where reads may be pipelined). For standalone
`Reg[T]` fields on a `Component` the read must be *synchronous* so that
`val = self.count.read()` works without `await`.

The fix is a new `RegProcRT` class (see RT-3) whose `read()` is synchronous.

**RT-3: `await reg.write(val)` must advance one clock cycle.**

In hardware the `await self.count.write(val)` boundary is the clock edge.
In simulation each call to `write()` inside a `@process` loop must:
1. Apply the new value (non-blocking — effective next cycle).
2. Advance the component's timebase by exactly one "cycle" so that external
   observers (`await comp.wait(zdc.cycles(10))`) can drain the correct number
   of iterations.

This requires a new `RegProcRT` class that holds a reference to the
component's `CompImplRT._timebase`. Its `write()` method calls
`timebase.advance()` (or waits on the next scheduled clock event) after
writing the value.

**RT-4: No default clock / timebase for `@process`.**

Counter has no explicit `clock` input port and no `ClockDomain` field. The
`@process` coroutine runs as a free `asyncio.create_task`. Today `write()` on
a plain `RegRT` does **not** touch the timebase, so the process loop spins
without advancing simulation time. External `await comp.wait(zdc.cycles(N))`
would hang forever.

Fix: when a component has `@process` methods and `Reg[T]` fields (no explicit
clock), `mkComponent` (or `CompImplRT.__init_eval__`) must create an implicit
internal timebase (if one doesn't already exist) and wire it into every
`RegProcRT` for that component.

**RT-5: `start_processes` correctly invokes `@process` — but fails because `count` is `int`.**

Confirmed by inspection: `c._impl._processes` already contains
`('_count', ExecProc(...))` and `start_all_processes` calls
`asyncio.create_task(proc.method(comp))`. The first `await
self.count.write(...)` call immediately raises `AttributeError: 'int' object
has no attribute 'write'`. Once RT-1/RT-3 are fixed this task will run
correctly.

---

### Required RT Changes

#### RT-A: New `RegProcRT` class

File: `src/zuspec/dataclasses/rt/regfile_rt.py` (or new `reg_proc_rt.py`)

```python
class RegProcRT(Generic[T]):
    _value: int          # current (committed) value
    _next: int | None    # pending write (None = no pending write)
    _width: int
    _timebase: ...       # reference to CompImplRT timebase

    def read(self) -> T:
        return self._value      # sync — no await

    async def write(self, val: T) -> None:
        self._next = val & mask  # schedule
        # advance one clock cycle (latch _next → _value, tick timebase)
        await self._timebase.tick_cycle()
        self._value = self._next
        self._next = None
```

The `timebase.tick_cycle()` method fires one clock-edge event and resumes the
external `wait(cycles=N)` counter.

#### RT-B: `mkComponent` detection of standalone `Reg[T]` fields

File: `src/zuspec/dataclasses/rt/obj_factory.py`, `mkComponent()`, ~line 212

Add **before** the `is Output`/`is Input` test:

```python
from typing import get_origin
from ..types import Reg
if get_origin(field_type) is Reg:
    # standalone Reg[T] field on Component
    width = <extract from T>
    reset_val = f.metadata.get('reset', 0)
    # Register for post-construction injection of timebase
    reg_proc_fields.append((f.name, width, reset_val))
    fields.append((f.name, object, dc.field(default=None)))
    continue
```

After the `cls_rt` dataclass is created, replace the `None` placeholders with
`RegProcRT` instances (similar to how `__init_regfile_fields__` works for
`RegFile`).

#### RT-C: Implicit default clock / `tick_cycle()` in `CompImplRT`

File: `src/zuspec/dataclasses/rt/comp_impl_rt.py`

Add:
- A `_default_cycle_event: asyncio.Event` attribute on `CompImplRT`.
- `async def tick_cycle(self)` — sets the event, yields to asyncio, clears
  it, and increments a cycle counter. Any `await comp.wait(cycles=N)` drains
  N ticks.
- `async def wait_cycles(self, n)` — waits for N `tick_cycle()` calls (for
  external observers).

This does **not** require an external clock driver; `RegProcRT.write()` is
the clock generator for Components with no explicit clock.

#### RT-D: `CompImplRT.__comp_build__` — inject timebase into `RegProcRT` fields

After construction of the `cls_rt` instance, iterate fields detected in RT-B
and call `regprocrt_inst._timebase = comp._impl` (or a timebase wrapper).

---

### RT Work Breakdown

| # | Task | File(s) |
|---|------|---------|
| R1 | Add `RegProcRT` with sync `read()` and clock-advancing async `write()` | `rt/regfile_rt.py` or `rt/reg_proc_rt.py` |
| R2 | Add `tick_cycle()` / `wait_cycles()` to `CompImplRT` | `rt/comp_impl_rt.py` |
| R3 | Detect `Reg[T]` in `mkComponent`, create `RegProcRT` placeholder field | `rt/obj_factory.py` |
| R4 | `__comp_build__` — inject `CompImplRT` reference into `RegProcRT` fields | `rt/obj_factory.py` |
| R5 | Write runtime smoke test: instantiate Counter, run 5 cycles, assert count==5 | `tests/` |

---

## Key Design Decisions

1. **`Reg[T]` maps to a plain register output port** — not a separate sub-module.
   `Reg` is a synthesizable storage element; `zdc.output()` means it appears on
   the module boundary as an output port. The read/write API is lowered away.

2. **`await reg.write(val)` defines the clock edge** — it does NOT imply a
   multi-cycle wait or FSM state. In the single-loop body pattern there is
   exactly one `await` per iteration, which maps directly to "this assignment
   is latched on the next posedge clock."

3. **Verilog-2005 output** — use `reg`, `always`, `<=` (not SystemVerilog
   `logic`/`always_ff`) for maximum Yosys compatibility, consistent with the
   existing `mls.py` code comments.

4. **No FSM for this pattern** — the `while True: await reg.write(...)` pattern
   is a degenerate single-state FSM. The `SPRTLTransformer` / FSM path is not
   needed here. Route this pattern to the simple always_ff emitter.
