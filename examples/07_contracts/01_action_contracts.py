"""01_action_contracts.py — Contracts on action classes.

Scenario
--------
A memory controller accepts MemWrite and MemRead actions.

Contracts express invariants that the *test* must satisfy (requires) and
invariants the *DUT* must maintain (ensures).  The solver respects requires
constraints when randomizing; the runner enforces ensures at runtime.

Key design insight
------------------
* ``@constraint.requires``  — solver always satisfies these; also checked at
                              runtime *before* body() executes.
* ``@constraint.ensures``   — NOT seen by solver; checked *after* body().
                              Catches bugs in the DUT model, not the stimulus.
* ``@constraint``           — plain randomization constraint (solver only).

Run:
    python 01_action_contracts.py
"""

import asyncio
import sys
import os

# Path setup so the example runs from the repo without installation.
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(_root, 'packages', 'zuspec-dataclasses', 'src'))

import zuspec.dataclasses as zdc

# ─────────────────────────────────────────────────────────────────────────────
# DUT model
# ─────────────────────────────────────────────────────────────────────────────

@zdc.dataclass
class MemCtrl(zdc.Component):
    """Minimal memory controller model.

    ``last_addr`` and ``last_data`` record what was last written so that
    a subsequent read action can verify the DUT's response.
    """
    last_addr: zdc.u32 = zdc.field(default=0)
    last_data: zdc.u32 = zdc.field(default=0)


# ─────────────────────────────────────────────────────────────────────────────
# Action: MemWrite
#
# The addr field is split into a *word_offset* (0..0x3FFF) so the byte
# address is always 4-byte aligned without requiring a modulo constraint —
# alignment becomes a structural guarantee rather than a solver-checked rule.
# ─────────────────────────────────────────────────────────────────────────────

@zdc.dataclass
class MemWrite(zdc.Action[MemCtrl]):
    """Issue a 32-bit word write to the memory controller.

    Fields
    ------
    word_offset
        Random word index (0..0x3FFF).  Byte address = word_offset * 4,
        so alignment is guaranteed structurally.
    data
        Random 32-bit payload.

    Contracts
    ---------
    @constraint.requires  word_in_range
        Keeps writes inside the 64 KiB main-memory window.  The solver
        only generates values satisfying this; no post-hoc filtering needed.
    @constraint.requires  no_mmio_target
        Top 4 KiB words are reserved MMIO space — exclude them.
    @constraint.ensures   data_latched
        After the write, the DUT must latch the data into ``last_data``.
        Catches bugs where the DUT silently drops writes.
    """

    word_offset: zdc.u32 = zdc.field(rand=True)
    data:        zdc.u32 = zdc.field(rand=True)

    # ── Preconditions (restrict stimulus) ────────────────────────────────────

    @zdc.constraint.requires
    def word_in_range(self):
        """Stay inside the 64 KiB window (0x0000..0xFFFF byte range)."""
        assert self.word_offset < 0x4000      # 0x4000 words × 4 = 64 KiB

    @zdc.constraint.requires
    def no_mmio_target(self):
        """Avoid the top 1 KiB words reserved for MMIO (word 0x3C00..0x3FFF)."""
        assert self.word_offset < 0x3C00

    # ── Plain constraint: solver-only, not a formal precondition ─────────────
    @zdc.constraint
    def data_not_poison(self):
        """Avoid the all-ones poison pattern handled specially by the DUT."""
        assert self.data != 0xFFFF_FFFF

    # ── Postcondition (verify DUT response) ──────────────────────────────────

    @zdc.constraint.ensures
    def data_latched(self):
        """DUT must update ``last_data`` to the value just written."""
        assert self.comp.last_data == self.data

    @zdc.constraint.ensures
    def addr_latched(self):
        """DUT must record the byte address in ``last_addr``."""
        assert self.comp.last_addr == self.word_offset * 4

    async def body(self):
        byte_addr = self.word_offset * 4
        self.comp.last_addr = byte_addr
        self.comp.last_data = self.data
        print(f"  MemWrite  word_offset=0x{self.word_offset:04x}"
              f"  addr=0x{byte_addr:05x}  data=0x{self.data:08x}")


# ─────────────────────────────────────────────────────────────────────────────
# Action: MemReadBuggy  (intentional DUT bug for demo)
# ─────────────────────────────────────────────────────────────────────────────

@zdc.dataclass
class MemReadBuggy(zdc.Action[MemCtrl]):
    """Read action that models a buggy DUT.

    The DUT's read path has an off-by-one error: it returns ``last_data - 1``
    instead of ``last_data``.  Without @constraint.ensures this bug is silent.
    With check_contracts=True the runner catches it on the first read.
    """

    word_offset: zdc.u32 = zdc.field(rand=True)
    result:      zdc.u32 = zdc.field(default=0)

    @zdc.constraint.requires
    def word_in_range(self):
        assert self.word_offset < 0x3C00

    @zdc.constraint.ensures
    def result_matches_stored(self):
        """Read must return exactly what was last written."""
        assert self.result == self.comp.last_data

    async def body(self):
        # BUG: off-by-one — returns last_data - 1
        self.result = self.comp.last_data - 1
        print(f"  MemReadBuggy  result=0x{self.result:08x}"
              f"  (expected 0x{self.comp.last_data:08x} — BUG!)")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


# ─────────────────────────────────────────────────────────────────────────────
# Demo
# ─────────────────────────────────────────────────────────────────────────────

def demo_write_contracts_pass():
    print("\n── Demo 1: MemWrite — solver respects @requires, runner verifies @ensures ──")
    print("The solver only generates word_offsets < 0x3C00 (inside main-memory window).")
    print("After each write the runner checks that the DUT latched data and addr.\n")

    ctrl = MemCtrl()
    runner = zdc.ScenarioRunner(ctrl, seed=42, check_contracts=True)
    for _ in range(5):
        action = _run(runner.run(MemWrite))
        assert action.word_offset < 0x3C00, "solver violated @requires!"
    print("\n✓ 5 writes — @requires never broken by solver, @ensures verified every time.")


def demo_ensures_violation():
    print("\n── Demo 2: Buggy DUT — @ensures catches the fault immediately ──────────────")
    print("MemReadBuggy.body() returns last_data - 1 (off-by-one bug in read path).")
    print("With check_contracts=True the first bad read raises ContractViolation.\n")

    ctrl = MemCtrl()
    ctrl.last_data = 0xCAFE_BABE   # pre-load a known value

    runner = zdc.ScenarioRunner(ctrl, seed=7, check_contracts=True)
    try:
        _run(runner.run(MemReadBuggy))
        print("ERROR: expected ContractViolation but none was raised!")
    except zdc.ContractViolation as exc:
        print(f"✓ ContractViolation caught!")
        print(f"  role        = {exc.role}")
        print(f"  method      = {exc.method_name}")


def demo_ensures_invisible_to_solver():
    print("\n── Demo 3: @ensures is invisible to the solver (no false constraints) ───────")
    print("Even though @ensures is violated, the solver runs freely.")
    print("The bug only surfaces when check_contracts=True.\n")

    ctrl = MemCtrl()
    ctrl.last_data = 0x1234_5678

    # Run WITHOUT contract checking — solver must not be affected by @ensures
    runner = zdc.ScenarioRunner(ctrl, seed=99, check_contracts=False)
    action = _run(runner.run(MemReadBuggy))
    expected = ctrl.last_data - 1
    assert action.result == expected, "body must still run"
    print(f"  result = 0x{action.result:08x}  (buggy, but no exception)")
    print("✓ @ensures didn't constrain the solver — correct behavior.")


if __name__ == '__main__':
    demo_write_contracts_pass()
    demo_ensures_violation()
    demo_ensures_invisible_to_solver()
    print()
