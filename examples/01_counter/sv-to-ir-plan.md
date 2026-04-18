# SV-to-IR Implementation Plan

Implements the mapping described in `sv-to-ir-design.md`: parse `counter_c.svh`
and `counter.sv` using `zuspec-fe-sv` and produce the same `DataTypeComponent`
IR that the Python `Counter` class produces.

**Baseline**: 59/59 tests passing in `zuspec-fe-sv`.

---

## Phase 1 — StmtMapper: `forever` loop

**File**: `packages/zuspec-fe-sv/src/zuspec/fe/sv/stmt_mapper.py`

### Change

Add `StatementKind.ForeverLoop` case to `map_statement()`:

```python
elif stmt_kind == 'StatementKind.ForeverLoop':
    body_stmts = self.map_statements(sv_stmt.body)
    return StmtWhile(test=ExprConstant(value=True), body=body_stmts)
```

Import `ExprConstant` at the top.

### Tests

New file: `tests/unit/test_stmt_mapper.py` (additions to existing file)

| Test | What it checks |
|---|---|
| `test_map_forever_loop` | `forever begin x = x+1; end` → `StmtWhile(ExprConstant(True), [StmtAssign(...)])` |
| `test_map_forever_nested_if` | `forever begin if (x) y=1; end` → `StmtWhile(..., [StmtIf(...)])` |

---

## Phase 2 — ExprMapper: field context + method calls on fields

**File**: `packages/zuspec-fe-sv/src/zuspec/fe/sv/expr_mapper.py`

### Changes

**2a. Field context injection**

Add `set_field_context(field_map: dict)` method and `_field_context` dict:
```python
def set_field_context(self, field_map: dict) -> None:
    self._field_context = field_map  # name → field_idx
```

Import `ExprRefField` from the IR.

**2b. `_map_named_value` — field vs local**

When `sv_expr.symbol.kind == ClassProperty` and name is in `_field_context`:
```python
return ExprRefField(field_idx=self._field_context[name])
```

Otherwise fall back to existing `ExprRefLocal(name=name)`.

**2c. `_map_call` — method on field + task wrapping**

Detect that the receiver is a `ClassProperty` named value, build
`ExprAttribute(ExprRefField(idx), method_name)` as `func`, map remaining
children as args, and wrap in `ExprAwait` if `subroutineKind == Task`:

```python
def _map_method_call(self, sv_expr):
    sub = sv_expr.subroutine
    method_name = str(sub.name)
    is_task = 'Task' in str(sub.subroutineKind)

    # Collect children: first ClassProperty NamedValue = receiver, rest = args
    children = []
    sv_expr.visit(lambda s: children.append(s) or True)
    # children[0] is sv_expr itself; skip it
    receiver_sym = None
    arg_nodes = []
    for child in children[1:]:
        ck = str(child.kind) if hasattr(child, 'kind') else ''
        if receiver_sym is None and ck == 'ExpressionKind.NamedValue':
            if str(child.symbol.kind) == 'SymbolKind.ClassProperty':
                receiver_sym = child
                continue
        arg_nodes.append(child)

    receiver = self._map_named_value(receiver_sym) if receiver_sym else ExprRefLocal('?')
    args = [self.map_expression(a) for a in arg_nodes if hasattr(a, 'kind') and 'Expression' in str(a.kind)]

    call = ExprCall(func=ExprAttribute(value=receiver, attr=method_name), args=args, keywords=[])
    return ExprAwait(expr=call) if is_task else call
```

### Tests

New file: `tests/unit/test_expr_mapper_fields.py`

| Test | What it checks |
|---|---|
| `test_named_value_becomes_ref_field` | With field context `{'count': 0}`, `count` NamedValue → `ExprRefField(0)` |
| `test_named_value_local_without_context` | Local variable → `ExprRefLocal` (no regression) |
| `test_task_call_wrapped_in_await` | `count.write(val)` → `ExprAwait(ExprCall(ExprAttribute(ExprRefField(0), 'write'), [val]))` |
| `test_function_call_not_awaited` | `count.read()` → `ExprCall(ExprAttribute(ExprRefField(0), 'read'), [])` (no `ExprAwait`) |
| `test_nested_call_in_binary_op` | `count.read() + 1` → `ExprBin(ExprCall(...), Add, ExprConstant(1))` |

---

## Phase 3 — TypeMapper: `zsp_reg_c` width extraction

**File**: `packages/zuspec-fe-sv/src/zuspec/fe/sv/type_mapper.py`

### Change

Add `map_zsp_reg_width(prop_sym) -> tuple[int, bool]`:
```python
def map_zsp_reg_width(self, prop_sym) -> tuple:
    """Extract (bits, signed) from a zsp_reg_c #(T) ClassProperty."""
    val = prop_sym.type.find('val')
    if val is None:
        return (32, False)
    t = val.type
    bits = t.bitWidth if hasattr(t, 'bitWidth') else 32
    signed = t.isSigned if hasattr(t, 'isSigned') else False
    return (int(bits), bool(signed))
```

Do NOT route `zsp_reg_c` fields through `map_builtin_type()` — the `reg`
template parameter would trigger the 4-state error.

### Tests

Additions to `tests/unit/test_type_mapper.py`

| Test | What it checks |
|---|---|
| `test_zsp_reg_width_int` | `zsp_reg_c #(int)` → `(32, True)` |
| `test_zsp_reg_width_bit32` | `zsp_reg_c #(bit[31:0])` → `(32, False)` |
| `test_zsp_reg_width_bit8` | `zsp_reg_c #(bit[7:0])` → `(8, False)` |
| `test_zsp_reg_width_default` | Malformed specialization → `(32, False)` (safe default) |

---

## Phase 4 — ClassMapper: `zsp_component` → `DataTypeComponent`

**File**: `packages/zuspec-fe-sv/src/zuspec/fe/sv/class_mapper.py`

### Changes

**4a. Helper predicates**

```python
def _is_zsp_component_subclass(self, class_sym) -> bool:
    bc = getattr(class_sym, 'baseClass', None)
    while bc is not None:
        if str(bc.name) in ('zsp_component', 'zsp_component_root'):
            return True
        bc = getattr(bc, 'baseClass', None)
    return False

def _is_zsp_reg_field(self, prop_sym) -> bool:
    gc = getattr(prop_sym.type, 'genericClass', None)
    return gc is not None and str(gc.name) == 'zsp_reg_c'
```

**4b. `map_component()` method**

New method that produces `DataTypeComponent`:
- Calls `_is_zsp_reg_field()` for each property
- For `zsp_reg_c` properties: calls `type_mapper.map_zsp_reg_width()` → `Field(is_reg=True, is_output_port=True, reset_value=0, datatype=DataTypeInt(...))`
- Skips known library-generated members: `create`, `randomize`, `pre_randomize`, `post_randomize`, `get_randstate`, `set_randstate`, `srandom`, `rand_mode`, `constraint_mode`
- Skips known library fields: `m_parent`, `m_children`, `clock_d`, `m_clock_domains`
- Builds `field_index_map = {name: idx}` and calls `expr_mapper.set_field_context(field_index_map)`
- Routes `task run()` to `proc_processes` as `Process(name='run', body=...)`
- Returns `DataTypeComponent`

**4c. `map_class()` update**

At the start of `map_class()`, check `_is_zsp_component_subclass()` and
delegate to `map_component()` if true.

### Tests

New file: `tests/unit/test_component_mapper.py`

| Test | What it checks |
|---|---|
| `test_zsp_component_subclass_detected` | `class foo extends zsp_component` → `DataTypeComponent`, not `DataTypeClass` |
| `test_zsp_reg_field_mapped` | `zsp_reg_c #(int) count` → `Field(is_reg=True, bits=32)` |
| `test_library_members_skipped` | `m_parent`, `clock_d`, `create`, `randomize` not in `fields` or `functions` |
| `test_run_task_is_proc` | `task run()` → `proc_processes`, not `functions` |
| `test_run_task_body_stmts` | `proc_processes[0].body` has the mapped statements |
| `test_non_run_task_is_function` | `task do_thing()` → `functions` |
| `test_plain_field_still_works` | `int count` (no zsp_reg_c) → plain `Field(is_reg=False)` |
| `test_field_index_map_passed_to_expr` | `count.read()` inside `run()` resolves to `ExprRefField(0)` |

---

## Phase 5 — FunctionMapper: field context threading

**File**: `packages/zuspec-fe-sv/src/zuspec/fe/sv/function_mapper.py`

### Changes

The `FunctionMapper` already calls `stmt_mapper.map_statements(sv_func.body)`.
The `StmtMapper` already calls `expr_mapper.map_expression(...)`. Since
`ExprMapper.set_field_context()` is called before mapping the class, the
field context is already live when the function body is mapped.

No structural changes needed — the threading is implicit through the shared
`expr_mapper` instance. Verify this is the case in the existing architecture.

**Minor addition**: `map_function()` should forward `is_async=True` for tasks
(`subroutineKind == Task`) so callers can distinguish tasks from functions
when classifying into `proc_processes` vs `functions`.

This is already done (`is_async = 'Task' in kind_str`). Confirm it's correct.

### Tests

Additions to `tests/unit/test_function_mapper.py`

| Test | What it checks |
|---|---|
| `test_task_is_async_true` | `task run()` → `Function.is_async == True` |
| `test_function_is_async_false` | `function int read()` → `Function.is_async == False` |
| `test_task_body_uses_field_context` | After `set_field_context({'x': 0})`, `x` in task body → `ExprRefField(0)` |

---

## Phase 6 — ModuleMapper (new file)

**File**: `packages/zuspec-fe-sv/src/zuspec/fe/sv/module_mapper.py`

### Purpose

Walks a slang module `InstanceBody` symbol and annotates an already-mapped
`DataTypeComponent` with port and domain information.

### Implementation

```python
class ModuleMapper:
    def annotate(self, module_body, component: DataTypeComponent) -> None:
        port_names = set()
        output_port_names = set()

        for sym in module_body.members:
            if str(sym.kind) == 'SymbolKind.Port':
                port_names.add(str(sym.name))
                if 'Out' in str(sym.direction):
                    output_port_names.add(str(sym.name))

        for field in component.fields:
            if field.name in output_port_names:
                field.is_output_port = True

        if 'clock' in port_names:
            component.clock_domain = 'default'   # sentinel; synthesizer handles implicit
        if 'reset' in port_names:
            component.reset_domain = 'default'
```

The synthesizer already handles `None` clock/reset by emitting implicit
`clock`/`reset` ports. Setting `'default'` vs `None` is a minor annotation;
confirm behaviour with synthesizer.

### Tests

New file: `tests/unit/test_module_mapper.py`

| Test | What it checks |
|---|---|
| `test_output_port_annotates_field` | `output int count` → `field.is_output_port = True` |
| `test_input_port_not_output` | `input clock` port → no field annotation |
| `test_clock_port_sets_domain` | `input clock` present → `component.clock_domain` set |
| `test_reset_port_sets_domain` | `input reset` present → `component.reset_domain` set |
| `test_unmatched_port_ignored` | Port with no matching field → no error, field unchanged |

---

## Phase 7 — SVMapper orchestration update

**File**: `packages/zuspec-fe-sv/src/zuspec/fe/sv/mapper.py`

### Changes

**7a. Two-phase collection**

Replace the single class-visitor loop with two phases:
1. Collect `ClassType` symbols → run `map_component()` or `map_class()` as appropriate
2. Collect `Instance`/`InstanceBody` symbols for modules → run `ModuleMapper.annotate()`

**7b. `get_components()` method**

```python
def get_components(self) -> List[DataTypeComponent]:
    return [c for c in self.classes if isinstance(c, DataTypeComponent)]
```

**7c. Include-path support in config**

`SVToZuspecConfig` needs `include_dirs: List[str]` so that `counter_pkg.sv`'s
`` `include "counter_c.svh" `` resolves correctly:

```python
# In SVToZuspecConfig:
include_dirs: List[str] = field(default_factory=list)
```

Pass include dirs to the slang compilation:
```python
# In SVParser.parse_files():
for d in self.config.include_dirs:
    self.compilation.addSystemDirectoryInclude(d)  # or addIncludeDirectory()
```

Check the exact pyslang API for include-path injection.

**7d. Filter built-in zsp library classes**

`zsp_component`, `zsp_reg_c`, `zsp_component_root`, etc. appear in the
class list when the library headers are compiled. Skip classes whose names
start with `zsp_` unless they're the user's subclass (determined by checking
`genericClass` and `baseClass` chains).

Simpler approach: only map classes that are **direct subclasses** of known
`zsp_*` base types, not the library types themselves.

### Tests

Additions to `tests/unit/test_integration.py`

| Test | What it checks |
|---|---|
| `test_get_components_empty` | Plain classes → `get_components()` returns `[]` |
| `test_get_components_returns_component` | `zsp_component` subclass → in `get_components()` |
| `test_map_text_zsp_component` | Map inline SV with `zsp_component` base → `DataTypeComponent` |
| `test_include_dirs_config` | `SVToZuspecConfig(include_dirs=[...])` accepted without error |

---

## Phase 8 — End-to-end Counter test

**File**: `packages/zuspec-fe-sv/tests/unit/test_counter_sv_to_ir.py` (new)

This test uses the **actual example files** from `examples/01_counter/`.

### Structure

```python
import os
import pytest
EXAMPLES_DIR = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', '..', '..', 'examples', '01_counter'
)

@pytest.fixture
def counter_component():
    from zuspec.fe.sv.mapper import SVMapper
    from zuspec.fe.sv.config import SVToZuspecConfig
    config = SVToZuspecConfig(include_dirs=[EXAMPLES_DIR])
    mapper = SVMapper(config=config)
    mapper.map_files([
        os.path.join(EXAMPLES_DIR, 'counter_pkg.sv'),
        os.path.join(EXAMPLES_DIR, 'counter.sv'),
    ])
    comps = mapper.get_components()
    assert len(comps) >= 1, mapper.get_error_report()
    return comps[0]
```

### Test cases

| Test | Assertion |
|---|---|
| `test_component_name` | `comp.name == 'counter_c'` |
| `test_field_count` | `len(comp.fields) == 1` |
| `test_count_field_is_reg` | `comp.fields[0].is_reg is True` |
| `test_count_field_is_output` | `comp.fields[0].is_output_port is True` |
| `test_count_field_bits` | `comp.fields[0].datatype.bits == 32` |
| `test_count_field_unsigned` | `comp.fields[0].datatype.signed is False` |
| `test_proc_count` | `len(comp.proc_processes) == 1` |
| `test_proc_name` | `comp.proc_processes[0].name == 'run'` |
| `test_proc_body_is_while` | `isinstance(comp.proc_processes[0].body[0], StmtWhile)` |
| `test_while_test_is_true` | `proc.body[0].test == ExprConstant(True)` |
| `test_while_body_has_one_stmt` | `len(proc.body[0].body) == 1` |
| `test_body_stmt_is_await_write` | `isinstance(body[0], StmtExpr)` and `isinstance(body[0].expr, ExprAwait)` |
| `test_write_call_attribute` | `body[0].expr.expr.func` is `ExprAttribute(ExprRefField(0), 'write')` |
| `test_read_call_in_arg` | First arg of write call contains `ExprCall` with `ExprAttribute(ExprRefField(0), 'read')` |
| `test_no_errors` | `mapper.has_errors() is False` |

---

## Phase 9 — Roundtrip synthesis test

**File**: `packages/zuspec-synth/tests/test_counter_roundtrip.py` (new)

Verifies that the IR produced from SV synthesis back to Verilog matches the
output produced from the Python `Counter` class — the full roundtrip.

```python
def test_sv_to_ir_to_verilog_matches_python():
    # 1. Synthesize from Python Counter
    from counter import Counter
    python_sv = synthesize(Counter)

    # 2. Map SV counter files → IR → synthesize
    mapper = SVMapper(config=SVToZuspecConfig(include_dirs=[EXAMPLES_DIR]))
    mapper.map_files([counter_pkg_sv, counter_sv])
    comp = mapper.get_components()[0]
    sv_sv = synthesize_component(comp)

    # Both should contain the same always@(posedge clock or posedge reset) structure
    assert 'always @(posedge clock or posedge reset)' in python_sv
    assert 'always @(posedge clock or posedge reset)' in sv_sv
    assert 'count <= count + 1' in sv_sv
```

---

## Phase 10 — Documentation

### `packages/zuspec-fe-sv/README.md`

Update status section to "Phase 5 Complete":
- Add bullet: `✅ Phase 5: Component and proc mapping (zsp_component, zsp_reg_c, module annotation)`
- Update test count

### `packages/zuspec-fe-sv/docs/` (if exists)

Add or update a section on `zsp_*` library recognition and the two-file
pattern (class file + module file).

### `examples/01_counter/design.md`

Add a section describing the SV frontend:
- How to run `SVMapper` on the counter files
- What IR is produced
- How it connects to synthesis

---

## Dependency Order

```
Phase 1  (StmtMapper: forever)
Phase 2  (ExprMapper: field context + method calls)
Phase 3  (TypeMapper: zsp_reg width)
    └─ Phase 4 (ClassMapper: map_component)
        └─ Phase 5 (FunctionMapper: confirm threading)
            ├─ Phase 6 (ModuleMapper: port annotation)
            │   └─ Phase 7 (SVMapper: orchestration)
            │       ├─ Phase 8 (E2E Counter test)
            │       └─ Phase 9 (Roundtrip synthesis test)
            └─ Phase 10 (Documentation)
```

Phases 1–3 are independent and can be implemented in parallel.
Phase 4 depends on 2 and 3. Phase 5 is a validation step only.
Phases 6 and 7 depend on 4. Phases 8 and 9 depend on 7.

---

## File Inventory

| File | Action | Phase |
|---|---|---|
| `src/zuspec/fe/sv/stmt_mapper.py` | Add `ForeverLoop` case | 1 |
| `src/zuspec/fe/sv/expr_mapper.py` | Field context + method call mapping | 2 |
| `src/zuspec/fe/sv/type_mapper.py` | Add `map_zsp_reg_width()` | 3 |
| `src/zuspec/fe/sv/class_mapper.py` | Add `map_component()`, predicates | 4 |
| `src/zuspec/fe/sv/function_mapper.py` | Verify field context threading | 5 |
| `src/zuspec/fe/sv/module_mapper.py` | New file | 6 |
| `src/zuspec/fe/sv/mapper.py` | Two-phase, `get_components()`, include dirs | 7 |
| `src/zuspec/fe/sv/config.py` | Add `include_dirs` field | 7 |
| `tests/unit/test_stmt_mapper.py` | Add `forever` tests | 1 |
| `tests/unit/test_expr_mapper_fields.py` | New file | 2 |
| `tests/unit/test_type_mapper.py` | Add `zsp_reg_width` tests | 3 |
| `tests/unit/test_component_mapper.py` | New file | 4 |
| `tests/unit/test_function_mapper.py` | Add async/field-context tests | 5 |
| `tests/unit/test_module_mapper.py` | New file | 6 |
| `tests/unit/test_integration.py` | Add `get_components` tests | 7 |
| `tests/unit/test_counter_sv_to_ir.py` | New end-to-end file | 8 |
| `packages/zuspec-synth/tests/test_counter_roundtrip.py` | New roundtrip file | 9 |
| `packages/zuspec-fe-sv/README.md` | Update status | 10 |
| `examples/01_counter/design.md` | Add SV frontend section | 10 |

**Estimated new test count**: 59 existing + ~40 new = ~99 tests
