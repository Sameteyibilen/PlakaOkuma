"""Desktop layout and truthful connection-state regression checks (requires Tk)."""
import unittest
from app import App


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.app = App()

    def tearDown(self):
        for timer in self.app.tk.call("after", "info"):
            self.app.after_cancel(timer)
        self.app.destroy()

    def test_layout_at_supported_sizes(self):
        for width, height in ((1280, 900), (1600, 1000), (1920, 1080)):
            self.app.geometry(f"{width}x{height}")
            self.app.update_idletasks()
            for widget in (self.app.tree, self.app.btn_solve,
                           self.app.plate_lbl, *self.app.cam_preview.values()):
                self.assertTrue(widget.winfo_ismapped(), (width, str(widget)))
                self.assertGreater(widget.winfo_height(), 15)
                self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                     self.app.winfo_rooty() + self.app.winfo_height())
            self.assertGreater(self.app.tree.winfo_height(), 65)

    def test_gate_requires_live_connection(self):
        self.app._paint_gate(None)
        self.assertIn("DURDURULDU", self.app.gate_title.get())
        self.app.running = True
        self.app._paint_gate(None)
        self.assertIn("BAĞLANTISI", self.app.gate_title.get())
        self.app._scale.update(ok=False, kg=32000, auto_state="SCALE_EMPTY")
        self.app._paint_gate({"gate": "READY_TO_PASS"})
        self.assertIn("BAĞLANTISI", self.app.gate_title.get())

    def test_diagnostics_can_be_reopened(self):
        self.assertEqual(self.app.diagnostics.state(), "withdrawn")
        self.app._show_diagnostics()
        self.app.update_idletasks()
        self.assertEqual(self.app.diagnostics.state(), "normal")

    def test_manual_entry_can_be_opened(self):
        self.app._show_manual_irs()
        self.app.update_idletasks()
        self.assertTrue(self.app.irs_manual.winfo_ismapped())
        self.assertEqual(self.app.manual_window.state(), "normal")
        self.app.diagnostics.withdraw()
        self.app._show_diagnostics()
        self.assertEqual(self.app.diagnostics.state(), "normal")


if __name__ == "__main__":
    unittest.main()
