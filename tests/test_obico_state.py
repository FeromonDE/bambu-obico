import unittest

from bambu_obico.obico_state import to_obico_status


class ObicoStateTests(unittest.TestCase):
    def test_finished_snapshot(self):
        status = to_obico_status({
            "gcode_state": "FINISH",
            "mc_percent": 100,
            "mc_remaining_time": 0,
            "layer_num": 90,
            "total_layer_num": 90,
            "nozzle_temper": 25.09375,
            "nozzle_target_temper": 0,
            "bed_temper": 26.15625,
            "bed_target_temper": 0,
            "subtask_name": "Shelly_Mini_DIN_Mount.gcode.3mf",
        })
        self.assertEqual(status["state"]["text"], "Operational")
        self.assertFalse(status["state"]["flags"]["printing"])
        self.assertEqual(status["progress"]["completion"], 100)
        self.assertEqual(status["progress"]["printTimeLeft"], 0)
        self.assertEqual(status["currentLayerHeight"], 90)
        self.assertEqual(status["file_metadata"]["obico"]["totalLayerCount"], 90)
        self.assertEqual(status["temperatures"]["tool0"]["actual"], 25.09)
        self.assertEqual(status["job"]["file"]["name"], "Shelly_Mini_DIN_Mount.gcode.3mf")

    def test_running_snapshot(self):
        status = to_obico_status({
            "gcode_state": "RUNNING",
            "mc_percent": 42,
            "mc_remaining_time": 17,
        })
        self.assertEqual(status["state"]["text"], "Printing")
        self.assertTrue(status["state"]["flags"]["printing"])
        self.assertEqual(status["progress"]["completion"], 42)
        self.assertEqual(status["progress"]["printTimeLeft"], 1020)


if __name__ == "__main__":
    unittest.main()
