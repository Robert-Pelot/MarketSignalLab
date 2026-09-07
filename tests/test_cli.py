import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from market_signal_lab.cli import main


class CommandLineTests(unittest.TestCase):
    def test_demo_runs_and_writes_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "analysis.csv"
            captured = io.StringIO()
            with redirect_stdout(captured):
                status = main(["demo", "--rows", "100", "--output", str(output)])
            self.assertEqual(status, 0)
            self.assertTrue(output.is_file())
            self.assertIn("Educational use only", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
