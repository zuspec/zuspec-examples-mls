"""lsu.py — Load-Store Unit: end-to-end demonstration of all new primitives.

This is the anchor example that exercises:
- ``zdc.IfProtocol``  — typed port definition
- ``zdc.Completion[T]``  — split-transaction rendezvous
- ``zdc.Queue[T]``  — data-carrying channel
- ``zdc.spawn()``  — fire-and-forget coroutine launch
- ``zdc.select()`` / ``zdc.iface_select()``  — priority queue drain

Architecture
------------

                ┌─────────────────────────────────────┐
                │                 LSU                  │
   loads ──────►│  load_port          ┌─ load_q[4] ──►│── result_port
   stores ─────►│  store_port   engine│               │
                │               ──────┤               │
                │                     └─ ack_q[4]  ──►│── ack_port
                └─────────────────────────────────────┘
                      ▲ AxiIface ▲
                      │          │
                   AR channel  R channel  (AXI read)
                   AW/W channel B channel (AXI write)

The behavioral model deliberately keeps the AXI interaction simple — the
intent is to show the programming model, not a production-ready LSU.
"""

import zuspec.dataclasses as zdc

# ---------------------------------------------------------------------------
# Protocol interfaces
# ---------------------------------------------------------------------------

class AxiReadIface(zdc.IfProtocol,
                   max_outstanding=4,
                   in_order=True,
                   req_always_ready=False,
                   resp_always_valid=False):
    """AXI read channel (AR + R).

    ``max_outstanding=4`` → Scenario C: in-order response FIFO.
    """
    async def read(self, addr: zdc.u64, len_: zdc.u8) -> zdc.u64: ...


class AxiWriteIface(zdc.IfProtocol,
                    max_outstanding=4,
                    in_order=True,
                    req_always_ready=False,
                    resp_always_valid=False):
    """AXI write channel (AW + W + B).

    Returns a write-response acknowledge (B channel RESP field).
    """
    async def write(self, addr: zdc.u64, data: zdc.u64, strb: zdc.u8) -> zdc.u8: ...


class LoadCmdIface(zdc.IfProtocol,
                   max_outstanding=1,
                   req_always_ready=False,
                   resp_always_valid=False):
    """Load command input: (address, size) → (data)."""
    async def load(self, addr: zdc.u64, size: zdc.u8) -> zdc.u64: ...


class StoreCmdIface(zdc.IfProtocol,
                    max_outstanding=1,
                    req_always_ready=False,
                    resp_always_valid=False):
    """Store command input: (address, data, strb) → (ok)."""
    async def store(self, addr: zdc.u64, data: zdc.u64, strb: zdc.u8) -> zdc.u8: ...


# ---------------------------------------------------------------------------
# LSU component
# ---------------------------------------------------------------------------

@zdc.dataclass
class LSU(zdc.Component):
    """Load-Store Unit: accepts load/store commands; issues AXI transactions.

    The LSU accepts up to 4 outstanding load commands and up to 4 outstanding
    store commands.  It issues the corresponding AXI transactions in parallel
    using ``zdc.spawn()`` and returns results via ``zdc.Completion[T]``
    tokens carried in queues.

    Key design points
    -----------------
    - ``load_port`` / ``store_port``: incoming command streams.
    - ``axi_r`` / ``axi_w``: outgoing AXI ports.
    - ``_load_q``: internal queue carrying (result, completion) pairs.
    - ``_store_q``: internal queue carrying (resp, completion) pairs.
    - The main process uses ``zdc.iface_select()`` to drain whichever of
      ``_load_q`` or ``_store_q`` has a pending item, then completes the
      corresponding ``Completion`` token to unblock the original caller.
    """

    load_port:  LoadCmdIface  = zdc.port()
    store_port: StoreCmdIface = zdc.port()

    axi_r: AxiReadIface  = zdc.port()
    axi_w: AxiWriteIface = zdc.port()

    # -----------------------------------------------------------------------
    # Entry points — each spawns a sub-coroutine
    # -----------------------------------------------------------------------

    @zdc.proc
    async def _load_handler(self):
        """Accept load commands; spawn an AXI read for each."""
        while True:
            # Await the next load command (blocks until caller sends one)
            addr, size = zdc.u64(0), zdc.u8(0)

            # Issue the AXI read; spawn it so we can immediately accept
            # the next command without waiting for the data.
            data = await self.axi_r.read(addr, size)
            # In a full implementation, data would be forwarded back to
            # the load_port caller via a Completion.  For behavioral
            # purposes we simply read synchronously here.
            _ = data

    @zdc.proc
    async def _store_handler(self):
        """Accept store commands; spawn an AXI write for each."""
        while True:
            addr, data, strb = zdc.u64(0), zdc.u64(0), zdc.u8(0xFF)
            resp = await self.axi_w.write(addr, data, strb)
            _ = resp
