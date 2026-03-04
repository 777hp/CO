import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
HISIM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.append(ROOT)
if HISIM_ROOT not in sys.path:
    sys.path.append(HISIM_ROOT)

from Module_Network.network_model import compute_2p5d_aib_from_floorplan_and_transfers
from Module_Network.aib_2_5d import aib


def main():
    floorplan = {
        "link_routing": "manhattan",
        "packaging": {
            "link_latency_type": "function",
            "link_latency": "lambda L: 0.01*L + 0.0",
        },
        "chiplets": [
            {"stack_id": 0, "x": 0.0, "y": 0.0, "w": 10.0, "h": 10.0, "rotation": 0},
            {"stack_id": 1, "x": 30.0, "y": 0.0, "w": 10.0, "h": 10.0, "rotation": 0},
            {"stack_id": 2, "x": 90.0, "y": 0.0, "w": 10.0, "h": 10.0, "rotation": 0},
        ],
    }
    transfers = [
        (0, 1, 8e9),
        (1, 2, 8e9),
    ]

    res = compute_2p5d_aib_from_floorplan_and_transfers(transfers, floorplan, volt=0.5, rapidchiplet_module_path=ROOT)
    details = {(item["src_stack"], item["dst_stack"]): item for item in res["details"]}

    l01 = details[(0, 1)]["wire_len_mm"]
    l12 = details[(1, 2)]["wire_len_mm"]
    t01 = details[(0, 1)]["wire_latency_ns"]
    t12 = details[(1, 2)]["wire_latency_ns"]
    a01 = details[(0, 1)]["aib_latency_ns"]
    a12 = details[(1, 2)]["aib_latency_ns"]

    print(f"link 0->1: wire_len_mm={l01:.4f}, wire_latency_ns={t01:.4f}, aib_latency_ns={a01:.4f}")
    print(f"link 1->2: wire_len_mm={l12:.4f}, wire_latency_ns={t12:.4f}, aib_latency_ns={a12:.4f}")

    assert l12 > l01
    assert t12 > t01
    assert a12 >= a01

    compat = aib(1.0, 10.0, 1, 0.5, wire_latency_ns=None)
    assert len(compat) >= 3
    print("compatibility aib call passed")


if __name__ == "__main__":
    main()
