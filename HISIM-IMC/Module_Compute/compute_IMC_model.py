import pandas as pd
from Module_Compute.functions import imc_analy
import csv
from itertools import chain


def _write_per_chiplet_power_report(path, n_core_by_stack, dyn_energy_by_stack_j, total_model_L_s, p_leak_tile_w):
    rows = []
    total_power = 0.0
    with open(path, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['stack_id', 'n_core', 'E_dyn(pJ)', 'E_leak(pJ)', 'P_dyn(W)', 'P_leak(W)', 'P_total(W)'])
        for stack_id, n_core in enumerate(n_core_by_stack):
            e_dyn_j = dyn_energy_by_stack_j[stack_id]
            p_dyn_w = (e_dyn_j / total_model_L_s) if total_model_L_s > 0 else 0.0
            p_leak_w = p_leak_tile_w * n_core
            e_leak_j = p_leak_w * total_model_L_s
            p_total_w = p_dyn_w + p_leak_w
            total_power += p_total_w
            row = {
                'stack_id': stack_id,
                'n_core': int(n_core),
                'E_dyn_pJ': e_dyn_j * 1e12,
                'E_leak_pJ': e_leak_j * 1e12,
                'P_dyn_W': p_dyn_w,
                'P_leak_W': p_leak_w,
                'P_total_W': p_total_w,
            }
            rows.append(row)
            writer.writerow([row['stack_id'], row['n_core'], row['E_dyn_pJ'], row['E_leak_pJ'], row['P_dyn_W'], row['P_leak_W'], row['P_total_W']])
    return rows, total_power


def compute_IMC_model(COMPUTE_VALIDATE,xbar_size,volt, freq_computing,quant_act, quant_weight, N_crossbar,N_pe,N_tier_real,N_stack_real,N_tile,result_list,result_dictionary, network_params, relu, n_core_by_stack=None, chiplet_defined_area_mm2=None):
    #Initialize variables
    total_model_L=0
    total_model_E_dynamic=0
    total_leakage=0
    out_peripherial=[]
    layer_dynamic_energy=[]

    #Obtain layer information from the csv file
    computing_inform = "./Debug/to_interconnect_analy/layer_inform.csv"
    computing_data = pd.read_csv(computing_inform, header=None)
    computing_data = computing_data.to_numpy()

    filename = "./Debug/to_interconnect_analy/layer_performance.csv"
    if COMPUTE_VALIDATE:
        freq_adc=0.005
    else:
        freq_adc=freq_computing
    imc_analy_fn=imc_analy(xbar_size=xbar_size, volt=volt, freq=freq_computing, freq_adc=freq_adc, compute_ref=COMPUTE_VALIDATE, quant_bits=[quant_weight,quant_act], RELU=relu)

    # write the layer performance data to csv file
    with open(filename, 'w') as csvfile1:
        for layer_idx in range(len(computing_data)):
            A_pe, L_layer, E_layer, peripherials, A_peri = imc_analy_fn.forward(computing_data, layer_idx, network_params)
            total_model_L+=L_layer
            total_model_E_dynamic+=E_layer
            layer_dynamic_energy.append(E_layer)
            leak_tile=imc_analy_fn.leakage(N_crossbar,N_pe)
            total_leakage+=leak_tile*L_layer*computing_data[layer_idx][1]

            # CSV file is written in the following format:
            #layer index, number of tiles required for this layer, latency of the layer, Energy of the layer, leakage energy of the layer, average power consumption of each tile for the layer
            csvfile1.write(str(layer_idx)+","+str(computing_data[layer_idx][1])+","+str(L_layer)+","+str(E_layer)+","+str(leak_tile)+","+str('%.3f'% (E_layer/L_layer*1000/computing_data[layer_idx][1])))
            csvfile1.write('\n')

            #Save performance data of peripherials for each layer
            if COMPUTE_VALIDATE:
                if len(out_peripherial)==0:
                    out_peripherial.append(peripherials)
                    out_peripherial=list(chain.from_iterable(out_peripherial))
                else:
                    for i in range(len(peripherials)):
                        out_peripherial[i]+=peripherials[i]

    # Default fallback for non-2.5D or legacy callers
    if n_core_by_stack is None:
        n_core_by_stack = [N_tile for _ in range(N_stack_real)]

    dyn_energy_by_stack = [0.0 for _ in range(len(n_core_by_stack))]
    for layer_idx in range(len(computing_data)):
        stack_idx = int(computing_data[layer_idx][-1])
        E_layer = layer_dynamic_energy[layer_idx]
        if 0 <= stack_idx < len(dyn_energy_by_stack):
            dyn_energy_by_stack[stack_idx] += E_layer

    p_leak_tile = imc_analy_fn.leakage(N_crossbar, N_pe)
    per_chiplet_rows, total_compute_power = _write_per_chiplet_power_report(
        './Results/PPA_per_chiplet.csv',
        n_core_by_stack,
        dyn_energy_by_stack,
        total_model_L,
        p_leak_tile,
    )
    total_leakage = sum((row['E_leak_pJ'] for row in per_chiplet_rows)) * 1e-12

    print("----------computing performance results-----------------")
    print("--------------------------------------------------------")
    print("Total compute latency",round(total_model_L*pow(10,9),5),"ns")
    print("Total dynamic energy",round(total_model_E_dynamic*pow(10,12),5),"pJ")
    print("Overall compute Power",round(total_model_E_dynamic/(total_model_L),5),"W")
    print("Total Leakage energy",round(total_leakage*pow(10,12),5),"pJ")
    result_list.append(total_model_L*pow(10,9))
    result_list.append(total_model_E_dynamic*pow(10,12))

    #-----------------------------------#
    #         Computing Area            #
    #-----------------------------------#
    n_tile_area_factor = sum(n_core_by_stack) if isinstance(N_tile, list) else N_stack_real*N_tier_real*N_tile
    area_single_tile=imc_analy_fn.area_per_core(N_crossbar, N_pe)
    total_tiles_area=n_tile_area_factor*area_single_tile
    print("Total tiles area",round(total_tiles_area,5),"mm2")
    print("Total tiles area each tier,",round(total_tiles_area/max(1, N_stack_real)/max(1, N_tier_real),5),"mm2")
    result_list.append(total_tiles_area*pow(10,6))

    result_dictionary['Computing_latency (ns)'] = total_model_L*pow(10,9)
    result_dictionary['Computing_energy (pJ)'] = total_model_E_dynamic*pow(10,12)
    result_dictionary['compute_area (um2)'] = total_tiles_area*pow(10,6)
    result_dictionary['compute_power_total (W)'] = total_compute_power
    result_dictionary['core_required_area_mm2'] = total_tiles_area
    if chiplet_defined_area_mm2 is not None:
        result_dictionary['chiplet_defined_area_mm2'] = chiplet_defined_area_mm2

    return N_tier_real,computing_data,area_single_tile,volt,total_model_L,result_list,out_peripherial,A_peri
