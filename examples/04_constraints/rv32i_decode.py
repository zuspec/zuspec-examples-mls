"""Example 04 — Constraint-based RV32I instruction decode.

Teaching points
---------------
1. **One block per instruction.**  Each of the 37 RV32I base instructions is
   described by a single @constraint block.  There is no hand-built if/else
   tree or case statement.

2. **Composable and extensible.**  Adding a new instruction means adding one
   block.  No existing block needs to change.  See rv32m_decode.py for the
   M-extension demonstration.

3. **Free synthesis.**  The constraint blocks are the specification.  The
   zuspec-synth ConstraintCompiler derives an optimized SOP cover automatically
   using Quine-McCluskey minimization with common-subexpression elimination.
   The result is typically better than what a human would write on first pass.

Instruction encoding summary (the only bits that matter for decode)
-------------------------------------------------------------------
  instr[6:0]   opcode  — instruction family
  instr[14:12] funct3  — operation selector within family
  instr[30]    funct7b5 — arithmetic/logic selector (R-type and shift immediates)
"""
import zuspec.dataclasses as zdc
from zuspec.dataclasses import constraint

from rv32_core import RV32Core

# ---------------------------------------------------------------------------
# ALU operation encoding (4 bits)
# ---------------------------------------------------------------------------
ALU_ADD  = 0   # addition / address calculation
ALU_SUB  = 1   # subtraction
ALU_SLL  = 2   # shift left logical
ALU_SLT  = 3   # set-less-than (signed)
ALU_SLTU = 4   # set-less-than (unsigned)
ALU_XOR  = 5   # exclusive-OR
ALU_SRL  = 6   # shift right logical
ALU_SRA  = 7   # shift right arithmetic
ALU_OR   = 8   # bitwise OR
ALU_AND  = 9   # bitwise AND
ALU_PASS = 10  # pass immediate (LUI/AUIPC)

# ---------------------------------------------------------------------------
# Immediate format encoding (3 bits)
# ---------------------------------------------------------------------------
IMM_NONE = 0   # no immediate
IMM_I    = 1   # I-type  (sign-extended 12-bit)
IMM_S    = 2   # S-type  (store offset)
IMM_B    = 3   # B-type  (branch offset)
IMM_U    = 4   # U-type  (upper 20 bits)
IMM_J    = 5   # J-type  (JAL offset)


# ---------------------------------------------------------------------------
# RV32IDecode — the decode action
# ---------------------------------------------------------------------------

@zdc.dataclass
class RV32IDecode(zdc.Action[RV32Core]):
    """Constraint-based RV32I instruction decoder.

    The single input is the raw 32-bit instruction word.  All decode outputs
    are rand fields whose values are determined entirely by the constraint
    blocks below.
    """

    # ------------------------------------------------------------------
    # Primary input — the raw instruction word, passed in by the caller.
    # Marked zdc.input() so the synthesizer recognises it as a hardware port.
    # ------------------------------------------------------------------
    instr     : zdc.u32 = zdc.input()

    # ------------------------------------------------------------------
    # Internal decode-relevant sub-fields.
    # These are constrained to be bit-slices of instr.  They exist so that
    # the per-instruction constraints can read opcode/funct3/funct7b5 by name
    # rather than repeating the bit-slice arithmetic in every block.
    # The synthesizer's pre-pass recognises the extraction pattern and maps
    # "self.opcode == X" guards to the corresponding bit-range of instr.
    # ------------------------------------------------------------------
    opcode    : zdc.u7  = zdc.rand()   # instr[6:0]
    funct3    : zdc.u3  = zdc.rand()   # instr[14:12]
    funct7b5  : zdc.u1  = zdc.rand()   # instr[30]

    # ------------------------------------------------------------------
    # Decode outputs — solved by constraints; synthesized to wire assigns.
    # ------------------------------------------------------------------
    alu_op    : zdc.u4  = zdc.rand()   # ALU_* constant above
    imm_sel   : zdc.u3  = zdc.rand()   # IMM_* constant above
    use_rs1   : zdc.u1  = zdc.rand()   # instruction reads register rs1
    use_rs2   : zdc.u1  = zdc.rand()   # instruction reads register rs2
    use_rd    : zdc.u1  = zdc.rand()   # instruction writes register rd
    is_load   : zdc.u1  = zdc.rand()   # instruction is a load
    is_store  : zdc.u1  = zdc.rand()   # instruction is a store
    is_branch : zdc.u1  = zdc.rand()   # instruction is a conditional branch
    is_jal    : zdc.u1  = zdc.rand()   # instruction is JAL
    is_jalr   : zdc.u1  = zdc.rand()   # instruction is JALR
    mem_width : zdc.u2  = zdc.rand()   # 0=byte, 1=half-word, 2=word
    mem_signed: zdc.u1  = zdc.rand()   # 1 = sign-extend the loaded value

    # ==================================================================
    # Field extraction — bind derived fields to bit-slices of instr.
    # The synthesizer emits these as plain wire assigns; they do not add
    # any logic.
    # ==================================================================
    @constraint
    def c_extract_fields(self):
        assert self.opcode   == (self.instr & 0x7F)
        assert self.funct3   == ((self.instr >> 12) & 0x7)
        assert self.funct7b5 == ((self.instr >> 30) & 0x1)

    # ==================================================================
    # R-type instructions — opcode = 0x33
    # use_rs1=1, use_rs2=1, use_rd=1, imm_sel=IMM_NONE
    # is_load=0, is_store=0, is_branch=0, is_jal=0, is_jalr=0
    # ==================================================================

    @constraint
    def c_add(self):
        if self.opcode == 0x33 and self.funct3 == 0 and self.funct7b5 == 0:
            assert self.alu_op == ALU_ADD
            assert self.imm_sel == IMM_NONE
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_sub(self):
        if self.opcode == 0x33 and self.funct3 == 0 and self.funct7b5 == 1:
            assert self.alu_op == ALU_SUB
            assert self.imm_sel == IMM_NONE
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_sll(self):
        if self.opcode == 0x33 and self.funct3 == 1 and self.funct7b5 == 0:
            assert self.alu_op == ALU_SLL
            assert self.imm_sel == IMM_NONE
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_slt(self):
        if self.opcode == 0x33 and self.funct3 == 2 and self.funct7b5 == 0:
            assert self.alu_op == ALU_SLT
            assert self.imm_sel == IMM_NONE
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_sltu(self):
        if self.opcode == 0x33 and self.funct3 == 3 and self.funct7b5 == 0:
            assert self.alu_op == ALU_SLTU
            assert self.imm_sel == IMM_NONE
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_xor(self):
        if self.opcode == 0x33 and self.funct3 == 4 and self.funct7b5 == 0:
            assert self.alu_op == ALU_XOR
            assert self.imm_sel == IMM_NONE
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_srl(self):
        if self.opcode == 0x33 and self.funct3 == 5 and self.funct7b5 == 0:
            assert self.alu_op == ALU_SRL
            assert self.imm_sel == IMM_NONE
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_sra(self):
        if self.opcode == 0x33 and self.funct3 == 5 and self.funct7b5 == 1:
            assert self.alu_op == ALU_SRA
            assert self.imm_sel == IMM_NONE
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_or(self):
        if self.opcode == 0x33 and self.funct3 == 6 and self.funct7b5 == 0:
            assert self.alu_op == ALU_OR
            assert self.imm_sel == IMM_NONE
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_and(self):
        if self.opcode == 0x33 and self.funct3 == 7 and self.funct7b5 == 0:
            assert self.alu_op == ALU_AND
            assert self.imm_sel == IMM_NONE
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    # ==================================================================
    # I-type ALU instructions — opcode = 0x13
    # use_rs1=1, use_rs2=0, use_rd=1, imm_sel=IMM_I
    # is_load=0, is_store=0, is_branch=0, is_jal=0, is_jalr=0
    # Note: funct7b5 is a don't-care for all except SLLI/SRLI/SRAI.
    # ==================================================================

    @constraint
    def c_addi(self):
        if self.opcode == 0x13 and self.funct3 == 0:
            assert self.alu_op == ALU_ADD
            assert self.imm_sel == IMM_I
            assert self.use_rs1 == 1
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_slti(self):
        if self.opcode == 0x13 and self.funct3 == 2:
            assert self.alu_op == ALU_SLT
            assert self.imm_sel == IMM_I
            assert self.use_rs1 == 1
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_sltiu(self):
        if self.opcode == 0x13 and self.funct3 == 3:
            assert self.alu_op == ALU_SLTU
            assert self.imm_sel == IMM_I
            assert self.use_rs1 == 1
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_xori(self):
        if self.opcode == 0x13 and self.funct3 == 4:
            assert self.alu_op == ALU_XOR
            assert self.imm_sel == IMM_I
            assert self.use_rs1 == 1
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_ori(self):
        if self.opcode == 0x13 and self.funct3 == 6:
            assert self.alu_op == ALU_OR
            assert self.imm_sel == IMM_I
            assert self.use_rs1 == 1
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_andi(self):
        if self.opcode == 0x13 and self.funct3 == 7:
            assert self.alu_op == ALU_AND
            assert self.imm_sel == IMM_I
            assert self.use_rs1 == 1
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_slli(self):
        if self.opcode == 0x13 and self.funct3 == 1 and self.funct7b5 == 0:
            assert self.alu_op == ALU_SLL
            assert self.imm_sel == IMM_I
            assert self.use_rs1 == 1
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_srli(self):
        if self.opcode == 0x13 and self.funct3 == 5 and self.funct7b5 == 0:
            assert self.alu_op == ALU_SRL
            assert self.imm_sel == IMM_I
            assert self.use_rs1 == 1
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    @constraint
    def c_srai(self):
        if self.opcode == 0x13 and self.funct3 == 5 and self.funct7b5 == 1:
            assert self.alu_op == ALU_SRA
            assert self.imm_sel == IMM_I
            assert self.use_rs1 == 1
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0

    # ==================================================================
    # Load instructions — opcode = 0x03
    # alu_op=ADD (address = rs1 + imm), use_rs1=1, use_rs2=0, use_rd=1
    # imm_sel=IMM_I, is_load=1, is_store=0, is_branch=0, is_jal=0, is_jalr=0
    # ==================================================================

    @constraint
    def c_lb(self):
        if self.opcode == 0x03 and self.funct3 == 0:
            assert self.alu_op == ALU_ADD
            assert self.imm_sel == IMM_I
            assert self.use_rs1 == 1
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 1
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 0
            assert self.mem_signed == 1

    @constraint
    def c_lh(self):
        if self.opcode == 0x03 and self.funct3 == 1:
            assert self.alu_op == ALU_ADD
            assert self.imm_sel == IMM_I
            assert self.use_rs1 == 1
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 1
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 1
            assert self.mem_signed == 1

    @constraint
    def c_lw(self):
        if self.opcode == 0x03 and self.funct3 == 2:
            assert self.alu_op == ALU_ADD
            assert self.imm_sel == IMM_I
            assert self.use_rs1 == 1
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 1
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 2
            assert self.mem_signed == 0  # full 32-bit word, no sign extension needed

    @constraint
    def c_lbu(self):
        if self.opcode == 0x03 and self.funct3 == 4:
            assert self.alu_op == ALU_ADD
            assert self.imm_sel == IMM_I
            assert self.use_rs1 == 1
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 1
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 0
            assert self.mem_signed == 0

    @constraint
    def c_lhu(self):
        if self.opcode == 0x03 and self.funct3 == 5:
            assert self.alu_op == ALU_ADD
            assert self.imm_sel == IMM_I
            assert self.use_rs1 == 1
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 1
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 1
            assert self.mem_signed == 0

    # ==================================================================
    # Store instructions — opcode = 0x23
    # alu_op=ADD (address = rs1 + imm), use_rs1=1, use_rs2=1, use_rd=0
    # imm_sel=IMM_S, is_load=0, is_store=1, is_branch=0, is_jal=0, is_jalr=0
    # mem_signed=0 (stores never sign-extend)
    # ==================================================================

    @constraint
    def c_sb(self):
        if self.opcode == 0x23 and self.funct3 == 0:
            assert self.alu_op == ALU_ADD
            assert self.imm_sel == IMM_S
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 0
            assert self.is_load == 0
            assert self.is_store == 1
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 0
            assert self.mem_signed == 0

    @constraint
    def c_sh(self):
        if self.opcode == 0x23 and self.funct3 == 1:
            assert self.alu_op == ALU_ADD
            assert self.imm_sel == IMM_S
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 0
            assert self.is_load == 0
            assert self.is_store == 1
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 1
            assert self.mem_signed == 0

    @constraint
    def c_sw(self):
        if self.opcode == 0x23 and self.funct3 == 2:
            assert self.alu_op == ALU_ADD
            assert self.imm_sel == IMM_S
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 0
            assert self.is_load == 0
            assert self.is_store == 1
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 2
            assert self.mem_signed == 0

    # ==================================================================
    # Branch instructions — opcode = 0x63
    # use_rs1=1, use_rs2=1, use_rd=0, imm_sel=IMM_B
    # is_load=0, is_store=0, is_branch=1, is_jal=0, is_jalr=0
    # mem_width=0, mem_signed=0
    # The ALU compares the two registers to decide taken/not-taken.
    # ==================================================================

    @constraint
    def c_beq(self):
        if self.opcode == 0x63 and self.funct3 == 0:
            assert self.alu_op == ALU_XOR   # XOR then test == 0
            assert self.imm_sel == IMM_B
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 0
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 1
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 0
            assert self.mem_signed == 0

    @constraint
    def c_bne(self):
        if self.opcode == 0x63 and self.funct3 == 1:
            assert self.alu_op == ALU_XOR   # XOR then test != 0
            assert self.imm_sel == IMM_B
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 0
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 1
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 0
            assert self.mem_signed == 0

    @constraint
    def c_blt(self):
        if self.opcode == 0x63 and self.funct3 == 4:
            assert self.alu_op == ALU_SLT
            assert self.imm_sel == IMM_B
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 0
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 1
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 0
            assert self.mem_signed == 0

    @constraint
    def c_bge(self):
        if self.opcode == 0x63 and self.funct3 == 5:
            assert self.alu_op == ALU_SLT   # taken when SLT == 0
            assert self.imm_sel == IMM_B
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 0
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 1
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 0
            assert self.mem_signed == 0

    @constraint
    def c_bltu(self):
        if self.opcode == 0x63 and self.funct3 == 6:
            assert self.alu_op == ALU_SLTU
            assert self.imm_sel == IMM_B
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 0
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 1
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 0
            assert self.mem_signed == 0

    @constraint
    def c_bgeu(self):
        if self.opcode == 0x63 and self.funct3 == 7:
            assert self.alu_op == ALU_SLTU  # taken when SLTU == 0
            assert self.imm_sel == IMM_B
            assert self.use_rs1 == 1
            assert self.use_rs2 == 1
            assert self.use_rd  == 0
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 1
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 0
            assert self.mem_signed == 0

    # ==================================================================
    # Upper-immediate and jump instructions — each has a unique opcode.
    # is_load=0, is_store=0, is_branch=0, mem_width=0, mem_signed=0
    # ==================================================================

    @constraint
    def c_lui(self):
        if self.opcode == 0x37:
            assert self.alu_op == ALU_PASS  # forward immediate directly to rd
            assert self.imm_sel == IMM_U
            assert self.use_rs1 == 0
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 0
            assert self.mem_signed == 0

    @constraint
    def c_auipc(self):
        if self.opcode == 0x17:
            assert self.alu_op == ALU_ADD   # PC + upper-immediate
            assert self.imm_sel == IMM_U
            assert self.use_rs1 == 0
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 0
            assert self.mem_signed == 0

    @constraint
    def c_jal(self):
        if self.opcode == 0x6F:
            assert self.alu_op == ALU_ADD   # PC + J-imm (link address = PC+4)
            assert self.imm_sel == IMM_J
            assert self.use_rs1 == 0
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 1
            assert self.is_jalr == 0
            assert self.mem_width == 0
            assert self.mem_signed == 0

    @constraint
    def c_jalr(self):
        if self.opcode == 0x67:
            assert self.alu_op == ALU_ADD   # rs1 + I-imm (then clear bit 0)
            assert self.imm_sel == IMM_I
            assert self.use_rs1 == 1
            assert self.use_rs2 == 0
            assert self.use_rd  == 1
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 1
            assert self.mem_width == 0
            assert self.mem_signed == 0

    # ==================================================================
    # Fence / system instructions (FENCE, ECALL, EBREAK) — decode only.
    # These are included for completeness; pipeline handling is separate.
    # ==================================================================

    @constraint
    def c_fence(self):
        if self.opcode == 0x0F and self.funct3 == 0:
            assert self.alu_op == ALU_ADD
            assert self.imm_sel == IMM_NONE
            assert self.use_rs1 == 0
            assert self.use_rs2 == 0
            assert self.use_rd  == 0
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 0
            assert self.mem_signed == 0

    @constraint
    def c_ecall(self):
        if self.opcode == 0x73 and self.funct3 == 0 and self.funct7b5 == 0:
            assert self.alu_op == ALU_ADD
            assert self.imm_sel == IMM_NONE
            assert self.use_rs1 == 0
            assert self.use_rs2 == 0
            assert self.use_rd  == 0
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 0
            assert self.mem_signed == 0

    @constraint
    def c_ebreak(self):
        if self.opcode == 0x73 and self.funct3 == 0 and self.funct7b5 == 1:
            assert self.alu_op == ALU_ADD
            assert self.imm_sel == IMM_NONE
            assert self.use_rs1 == 0
            assert self.use_rs2 == 0
            assert self.use_rd  == 0
            assert self.is_load == 0
            assert self.is_store == 0
            assert self.is_branch == 0
            assert self.is_jal == 0
            assert self.is_jalr == 0
            assert self.mem_width == 0
            assert self.mem_signed == 0

    # ==================================================================
    # No-op body: this action is pure combinational decode.
    # In simulation the constraint solver fills all output fields when
    # 'instr' is set; no procedural logic is needed.
    # ==================================================================

    async def body(self):
        pass
