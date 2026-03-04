import os
import sys

HISIM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if HISIM_ROOT not in sys.path:
    sys.path.append(HISIM_ROOT)

from hisim_model import _validate_area_feasibility


def main():
    floorplan = {
        'chiplets': [
            {'stack_id': 0, 'w': 1.0, 'h': 1.0},
            {'stack_id': 1, 'w': 100.0, 'h': 100.0},
        ]
    }
    n_core = [1000, 10]
    area_single_core_mm2 = 0.1

    got_error = False
    try:
        _validate_area_feasibility(n_core, floorplan, area_single_core_mm2, packing_efficiency=0.85, area_violation_policy='error')
    except ValueError as exc:
        got_error = True
        print('caught expected error:', exc)
    assert got_error

    clipped, report = _validate_area_feasibility(n_core, floorplan, area_single_core_mm2, packing_efficiency=0.85, area_violation_policy='clip')
    assert clipped[0] == 8
    assert clipped[1] == 10
    print('clip result:', clipped)
    print('report:', report)


if __name__ == '__main__':
    main()
