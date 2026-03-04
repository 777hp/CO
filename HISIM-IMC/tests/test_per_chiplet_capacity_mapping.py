import os
import sys
import tempfile
import numpy as np

HISIM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if HISIM_ROOT not in sys.path:
    sys.path.append(HISIM_ROOT)

from Module_AI_Map.util_chip.util_mapping import model_mapping


def build_network(num_layers=3):
    # [in_x, in_y, in_channel, k_x, k_y, out_channel, enable_pooling, density]
    row = [1, 1, 128, 1, 1, 480, 0, 1]
    return np.array([row for _ in range(num_layers)], dtype=int)


def read_stack_assignments(csv_path):
    stacks = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        for line in f:
            cols = line.strip().split(',')
            if len(cols) >= 16:
                stacks.append(int(cols[15]))
    return stacks


def main():
    network_params = build_network(3)
    with tempfile.TemporaryDirectory() as td:
        out_csv = os.path.join(td, 'layer_inform.csv')
        _ = model_mapping(
            out_csv,
            placement_method=1,
            network_params=network_params,
            quant_act=8,
            xbar_size=128,
            N_crossbar=1,
            N_pe=1,
            quant_weight=8,
            N_tile=[50, 200],
            N_tier=1,
            N_stack=2,
        )
        stacks = read_stack_assignments(out_csv)
        print('stack assignments with n_core_by_stack=[50,200]:', stacks)
        # Each layer needs 30 tiles. stack0 capacity=50, so layer1 should already move to stack1.
        assert stacks[0] == 0
        assert stacks[1] == 1


if __name__ == '__main__':
    main()
