"""fixed_latency.py — Example: fixed-latency RAM lookup via IfProtocol.

Demonstrates::

    req_always_ready=True, resp_always_valid=True, fixed_latency=4

The caller sends an address and receives data exactly 4 cycles later with
no handshake signals.  In hardware this synthesises to a shift-register
delay line — no ``req_ready`` or ``resp_valid`` signals are generated.
"""

import zuspec.dataclasses as zdc


class RamIface(zdc.IfProtocol,
               max_outstanding=1,
               req_always_ready=True,
               resp_always_valid=True,
               fixed_latency=4):
    """Fixed-latency RAM read interface.

    * ``req_always_ready=True`` — the RAM always accepts an address.
    * ``resp_always_valid=True`` — data is valid exactly ``fixed_latency`` cycles later.
    * No ``req_ready`` or ``resp_valid`` ports are generated.
    """
    async def read(self, addr: zdc.u32) -> zdc.u32: ...


@zdc.dataclass
class FixedLatencyLookup(zdc.Component):
    """Component that reads a value from a fixed-latency RAM and exposes it.

    Because the RAM always accepts requests and always returns data after
    exactly 4 cycles, the generated SV requires only address and data wires —
    no handshake logic.
    """

    ram: RamIface = zdc.port()
    result: zdc.Reg[zdc.u32] = zdc.output()

    @zdc.proc
    async def _run(self):
        addr: zdc.u32 = 0
        while True:
            data = await self.ram.read(addr)
            await self.result.write(data)
            addr = (addr + 1) & 0xFFFF
