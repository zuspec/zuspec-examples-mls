"""RV32 Core stub — provides the Fetch method port consumed by RV32IDecode.

Teaching note:
  This component represents the processor pipeline context.  In a real design
  it would hold register files, pipeline state, and control signals.  Here it
  is intentionally minimal: its only job is to supply instruction words to the
  decode action and to demonstrate how Actions integrate with Components.
"""
import zuspec.dataclasses as zdc


@zdc.dataclass
class RV32Core(zdc.Component):
    """Minimal processor core stub.

    The 'fetch' port models the instruction-memory interface: the core calls
    it each cycle to receive the next raw instruction word.  In simulation the
    port is driven by the testbench; in hardware it connects to an instruction
    cache or ROM.
    """

    fetch: zdc.InPort[zdc.u32] = zdc.in_port()
