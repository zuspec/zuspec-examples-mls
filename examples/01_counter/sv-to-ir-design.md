# SV-to-IR Design: Mapping counter.sv to DataTypeComponent IR

## Goal

Parse `counter_c.svh` (plus its wrapper `counter.sv`) using `zuspec-fe-sv` and
produce the same `DataTypeComponent` IR that the Python `Counter` class produces.
That IR can then be synthesized to Verilog by `zuspec-synth` without any
synthesizer changes.

---

## The Two Examples Side-by-Side

**Python** (`counter.py`)
```python
@zdc.dataclass
class Counter(zdc.Component):
    count: zdc.Reg[zdc.b32] = zdc.output()

    @zdc.proc
    async def _count(self):
        while True:
            await self.count.write(self.count.read() + 1)
```

**SystemVerilog** (`counter_c.svh`)
```systemverilog
class counter_c extends zsp_component;
    `zsp_component_utils(counter_c)
    zsp_reg_c #(reg[31:0])    count;

    task run();
        forever begin
            count.write(count.read()+1);
        end
    endtask
endclass
```

**Structural wrapper** (`counter.sv`)
```systemverilog
module counter(
    input               clock,
    input               reset,
    output reg[31:0]    count);
    import counter_pkg::*;

    zsp_reg_if #(reg[31:0])  count_if(.clock(clock), .val(count));
    zsp_clock_if clock_if(.clock(clock));
    zsp_reset_if reset_if(.reset(reset));

    initial begin
        automatic zsp_component_root #(counter_c) counter = new();
        counter.count = zsp_reg_c #(reg[31:0])::mk(count_if);
        counter.clock_d = zsp_domain_clock_c::mk(clock_if);
        counter.run();
    end
endmodule
```

---

## Target IR

Both examples must produce an identical `DataTypeComponent`:

```
DataTypeComponent(
  name = 'counter_c',
  fields = [
    Field(name='count',
          datatype=DataTypeInt(bits=32, signed=False),
          is_reg=True,
          is_output_port=True,
          reset_value=0)
  ],
  proc_processes = [
    Process(name='run',
      body=[
        StmtWhile(
          test=ExprConstant(True),
          body=[
            StmtExpr(
              ExprAwait(
                ExprCall(
                  func=ExprAttribute(ExprRefField(field_idx=0), 'write'),
                  args=[ExprBin(
                    ExprCall(
                      func=ExprAttribute(ExprRefField(field_idx=0), 'read'),
                      args=[]
                    ),
                    op='+',
                    ExprConstant(1)
                  )]
                )
              )
            )
          ]
        )
      ]
    )
  ]
)
```

---

## The `zsp_*` Class Library

The SV example uses a small class library (in `share/`) that is the SV
analogue of the Python `zdc` decorators and types. Understanding this
library is the key to mapping correctly.

| SV library entity | Python equivalent | Semantic meaning |
|---|---|---|
| `extends zsp_component` | `class Foo(zdc.Component)` | This is a component |
| `zsp_reg_c #(T) count` | `count: zdc.Reg[T] = zdc.output()` | A registered output port |
| `task run()` | `@zdc.proc async def _count(self)` | The proc body |
| `forever begin … end` | `while True:` | Infinite loop |
| `count.write(val)` (task) | `await self.count.write(val)` | Clock-edge write |
| `count.read()` (function) | `self.count.read()` | Combinational read |
| `zsp_reg_if` (interface) | implicit (field has `is_output_port=True`) | Connects field → module port |
| `zsp_clock_if` / `zsp_domain_clock_c` | implicit clock domain | Provides clock to the proc |
| `zsp_reset_if` | implicit reset domain | Provides reset to the proc |

### `zsp_reg_c` internals (from `zsp_reg_c.svh`)

```systemverilog
class zsp_reg_c #(type T);
    virtual interface zsp_reg_if #(T) vif;
    T val;

    function T read();   return val;     endfunction
    task      write(T v); ... vif.write(val); endtask
endclass
```

`read()` is a **function** (synchronous). `write()` is a **task** (timing-consuming —
it delegates to `zsp_reg_if.write()` which has `@(posedge clock)`). This maps
exactly to `is_async=False` for `read` and `is_async=True` for `write`, which the
synthesizer uses to distinguish `ExprCall` (comb) from `ExprAwait(ExprCall(...))` (FF).

---

## Two-File Pattern

The SV counter is split across two files with distinct roles:

**Class file** (`*_c.svh`) — behavioral model, maps to IR:
- `zsp_component` subclass → `DataTypeComponent`
- `zsp_reg_c #(T)` fields → `Field(is_reg=True, ...)`
- `task run()` body → `proc_processes`

**Module file** (`*.sv`) — structural wrapper, provides hardware context:
- Module ports (`clock`, `reset`) → clock/reset domain
- `output reg[31:0] count` → `is_output_port=True` on the matching field
- `zsp_reg_if #(T) count_if(...)` → binds the reg field to the output port
- `zsp_component_root #(counter_c)` instantiation → identifies root class `T`

The mapper must process **both files** and merge the results:
1. Parse and map the class file → get `DataTypeComponent` skeleton
2. Parse and analyze the module file → annotate the skeleton with port/clock/reset info

---

## Slang AST Observations (Verified Experimentally)

### `zsp_reg_c #(T)` field type

```python
# From ClassProperty 'count':
sym.type.name           # → 'zsp_reg_c'
sym.type.genericClass   # → the unspecialized ClassType
# To get T's width, find the 'val' member of the specialized type:
val_sym = sym.type.find('val')
val_sym.type.bitWidth   # → 32  (for T = bit[31:0])
val_sym.type.isSigned   # → False
```

This gives us the bit-width of the register without needing to parse
the type-parameter syntax directly.

### `forever begin ... end`

```
StatementKind.ForeverLoop
  StatementKind.Block
    StatementKind.ExpressionStatement
      ExpressionKind.Call  (count.write(...))
```

Slang uses `StatementKind.ForeverLoop`, not `WhileLoop`. The IR target is
`StmtWhile(test=ExprConstant(True), body=[...])`.

### `count.write(count.read()+1)` call

```
ExpressionKind.Call
  .subroutine → Symbol(SymbolKind.Subroutine, "write"), subroutineKind=Task
  ExpressionKind.NamedValue
    .symbol → Symbol(SymbolKind.ClassProperty, "count")   ← the receiver
  ExpressionKind.BinaryOp                                  ← first arg
    .left  → ExpressionKind.Call (count.read())
    .right → ExpressionKind.IntegerLiteral (1)
```

Key points:
- The receiver is a `NamedValue` whose `.symbol.kind == ClassProperty` and
  `.symbol.name == 'count'` → maps to `ExprRefField(field_idx)`.
- `subroutineKind == Task` → the call is timing-consuming → wrap in `ExprAwait`.
- `subroutineKind == Function` → the call is combinational → no `ExprAwait`.

### Module ports

```
SymbolKind.Instance   counter
  SymbolKind.Port     clock   (input)
  SymbolKind.Port     reset   (input)
  SymbolKind.Port     count   (output)
```

Matching port name `count` to field name `count` in the class → set
`is_output_port=True` on that field.

---

## Gaps in Current `zuspec-fe-sv` Implementation

### 1. `TypeMapper` — rejects `reg[31:0]`

**Current**: `map_builtin_type('reg', ...)` returns `None` and logs an error.

**Needed**: `zsp_reg_c #(reg[31:0])` is the canonical field declaration.  
The `reg` type is only used as a template parameter, not as a variable type.
The mapper must recognize `zsp_reg_c` as a known generic and extract width
from the specialization's `val` member type (using `sym.type.find('val')`),
bypassing the 4-state check entirely.

**New method**: `TypeMapper.map_zsp_reg_width(class_property_symbol) → (bits, signed)`
```python
def map_zsp_reg_width(self, prop_sym):
    val = prop_sym.type.find('val')
    if val is None:
        return (32, False)  # safe default
    return (val.type.bitWidth, val.type.isSigned)
```

### 2. `ClassMapper` — no `zsp_component` recognition

**Current**: All SV classes map to `DataTypeClass`.

**Needed**:
- Detect `extends zsp_component` (or `zsp_component` in the inheritance chain)
  → emit `DataTypeComponent` instead of `DataTypeClass`.
- Detect `zsp_reg_c #(T)` fields → emit `Field(is_reg=True, is_output_port=True)`.
- Skip `zsp_component_utils(...)` macro expansion artifacts.
- Build a `field_name → field_index` map to pass to expression mappers.

**Detection logic**:
```python
def _is_zsp_component_subclass(class_symbol) -> bool:
    bc = class_symbol.baseClass
    while bc is not None:
        if bc.name == 'zsp_component':
            return True
        bc = getattr(bc, 'baseClass', None)
    return False

def _is_zsp_reg_field(prop_sym) -> bool:
    gc = getattr(prop_sym.type, 'genericClass', None)
    return gc is not None and gc.name == 'zsp_reg_c'
```

### 3. `FunctionMapper` — `task run()` not classified as proc

**Current**: All subroutines produce `Function` objects placed in `functions`.

**Needed**: `task run()` in a `zsp_component` subclass is the proc body. The
function mapper (or class mapper) must detect this and place the result in
`DataTypeComponent.proc_processes` as a `Process` node, not `functions`.

**Classification rule**: If the class maps to `DataTypeComponent` and the task
name is `run`, it goes into `proc_processes`. Other tasks/functions go into
`functions` as before.

### 4. `StmtMapper` — no `StatementKind.ForeverLoop`

**Current**: Only handles `WhileLoop`, `DoWhileLoop`.

**Needed**:
```python
elif stmt_kind == 'StatementKind.ForeverLoop':
    body_stmts = self.map_statements(sv_stmt.body)
    return StmtWhile(test=ExprConstant(value=True), body=body_stmts)
```

### 5. `ExprMapper` — no field-context-aware name resolution

**Current**: `NamedValue` references always produce `ExprRefLocal(name=...)`.

**Needed**: When the `NamedValue.symbol.kind == ClassProperty`, the name is a
field on the component, not a local variable. Must produce
`ExprRefField(field_idx=<index>)`.

The mapper needs a **field context** (a `dict[str, int]` mapping field name →
index) injected at mapping time.

**New method**: `ExprMapper.set_field_context(field_map: dict[str, int])`

Then in `_map_named_value`:
```python
if str(sv_expr.symbol.kind) == 'SymbolKind.ClassProperty':
    name = str(sv_expr.symbol.name)
    if name in self._field_context:
        return ExprRefField(field_idx=self._field_context[name])
return ExprRefLocal(name=str(sv_expr.symbol.name))
```

### 6. `ExprMapper` — `ExpressionKind.Call` on task vs function

**Current**: `_map_call` builds an `ExprCall` with a local name ref.

**Needed**:
- Detect that the call target is a method on a field (receiver is `ClassProperty`)
  → build `ExprAttribute(ExprRefField(idx), method_name)` as the func.
- Detect `subroutineKind == Task` → wrap the entire call in `ExprAwait`.
- Return `ExprCall(func=ExprAttribute(...), args=[...])` (possibly in `ExprAwait`).

```python
def _map_method_call(self, sv_expr):
    sub = sv_expr.subroutine
    method_name = str(sub.name)
    is_task = 'Task' in str(sub.subroutineKind)

    # First child is the receiver NamedValue
    receiver = self._first_arg(sv_expr)  # → ExprRefField or ExprRefLocal
    args = [self.map_expression(a) for a in self._remaining_args(sv_expr)]

    call = ExprCall(
        func=ExprAttribute(value=receiver, attr=method_name),
        args=args,
        keywords=[]
    )
    return ExprAwait(expr=call) if is_task else call
```

Note: Slang's `ExpressionKind.Call` for a method call exposes the receiver as
the first child in the visitor walk (before the arg list). The child ordering
in `ExpressionKind.Call` from our AST dump is:
`[receiver_NamedValue, arg0, arg1, ...]`. We must skip the receiver when
building the `args` list.

### 7. New: `ModuleMapper` (doesn't exist yet)

Maps `counter.sv` to extract:
- Root class name (from `zsp_component_root #(counter_c)`) → used as the type to look up
- Output port name → field name binding (by matching `output` port names to field names)
- Presence of clock/reset ports → set `clock_domain`/`reset_domain` on the component

This is a **post-processing step** that annotates an already-mapped
`DataTypeComponent` with port and domain information.

```python
class ModuleMapper:
    def annotate(self, module_symbol, component: DataTypeComponent) -> None:
        # Match output port names → field names → set is_output_port=True
        for port in module_symbol.ports:
            if port.direction == 'Output':
                for field in component.fields:
                    if field.name == port.name:
                        field.is_output_port = True
        # Detect clock/reset ports
        port_names = {str(p.name) for p in module_symbol.ports}
        if 'clock' in port_names:
            component.clock_domain = DefaultClockDomain()
        if 'reset' in port_names:
            component.reset_domain = DefaultResetDomain()
```

---

## Proposed Mapping Flow

```
Input files: counter_pkg.sv, counter.sv
         (counter_pkg.sv includes counter_c.svh via `include)

svmapper.map_files([counter_pkg.sv, counter.sv])
  ├─ parser.parse_files(...)     → pyslang.Compilation
  ├─ Phase 1: collect classes
  │    root.visit(...)
  │    For each ClassType:
  │      if _is_zsp_component_subclass(cls):
  │        class_mapper.map_component(cls)   → DataTypeComponent
  │      else:
  │        class_mapper.map_class(cls)       → DataTypeClass
  │
  ├─ Phase 2: collect modules
  │    For each Instance with InstanceBody:
  │      module_mapper.annotate(module, component_map[root_class_name])
  │
  └─ returns [DataTypeComponent, ...]
```

---

## New `class_mapper.map_component()` Algorithm

```
map_component(class_symbol):
  1. name = class_symbol.name
  2. fields = []
     field_index_map = {}
     for prop in class_symbol members where kind == ClassProperty:
       if _is_zsp_reg_field(prop):
         bits, signed = type_mapper.map_zsp_reg_width(prop)
         fields.append(Field(name=prop.name,
                             datatype=DataTypeInt(bits=bits, signed=signed),
                             is_reg=True,
                             is_output_port=True,  # will be confirmed by module
                             reset_value=0))
         field_index_map[prop.name] = len(fields)-1
       elif _is_known_library_field(prop):
         skip  # e.g., clock_d, m_parent, m_children
       else:
         field = _map_plain_property(prop)
         if field: fields.append(field)

  3. expr_mapper.set_field_context(field_index_map)

  4. proc_processes = []
     functions = []
     for sub in class_symbol members where kind == Subroutine:
       if sub.name in ('new', 'randomize', ...):  # skip built-ins
         continue
       func = function_mapper.map_function(sub)
       if sub.name == 'run' and sub.subroutineKind == Task:
         proc_processes.append(Process(name=sub.name, body=func.body))
       else:
         functions.append(func)

  5. return DataTypeComponent(
       name=name,
       fields=fields,
       functions=functions,
       proc_processes=proc_processes,
     )
```

---

## `forever` → `StmtWhile` Mapping

```
StatementKind.ForeverLoop  →  StmtWhile(test=ExprConstant(True), body=...)
```

Access the body via `sv_stmt.body` (a `Block` statement). Recursively map the
block's contents with `map_statements(sv_stmt.body)`.

---

## `count.write(count.read()+1)` → IR Mapping

The slang AST for this call (verified experimentally):

```
ExpressionKind.Call                    ← count.write(...)
  .subroutine → write (Task)
  children[0]: ExpressionKind.NamedValue  ← count (receiver)
    .symbol.kind = ClassProperty
    .symbol.name = 'count'
  children[1]: ExpressionKind.BinaryOp   ← count.read() + 1 (arg 0)
    .left:  ExpressionKind.Call (count.read(), Function)
    .right: ExpressionKind.IntegerLiteral (1)
```

Target IR:
```
ExprAwait(
  ExprCall(
    func=ExprAttribute(ExprRefField(0), 'write'),
    args=[
      ExprBin(
        ExprCall(func=ExprAttribute(ExprRefField(0), 'read'), args=[]),
        '+',
        ExprConstant(1)
      )
    ]
  )
)
```

**Receiver extraction**: In `ExpressionKind.Call`, the receiver is provided as
the first child in the visit order but NOT included in the argument list. We
visit all children, classify the first `NamedValue` with `ClassProperty` kind
as the receiver, and map remaining children as the argument list.

**Task vs Function**: `sv_expr.subroutine.subroutineKind`:
- `SubroutineKind.Task` → wrap entire `ExprCall` in `ExprAwait`
- `SubroutineKind.Function` → return `ExprCall` directly

---

## What Does NOT Need to Change

- `zuspec-synth` — already handles `DataTypeComponent` with `proc_processes`,
  `is_reg=True` fields, `ExprAwait`, `ExprAttribute`, `ExprRefField`
- `zuspec-dataclasses` IR node definitions — `DataTypeComponent`, `Field`,
  `Process`, `StmtWhile`, `ExprAwait`, `ExprAttribute`, `ExprRefField`,
  `ExprCall`, `ExprBin`, `ExprConstant` all exist
- `zuspec-dataclasses` runtime — already has `RegProcRT`, `tick_cycle()`, etc.

---

## Files to Create/Modify in `zuspec-fe-sv`

| File | Change |
|---|---|
| `type_mapper.py` | Add `map_zsp_reg_width(prop_sym)` |
| `class_mapper.py` | Add `map_component()`, `_is_zsp_component_subclass()`, `_is_zsp_reg_field()` |
| `function_mapper.py` | Accept `field_index_map` context; classify `run` task → proc |
| `stmt_mapper.py` | Add `StatementKind.ForeverLoop` case |
| `expr_mapper.py` | Add `set_field_context()`; fix `_map_named_value` and `_map_call` |
| `mapper.py` | Call `map_component()` for `zsp_component` subclasses; add module phase |
| `module_mapper.py` | New file: `ModuleMapper.annotate()` |
| `tests/unit/test_counter_sv_to_ir.py` | New end-to-end test |

---

## End-to-End Test Shape

```python
def test_counter_c_maps_to_component():
    mapper = SVMapper()
    mapper.map_files(['counter_pkg.sv', 'counter.sv'])
    assert not mapper.has_errors()

    comp = mapper.get_components()[0]
    assert isinstance(comp, DataTypeComponent)

    assert len(comp.fields) == 1
    assert comp.fields[0].name == 'count'
    assert comp.fields[0].is_reg is True
    assert comp.fields[0].is_output_port is True
    assert comp.fields[0].datatype.bits == 32

    assert len(comp.proc_processes) == 1
    proc = comp.proc_processes[0]
    assert proc.name == 'run'
    assert len(proc.body) == 1
    assert isinstance(proc.body[0], StmtWhile)

    while_body = proc.body[0].body
    assert len(while_body) == 1
    assert isinstance(while_body[0], StmtExpr)
    assert isinstance(while_body[0].expr, ExprAwait)
```

---

## Open Questions

1. **`reg[31:0]` as template parameter**: slang will elaborate the specialization
   `zsp_reg_c #(reg[31:0])` and give us `val.type.bitWidth = 32`. The 4-state
   check fires when `reg` is used as a *variable* type, not a template arg. We
   should verify that slang marks the `val` member's elaborated type as
   `isFourState = True` and decide whether to warn or suppress (it's not a
   variable we generate code for — it's only used to carry width info).

2. **`zsp_macros.svh`** contains `` `define `` macros that slang's preprocessor
   will expand. The `zsp_component_utils` macro expands to a `create` static
   function, which appears as a normal `Subroutine` in the AST. The class
   mapper must skip known library-generated members (`create`, `randomize`,
   `pre_randomize`, etc.).

3. **Package compilation order**: `counter_pkg.sv` uses `` `include "counter_c.svh" ``
   which is a relative path. The mapper must add the package file's directory
   to the slang include path. This requires `SVToZuspecConfig` to support
   `include_dirs: List[str]`.

4. **Multiple components**: Larger designs will have multiple `zsp_component`
   subclasses. `SVMapper.get_components()` should return all of them, not just
   the first.

5. **Module-to-class resolution**: The module references `counter_c` by name
   inside a template parameter (`zsp_component_root #(counter_c)`). Extracting
   this name requires parsing the `initial` block's variable declarations, which
   are `StatementKind.VariableDeclaration` nodes. Slang does elaborate these,
   so we can read the type name from the variable's type symbol.
