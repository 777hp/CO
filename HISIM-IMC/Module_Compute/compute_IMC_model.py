import pandas as pd
from Module_Compute.functions import imc_analy
import csv
from itertools import chain


def _select_leak_values(leakage_mode, e_leak_active_j, e_leak_installed_j, p_leak_active_w, p_leak_installed_w):
    if leakage_mode == "active_only":
        return e_leak_active_j, p_leak_active_w
    if leakage_mode == "installed":
        return e_leak_installed_j, p_leak_installed_w
    raise ValueError(f"Unsupported leakage_mode={leakage_mode}")


def _write_per_chiplet_power_report(
    path,
    n_core_by_stack,
    dyn_energy_by_stack_j,
    total_model_L_s,
    p_leak_tile_w,
    active_leak_energy_by_stack_j,
    leakage_mode,
):
    rows = []
    total_power = 0.0
    with open(path, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            'stack_id', 'n_core', 'E_dyn(pJ)',
            'E_leak_active(pJ)', 'P_leak_active(W)',
            'E_leak_installed(pJ)', 'P_leak_installed(W)',
            'E_leak(pJ)', 'P_leak(W)',
            'P_dyn(W)', 'P_total(W)'
        ])

        for stack_id, n_core in enumerate(n_core_by_stack):
            e_dyn_j = dyn_energy_by_stack_j[stack_id]
            p_dyn_w = (e_dyn_j / total_model_L_s) if total_model_L_s > 0 else 0.0

            e_leak_active_j = active_leak_energy_by_stack_j[stack_id]
            p_leak_active_w = (e_leak_active_j / total_model_L_s) if total_model_L_s > 0 else 0.0

            p_leak_installed_w = p_leak_tile_w * n_core
            e_leak_installed_j = p_leak_installed_w * total_model_L_s

            e_leak_sel_j, p_leak_sel_w = _select_leak_values(
                leakage_mode,
                e_leak_active_j,
                e_leak_installed_j,
                p_leak_active_w,
                p_leak_installed_w,
            )

            p_total_w = p_dyn_w + p_leak_sel_w
            total_power += p_total_w

            row = {
                'stack_id': stack_id,
                'n_core': int(n_core),
                'E_dyn_pJ': e_dyn_j * 1e12,
                'E_leak_active_pJ': e_leak_active_j * 1e12,
                'P_leak_active_W': p_leak_active_w,
                'E_leak_installed_pJ': e_leak_installed_j * 1e12,
                'P_leak_installed_W': p_leak_installed_w,
                'E_leak_pJ': e_leak_sel_j * 1e12,
                'P_leak_W': p_leak_sel_w,
                'P_dyn_W': p_dyn_w,
                'P_total_W': p_total_w,
            }
            rows.append(row)

            writer.writerow([
                row['stack_id'], row['n_core'], row['E_dyn_pJ'],
                row['E_leak_active_pJ'], row['P_leak_active_W'],
                row['E_leak_installed_pJ'], row['P_leak_installed_W'],
                row['E_leak_pJ'], row['P_leak_W'],
                row['P_dyn_W'], row['P_total_W'],
            ])
    return rows, total_power


def compute_IMC_model(
    COMPUTE_VALIDATE,
    xbar_size,
    volt,
    freq_computing,
    quant_act,
    quant_weight,
    N_crossbar,
    N_pe,
    N_tier_real,
    N_stack_real,
    N_tile,
    result_list,
    result_dictionary,
    network_params,
    relu,
    n_core_by_stack=None,
    chiplet_defined_area_mm2=None,
    tile_area_mode="legacy_last",
    leakage_mode="active_only",
):
    # Initialize variables
    total_model_L = 0
    total_model_E_dynamic = 0
    out_peripherial = []
    layer_dynamic_energy = []
    layer_latency = []
    layer_tiles_used = []
    legacy_tile_area_candidates = []

    # Obtain layer information from the csv file
    computing_inform = "./Debug/to_interconnect_analy/layer_inform.csv"
    computing_data = pd.read_csv(computing_inform, header=None).to_numpy()

    filename = "./Debug/to_interconnect_analy/layer_performance.csv"
    freq_adc = 0.005 if COMPUTE_VALIDATE else freq_computing
    imc_analy_fn = imc_analy(
        xbar_size=xbar_size,
        volt=volt,
        freq=freq_computing,
        freq_adc=freq_adc,
        compute_ref=COMPUTE_VALIDATE,
        quant_bits=[quant_weight, quant_act],
        RELU=relu,
    )

    # Default fallback for non-2.5D or legacy callers
    if n_core_by_stack is None:
        n_core_by_stack = [N_tile for _ in range(N_stack_real)]

    dyn_energy_by_stack = [0.0 for _ in range(len(n_core_by_stack))]
    active_leak_energy_by_stack = [0.0 for _ in range(len(n_core_by_stack))]
    p_leak_tile = imc_analy_fn.leakage(N_crossbar, N_pe)

    # write the layer performance data to csv file
    with open(filename, 'w') as csvfile1:
        for layer_idx in range(len(computing_data)):
            A_pe, L_layer, E_layer, peripherials, A_peri = imc_analy_fn.forward(computing_data, layer_idx, network_params)
            total_model_L += L_layer
            total_model_E_dynamic += E_layer
            layer_dynamic_energy.append(E_layer)
            layer_latency.append(L_layer)
            tiles_used = float(computing_data[layer_idx][1])
            layer_tiles_used.append(tiles_used)

            legacy_tile_area_candidates.append(A_pe * N_pe * N_crossbar)

            stack_idx = int(computing_data[layer_idx][-1])
            if 0 <= stack_idx < len(dyn_energy_by_stack):
                dyn_energy_by_stack[stack_idx] += E_layer
                active_leak_energy_by_stack[stack_idx] += p_leak_tile * L_layer * tiles_used

            # CSV format kept backward-compatible
            csvfile1.write(
                str(layer_idx) + "," + str(computing_data[layer_idx][1]) + "," + str(L_layer) + "," + str(E_layer) + "," + str(p_leak_tile) + "," + str('%.3f' % (E_layer / L_layer * 1000 / computing_data[layer_idx][1]))
            )
            csvfile1.write('\n')

            if COMPUTE_VALIDATE:
                if len(out_peripherial) == 0:
                    out_peripherial.append(peripherials)
                    out_peripherial = list(chain.from_iterable(out_peripherial))
                else:
                    for i in range(len(peripherials)):
                        out_peripherial[i] += peripherials[i]

    area_single_tile_legacy_last = legacy_tile_area_candidates[-1] if legacy_tile_area_candidates else 0.0
    if hasattr(imc_analy_fn, 'area_per_core'):
        area_single_tile_hw = imc_analy_fn.area_per_core(N_crossbar, N_pe)
    else:
        # fallback estimate matches current implementation assumption
        area_single_tile_hw = area_single_tile_legacy_last

    total_leak_active = sum(active_leak_energy_by_stack)
    total_leak_installed = p_leak_tile * total_model_L * sum(n_core_by_stack)

    if tile_area_mode == "legacy_last":
        area_single_tile = area_single_tile_legacy_last
    elif tile_area_mode == "hardware_only":
        area_single_tile = area_single_tile_hw
    else:
        raise ValueError(f"Unsupported tile_area_mode={tile_area_mode}")

    if leakage_mode == "active_only":
        total_leakage = total_leak_active
    elif leakage_mode == "installed":
        total_leakage = total_leak_installed
    else:
        raise ValueError(f"Unsupported leakage_mode={leakage_mode}")

    per_chiplet_rows, total_compute_power = _write_per_chiplet_power_report(
        './Results/PPA_per_chiplet.csv',
        n_core_by_stack,
        dyn_energy_by_stack,
        total_model_L,
        p_leak_tile,
        active_leak_energy_by_stack,
        leakage_mode,
    )

    print("----------computing performance results-----------------")
    print("--------------------------------------------------------")
    print("Total compute latency", round(total_model_L * pow(10, 9), 5), "ns")
    print("Total dynamic energy", round(total_model_E_dynamic * pow(10, 12), 5), "pJ")
    print("Overall compute Power", round(total_model_E_dynamic / (total_model_L), 5), "W")
    print("Total Leakage energy", round(total_leakage * pow(10, 12), 5), "pJ")

    result_list.append(total_model_L * pow(10, 9))
    result_list.append(total_model_E_dynamic * pow(10, 12))

    #-----------------------------------#
    #         Computing Area            #
    #-----------------------------------#
    tile_count = sum(n_core_by_stack) if n_core_by_stack is not None else (N_stack_real * N_tier_real * N_tile)
    total_tiles_area = tile_count * area_single_tile
    print("Total tiles area", round(total_tiles_area, 5), "mm2")
    print("Total tiles area each tier,", round(total_tiles_area / max(1, N_stack_real) / max(1, N_tier_real), 5), "mm2")
    result_list.append(total_tiles_area * pow(10, 6))

    result_dictionary['Computing_latency (ns)'] = total_model_L * pow(10, 9)
    result_dictionary['Computing_energy (pJ)'] = total_model_E_dynamic * pow(10, 12)
    result_dictionary['compute_area (um2)'] = total_tiles_area * pow(10, 6)
    result_dictionary['compute_power_total (W)'] = total_compute_power

    result_dictionary['area_single_tile_legacy_last_mm2'] = area_single_tile_legacy_last
    result_dictionary['area_single_tile_hw_mm2'] = area_single_tile_hw
    result_dictionary['core_required_area_mm2'] = (sum(n_core_by_stack) * area_single_tile_hw) if n_core_by_stack is not None else total_tiles_area
    if chiplet_defined_area_mm2 is not None:
        result_dictionary['chiplet_defined_area_mm2'] = chiplet_defined_area_mm2

    result_dictionary['total_leakage_active_only_pJ'] = total_leak_active * 1e12
    result_dictionary['total_leakage_installed_pJ'] = total_leak_installed * 1e12
    result_dictionary['Total_Leakage_energy(pJ)'] = total_leakage * 1e12
    result_dictionary['tile_area_mode'] = tile_area_mode
    result_dictionary['leakage_mode'] = leakage_mode

    return N_tier_real, computing_data, area_single_tile, volt, total_model_L, result_list, out_peripherial, A_peri


def _self_test_leakage_dual_modes():
    leak_tile = 1e-3
    L_layer = [1e-6, 2e-6]
    tiles_used = [10, 20]
    stack_idx = [0, 1]
    n_core_by_stack = [50, 200]

    total_L = sum(L_layer)
    active_by_stack = [0.0, 0.0]
    for i in range(len(L_layer)):
        active_by_stack[stack_idx[i]] += leak_tile * L_layer[i] * tiles_used[i]

    total_leak_active = sum(active_by_stack)
    total_leak_installed = leak_tile * sum(n_core_by_stack) * total_L

    print('[self-test] total_leak_active(J)=', total_leak_active)
    print('[self-test] total_leak_installed(J)=', total_leak_installed)
    assert total_leak_installed > total_leak_active


if __name__ == '__main__':
    _self_test_leakage_dual_modes()
