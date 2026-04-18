"""02_method_contracts.py — Body contracts inside action.body().

The previous example showed *class-level* contracts: ``@constraint.requires``
shapes the solver, ``@constraint.ensures`` is checked after each action.

This example shows *body-level* contracts — ``with zdc.requires:`` and
``with zdc.ensures:`` written directly inside an action's ``body()`` method.
They serve a different purpose:

  with zdc.requires:   — runtime precondition; defensive assertion that the
                         *execution context* satisfies expected conditions
                         before the action does its work.
  with zdc.ensures:    — runtime postcondition; assertion that the action
                         left the system in the expected state.

The key distinction from class-level contracts:
  - Class-level ``@constraint.requires`` feeds the *solver* → constrains
    random stimulus so invalid inputs are never generated.
  - Body-level ``with zdc.requires:`` fires *at runtime* → catches cases
    where the caller puts the action in an invalid state despite the solver.
  - Body-level ``with zdc.ensures:`` fires *after the body executes* →
    the same semantics as class-level ``@constraint.ensures`` but written
    inline, documenting intent next to the code that should satisfy it.

Scenario
--------
An I2C register-file controller.  Each action reads or writes a single
8-bit register.  The register file has 8 addressable slots (0..7).

  WriteReg  — generates a random address/data pair and writes to the DUT.
  ReadReg   — reads back and verifies the value matches what was written.

We deliberately introduce an off-by-one bug in a "BuggyReadReg" variant
and show that the body ensures clause catches it immediately.

Run:
    python 02_method_contracts.py
"""

import asyncio
import sys
import os

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(_root, 'packages', 'zuspec-dataclasses', 'src'))

import zuspec.dataclasses as zdc
from zuspec.dataclasses.decorators import ContractViolation
from zuspec.dataclasses.rt.scenario_runner import ScenarioRunner
# ─────────────────────────────────────────────────────────────────────────────
# DUT model — simple 8-register file
# ─────────────────────────────────────────────────────────────────────────────

REG_COUNT = 8

@zdc.dataclass
class RegFile(zdc.Component):
    """An 8-register file.  regs[i] holds the current value of register i."""
    regs: list = zdc.field(default_factory=lambda: [0] * REG_COUNT)
    write_count: int = zdc.field(default=0)
    read_count:  int = zdc.field(default=0)


# ─────────────────────────────────────────────────────────────────────────────
# WriteReg action
# ─────────────────────────────────────────────────────────────────────────────

@zdc.dataclass
class WriteReg(zdc.Action[RegFile]):
    """Write an 8-bit value to a randomly chosen register.

    Class-level contracts (solver):
      @constraint.requires addr_in_range
        The solver only generates addresses 0..7.  No need to clamp or
        filter after randomization.

    Body contracts (runtime):
      with zdc.requires:  addr_valid
        Defensive check that addr is still in range at execution time.
        Catches test-infrastructure bugs where the action is pre-set to
        a bad value without going through the solver.
      with zdc.ensures:  write_recorded
        After body() executes, write_count must have incremented.
        Catches a DUT model that silently drops writes.
    """

    addr: zdc.u8 = zdc.field(rand=True)
    data: zdc.u8 = zdc.field(rand=True)

    # ── class-level (solver) ───────────────────────────────────────────────

    @zdc.constraint.requires
    def addr_in_range(self):
        self.addr < 8              # solver satisfies this — no out-of-range addr

    # ── body ──────────────────────────────────────────────────────────────

    async def body(self):
        with zdc.requires:
            self.addr < 8              # runtime defensive check

        # perform the write
        self.comp.regs[self.addr] = self.data
        self.comp.write_count += 1

        with zdc.ensures:
            self.comp.write_count > 0  # write_count must have moved


# ─────────────────────────────────────────────────────────────────────────────
# ReadReg action — correct implementation
# ─────────────────────────────────────────────────────────────────────────────

@zdc.dataclass
class ReadReg(zdc.Action[RegFile]):
    """Read a register and verify it matches the expected value.

    Body contracts (runtime):
      with zdc.requires:  addr_valid + written_something
        Must be called after at least one write has occurred.
      with zdc.ensures:  value_matches
        The returned value must equal the register's current content.
        Catches DUT model bugs.
    """

    addr:     zdc.u8 = zdc.field(rand=True)
    expected: zdc.u8 = zdc.field(default=0)   # set by test infra before run
    result:   zdc.u8 = zdc.field(default=0)   # filled by body()

    @zdc.constraint.requires
    def addr_in_range(self):
        self.addr < 8

    async def body(self):
        with zdc.requires:
            self.addr < 8
            self.comp.read_count >= 0   # always true; here as documentation

        self.result = self.comp.regs[self.addr]
        self.expected = self.comp.regs[self.addr]
        self.comp.read_count += 1

        with zdc.ensures:
            self.result == self.expected    # DUT returned what was stored


# ─────────────────────────────────────────────────────────────────────────────
# BuggyReadReg — off-by-one in address decoding
# ─────────────────────────────────────────────────────────────────────────────

@zdc.dataclass
class BuggyReadReg(zdc.Action[RegFile]):
    """Same as ReadReg but with an off-by-one bug: reads regs[addr + 1]."""

    addr:     zdc.u8 = zdc.field(rand=True)
    expected: zdc.u8 = zdc.field(default=0)
    result:   zdc.u8 = zdc.field(default=0)

    @zdc.constraint.requires
    def addr_in_range(self):
        self.addr < 7              # keep addr in 0..6 so addr+1 stays valid

    async def body(self):
        with zdc.requires:
            self.addr < 7

        buggy_addr = (self.addr + 1) % REG_COUNT   # bug: reads wrong slot
        self.result   = self.comp.regs[buggy_addr]
        self.expected = self.comp.regs[self.addr]   # expect from correct addr
        self.comp.read_count += 1

        with zdc.ensures:
            self.result == self.expected   # catches the off-by-one!


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _sep(title: str):
    print(f"\n── {title} {'─' * max(0, 70 - len(title))}")


def _run_once(action_cls, comp, seed, check_contracts=True):
    async def _go():
        runner = ScenarioRunner(comp, seed=seed, check_contracts=check_contracts)
        return await runner.run(action_cls)
    return asyncio.run(_go())


# ─────────────────────────────────────────────────────────────────────────────
# Demo 1: @constraint.requires feeds the solver — only valid addresses generated
# ─────────────────────────────────────────────────────────────────────────────

def demo1():
    _sep("Demo 1: @constraint.requires feeds the solver — only valid addresses")
    print("WriteReg has @constraint.requires addr_in_range → self.addr < 8.")
    print("The solver ONLY produces addresses in 0..7.  No post-hoc filtering.\n")

    rf = RegFile()
    print(f"  {'seed':>4}  {'addr':>4}  {'data':>4}")
    print("  " + "─" * 22)
    for seed in range(8):
        w = _run_once(WriteReg, rf, seed=seed)
        assert w.addr < REG_COUNT, f"Solver violated @requires! addr={w.addr}"
        print(f"  {seed:>4}  {w.addr:>4}  0x{w.data:02x}")
    print("\n  ✓ All addresses in 0..7 — @constraint.requires shapes the solver.")


# ─────────────────────────────────────────────────────────────────────────────
# Demo 2: normal write-then-read flow, all contracts satisfied
# ─────────────────────────────────────────────────────────────────────────────

def demo2():
    _sep("Demo 2: write-then-read, all body contracts satisfied")
    rf = RegFile()

    print(f"  {'Action':<12} {'addr':>4}  {'data':>4}  {'result':>6}")
    print("  " + "─" * 40)

    for seed in range(5):
        w = _run_once(WriteReg, rf, seed=seed)
        r = _run_once(ReadReg, rf, seed=seed + 100)
        print(f"  WriteReg     addr={w.addr:>3}  data=0x{w.data:02x}")
        print(f"  ReadReg      addr={r.addr:>3}               result=0x{r.result:02x}")

    print(f"\n  Writes: {rf.write_count}   Reads: {rf.read_count}")
    print("  ✓ All body contracts satisfied — requires and ensures both pass.")


# ─────────────────────────────────────────────────────────────────────────────
# Demo 3: BuggyReadReg — off-by-one caught by body ensures
# ─────────────────────────────────────────────────────────────────────────────

def demo3():
    _sep("Demo 3: BuggyReadReg — off-by-one caught by body 'with ensures:'")
    print("BuggyReadReg.body() reads regs[addr+1] instead of regs[addr].")
    print("The 'with zdc.ensures: self.result == self.expected' catches it.\n")

    rf = RegFile()
    # Prime the register file with distinct values so mismatch is detectable
    for i in range(REG_COUNT):
        rf.regs[i] = (i + 1) * 0x11

    # First do a write so write_count > 0
    _run_once(WriteReg, rf, seed=0)

    try:
        _run_once(BuggyReadReg, rf, seed=42)
        print("  ✗ No violation — bug not caught!")
    except ContractViolation as exc:
        print(f"  ✓ ContractViolation raised!")
        print(f"    role   = {exc.role}")
        print(f"    method = {exc.method_name}")
        print(f"\n  The ensures block in body() detected the wrong register was read.")
        print(f"  This is the 'assertion' half: it fires AFTER body() executes.")


# ─────────────────────────────────────────────────────────────────────────────
# Demo 4: BuggyReadReg with check_contracts=False — bug is silent
# ─────────────────────────────────────────────────────────────────────────────

def demo4():
    _sep("Demo 4: check_contracts=False — the bug is invisible")
    print("With check_contracts=False the body ensures block is never evaluated.")
    print("The off-by-one propagates silently — showing WHY enables contracts.\n")

    rf = RegFile()
    for i in range(REG_COUNT):
        rf.regs[i] = (i + 1) * 0x11
    _run_once(WriteReg, rf, seed=0)

    action = _run_once(BuggyReadReg, rf, seed=42, check_contracts=False)
    print(f"  BuggyReadReg: addr={action.addr}  expected=0x{action.expected:02x}"
          f"  result=0x{action.result:02x}")
    if action.result != action.expected:
        print(f"  Bug present but silent — result 0x{action.result:02x} ≠ "
              f"expected 0x{action.expected:02x}")
    print("  ✓ Demonstrates why enabling contracts matters for bug detection.")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    demo1()
    demo2()
    demo3()
    demo4()
    print()
