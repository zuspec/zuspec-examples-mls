
import zuspec.dataclasses as zdc

@zdc.dataclass
class Counter(zdc.Component):
    count: zdc.Reg[zdc.b32] = zdc.output()

    @zdc.proc
    async def _count(self):
        while True:
            await self.count.write(self.count.read()+1)

