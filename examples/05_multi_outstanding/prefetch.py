"""prefetch.py — Example: multi-outstanding prefetch buffer via IfProtocol.

Demonstrates::

    max_outstanding=4, in_order=True

The prefetch buffer issues up to 4 memory read requests before waiting for
any response.  Responses arrive in order (``in_order=True``).

In hardware this synthesises to Scenario C:
- Full req/resp handshake (Scenario B signals)
- An inflight counter (max value 4)
- A response FIFO (depth 4) to buffer out-of-phase responses
"""

import zuspec.dataclasses as zdc


class MemIface(zdc.IfProtocol,
               max_outstanding=4,
               in_order=True,
               req_always_ready=False,
               resp_always_valid=False):
    """In-order memory read interface supporting 4 outstanding requests.

    * ``max_outstanding=4`` — up to 4 reads can be in flight simultaneously.
    * ``in_order=True`` — responses arrive in the same order as requests.
    * Synthesises to Scenario C: handshake + inflight counter + response FIFO.
    """
    async def read(self, addr: zdc.u32) -> zdc.u32: ...


@zdc.dataclass
class Prefetch(zdc.Component):
    """Prefetch buffer: issues 4 reads ahead of consumption.

    The component continuously issues memory reads up to the outstanding
    limit, then drains the in-order responses into a local accumulator.
    """

    mem: MemIface = zdc.port()
    sum_out: zdc.Reg[zdc.u64] = zdc.output()

    @zdc.proc
    async def _run(self):
        acc: zdc.u64 = 0
        addr: zdc.u32 = 0

        while True:
            # Issue a burst of 4 reads
            results = []
            for _ in range(4):
                # zdc.spawn() would be used here for true pipelining;
                # for the behavioral model we await sequentially.
                val = await self.mem.read(addr)
                results.append(val)
                addr = (addr + 4) & 0xFFFF_FFFF

            # Drain responses
            for val in results:
                acc = (acc + val) & 0xFFFF_FFFF_FFFF_FFFF

            await self.sum_out.write(acc)
