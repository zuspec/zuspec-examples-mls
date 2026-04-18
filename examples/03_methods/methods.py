
import zuspec.dataclasses as zdc


class DatIface(zdc.IfProtocol,
               max_outstanding=1,
               req_always_ready=False,
               resp_always_valid=False):
    """Simple single-outstanding request/response interface.

    The caller sends no arguments; the callee returns a 32-bit value.
    Scenario B synthesis: ``req_valid``/``req_ready`` + ``resp_valid`` handshake.
    """
    async def get(self) -> zdc.u32: ...


@zdc.dataclass
class Methods(zdc.Component):
    """Accumulates values fetched via IfProtocol and writes the running sum."""

    dat: DatIface = zdc.port()
    val: zdc.Reg[zdc.b32] = zdc.output()

    @zdc.proc
    async def _eval(self):
        while True:
            dat = await self.dat.get()
            for i in range(64):
                v = await self._compute(dat + i)
                await self.val.write(v)

    async def _compute(self, val: zdc.i32) -> zdc.i32:
        ret = 0
        for i in range(val):
            ret += val
            await zdc.cycles(1)
        return ret
