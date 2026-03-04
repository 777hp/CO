import os
import sys
import tempfile
import csv

HISIM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if HISIM_ROOT not in sys.path:
    sys.path.append(HISIM_ROOT)

from Module_Compute.compute_IMC_model import _write_per_chiplet_power_report


def main():
    with tempfile.TemporaryDirectory() as td:
        out_csv = os.path.join(td, 'PPA_per_chiplet.csv')
        n_core_by_stack = [50, 200]
        # same dynamic energy for both stacks
        dyn_energy_by_stack_j = [1e-6, 1e-6]
        total_model_L_s = 1e-3
        p_leak_tile_w = 1e-3

        active_leak_energy_by_stack_j = [2e-8, 3e-8]
        rows, total_power = _write_per_chiplet_power_report(
            out_csv,
            n_core_by_stack,
            dyn_energy_by_stack_j,
            total_model_L_s,
            p_leak_tile_w,
            active_leak_energy_by_stack_j,
            'active_only',
        )

        assert rows[0]['P_leak_W'] < rows[1]['P_leak_W']
        assert rows[0]['E_dyn_pJ'] == rows[1]['E_dyn_pJ']

        with open(out_csv, 'r', encoding='utf-8') as f:
            reader = list(csv.reader(f))
        assert len(reader) == 3
        print('rows:', rows)
        print('total_power:', total_power)


if __name__ == '__main__':
    main()
