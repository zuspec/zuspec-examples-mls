
import zuspec.dataclasses as zdc

@zdc.dataclass
class Constraints(zdc.Component):
    val: zdc.Reg[zdc.b32] = zdc.output()

    @zdc.proc
    async def _eval(self):
        while True:
            for i in range(64):
                val = await self._compute(i)
                await self.val.write(val)

@zdc.dataclass
class Compute(zdc.Action[Constraints]):
    async def _compute(self, val: zdc.i32) -> zdc.i32:
        ret = 0
        for i in range(val):
            ret += val
            await zdc.cycles(1)
        return ret
