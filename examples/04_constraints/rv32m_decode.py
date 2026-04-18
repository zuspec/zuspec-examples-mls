"""RV32M extension — multiply/divide instructions.

Teaching point: extensibility via inheritance.
  Adding an entirely new instruction group requires only this file.
  The base class (RV32IDecode) is not modified at all.
  The synthesizer processes the combined constraint set automatically.

M-extension encoding
--------------------
  All M-extension instructions share opcode=0x33 with the R-type base
  instructions but use a non-zero funct7: funct7[0] (bit 25 of instr) is 1.
  funct3 selects among the 8 multiply/divide operations.
"""
import zuspec.dataclasses as zdc
from zuspec.dataclasses import constraint

from rv32i_decode import RV32IDecode

# ---------------------------------------------------------------------------
# Additional ALU operations for M-extension (extending the base encoding)
# ---------------------------------------------------------------------------
ALU_MUL    = 11  # lower 32 bits of signed × signed
ALU_MULH   = 12  # upper 32 bits of signed × signed
ALU_MULHSU = 13  # upper 32 bits of signed × unsigned
ALU_MULHU  = 14  # upper 32 bits of unsigned × unsigned
ALU_DIV    = 15  # signed division
ALU_DIVU   = 16  # unsigned division
ALU_REM    = 17  # signed remainder
ALU_REMU   = 18  # unsigned remainder


@zdc.dataclass
class RV32MDecode(RV32IDecode):
    """RV32I + M-extension decoder.

    Adds one new derived field (funct7b0, bit 25) and 8 constraint blocks.
    All base-class fields and constraints are inherited unchanged.
    """

    funct7b0 : zdc.u1 = zdc.rand()   # instr[25] — M-extension discriminator

    @constraint
    def c_extract_funct7b0(self):
        assert self.funct7b0 == ((self.instr >> 25) & 0x1)

    # M-extension instructions: opcode=0x33, funct7b0=1
    # All are R-type: use_rs1=1, use_rs2=1, use_rd=1, imm_sel=IMM_NONE
    # is_load=0, is_store=0, is_branch=0, is_jal=0, is_jalr=0

    @constraint
    def c_mul(self):
        if self.opcode == 0x33 and self.funct3 == 0 and self.funct7b0 == 1:
            assert self.alu_op == ALU_MUL
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_mulh(self):
        if self.opcode == 0x33 and self.funct3 == 1 and self.funct7b0 == 1:
            assert self.alu_op == ALU_MULH
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_mulhsu(self):
        if self.opcode == 0x33 and self.funct3 == 2 and self.funct7b0 == 1:
            assert self.alu_op == ALU_MULHSU
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_mulhu(self):
        if self.opcode == 0x33 and self.funct3 == 3 and self.funct7b0 == 1:
            assert self.alu_op == ALU_MULHU
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_div(self):
        if self.opcode == 0x33 and self.funct3 == 4 and self.funct7b0 == 1:
            assert self.alu_op == ALU_DIV
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_divu(self):
        if self.opcode == 0x33 and self.funct3 == 5 and self.funct7b0 == 1:
            assert self.alu_op == ALU_DIVU
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_rem(self):
        if self.opcode == 0x33 and self.funct3 == 6 and self.funct7b0 == 1:
            assert self.alu_op == ALU_REM
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_remu(self):
        if self.opcode == 0x33 and self.funct3 == 7 and self.funct7b0 == 1:
            assert self.alu_op == ALU_REMU
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0
