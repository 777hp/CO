from pathlib import Path

from hisim_model import HiSimModel


def _set_floorplan_path(hisim, floorplan_path: str) -> None:
    setter_names = ("set_chiplet_floorplan_path", "set_floorplan")
    for name in setter_names:
        setter = getattr(hisim, name, None)
        if callable(setter):
            setter(floorplan_path)
            return
    hisim.chiplet_floorplan_path = floorplan_path


def _set_scheme2_enabled(hisim, enabled: bool = True) -> None:
    setter = getattr(hisim, "set_use_rapidchiplet_scheme2", None)
    if callable(setter):
        setter(enabled)
        return
    hisim.use_rapidchiplet_scheme2 = enabled


def main() -> None:
    hisim = HiSimModel(
        chip_architect="H2_5D",
        xbar_size=1024,
        N_tile=81,
        N_pe=36,
        N_tier=1,
        placement_method=1,
        N_stack=2,
        ai_model="densenet121",
    )

    floorplan_path = str(Path(__file__).resolve().parents[1] / "chiplet_floorplan.json")
    _set_floorplan_path(hisim, floorplan_path)
    _set_scheme2_enabled(hisim, True)

    hisim.set_N_tier(1)
    hisim.set_placement(1)
    hisim.set_N_stack(2)
    hisim.set_num_pe(36)
    hisim.set_N_tile(81)
    hisim.set_ai_model("densenet121")

    hisim.run_model()


if __name__ == "__main__":
    main()
