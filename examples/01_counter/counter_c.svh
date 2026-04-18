
class counter_c extends zsp_component;
    `zsp_component_utils(counter_c)
    zsp_reg_c #(reg[31:0])    count;

    task run();
        forever begin
            count.write(count.read()+1);
        end
    endtask

endclass

