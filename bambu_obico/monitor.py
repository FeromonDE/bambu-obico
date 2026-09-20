import json
import logging

from .config import load_config
from .connection import BambuConn


VISIBLE_FIELDS = (
    "gcode_state",
    "mc_percent",
    "mc_remaining_time",
    "layer_num",
    "total_layer_num",
    "nozzle_temper",
    "nozzle_target_temper",
    "bed_temper",
    "bed_target_temper",
    "subtask_name",
    "print_type",
    "stg_cur",
)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    last = {}

    def on_state(state):
        current = {key: state.get(key) for key in VISIBLE_FIELDS if key in state}
        changed = {key: value for key, value in current.items() if last.get(key) != value}
        if changed:
            print(json.dumps(changed, ensure_ascii=False, sort_keys=True), flush=True)
        last.clear()
        last.update(current)

    BambuConn(load_config(), on_state=on_state).run_forever()


if __name__ == "__main__":
    main()
