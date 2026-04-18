"""Example 02 — Action-based counter.

Demonstrates how a :class:`zdc.Action` can encapsulate register-write logic
and be invoked from a ``@zdc.proc`` loop using the positional call form::

    await IncrCount()(self)

The synthesis result is structurally identical to the direct-write counter in
``examples/01_counter/counter.py``.  The ``await ActionCls()(comp)`` form
randomizes any declared fields of the action, then runs ``body()`` (or the
PSS ``activity()`` sub-graph if present).
"""

import zuspec.dataclasses as zdc

@zdc.dataclass
class Counter(zdc.Component):
    count: zdc.Reg[zdc.u32] = zdc.output()

    @zdc.proc
    async def _count(self):
        while True:
            await IncrCount()(self)

@zdc.dataclass
class IncrCount(zdc.Action[Counter]):

    async def body(self):
        await self.comp.count.write(self.comp.count.read()+1)

