import unittest

from bambu_obico.lifecycle import PrintLifecycle


class PrintLifecycleTests(unittest.TestCase):
    def test_idle_start_does_not_create_fake_print(self):
        life = PrintLifecycle()
        update = life.update({"gcode_state": "FINISH", "mc_percent": 100}, now=100)
        self.assertIsNone(update.message)
        self.assertIsNone(life.current_print_ts)

    def test_start_pause_resume_done(self):
        life = PrintLifecycle()
        life.update({"gcode_state": "FINISH"}, now=100)

        started = life.update({"gcode_state": "RUNNING", "mc_percent": 1}, now=110)
        self.assertEqual(started.transition, "PrintStarted")
        self.assertEqual(started.message["current_print_ts"], 110)

        paused = life.update({"gcode_state": "PAUSE"}, now=120)
        self.assertEqual(paused.transition, "PrintPaused")
        self.assertEqual(paused.message["current_print_ts"], 110)

        resumed = life.update({"gcode_state": "RUNNING"}, now=130)
        self.assertEqual(resumed.transition, "PrintResumed")
        self.assertEqual(resumed.message["current_print_ts"], 110)

        done = life.update({"gcode_state": "FINISH", "mc_percent": 100}, now=140)
        self.assertEqual(done.transition, "PrintDone")
        self.assertEqual(done.message["current_print_ts"], 110)
        self.assertIsNone(life.current_print_ts)

    def test_restart_mid_print_does_not_invent_started_event(self):
        life = PrintLifecycle()
        update = life.update({"gcode_state": "RUNNING"}, now=200)
        self.assertIsNone(update.transition)
        self.assertEqual(update.message["current_print_ts"], 200)


if __name__ == "__main__":
    unittest.main()
