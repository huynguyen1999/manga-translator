import argparse
from pathlib import Path
import tempfile
from urllib.parse import quote
import unittest

from server.args import dir_path, file_path, parse_arguments, path, url_decode


class TestServerArgs(unittest.TestCase):
    def test_shared_path_converters(self):
        with tempfile.TemporaryDirectory() as root:
            existing_file = Path(root) / "page 1.png"
            existing_file.touch()
            file_url = f"file://{quote(str(existing_file))}"

            self.assertEqual(url_decode(file_url), str(existing_file))
            self.assertEqual(path(file_url), str(existing_file))
            self.assertEqual(file_path(file_url), str(existing_file))
            self.assertEqual(dir_path(root), root)
            self.assertEqual(path(""), "")
            self.assertEqual(file_path(""), "")
            self.assertEqual(dir_path(""), "")
            with self.assertRaisesRegex(argparse.ArgumentTypeError, "No such file:"):
                file_path(str(Path(root) / "missing.png"))

    def test_default_arguments(self):
        args = parse_arguments([])
        self.assertEqual(args.host, '0.0.0.0')
        self.assertEqual(args.port, 8000)
        self.assertEqual(args.workers, 3)
        self.assertTrue(args.use_gpu)
        self.assertFalse(args.use_gpu_limited)
        self.assertEqual(args.executor_mode, 'inprocess')
        self.assertIsNone(args.cpu_stage_workers)

    def test_explicit_use_gpu(self):
        args = parse_arguments(['--use-gpu'])
        self.assertTrue(args.use_gpu)
        self.assertFalse(args.use_gpu_limited)

    def test_no_gpu(self):
        args = parse_arguments(['--no-gpu'])
        self.assertFalse(args.use_gpu)
        self.assertFalse(args.use_gpu_limited)

    def test_no_use_gpu_alias(self):
        args = parse_arguments(['--no-use-gpu'])
        self.assertFalse(args.use_gpu)
        self.assertFalse(args.use_gpu_limited)

    def test_use_gpu_limited(self):
        args = parse_arguments(['--use-gpu-limited'])
        self.assertFalse(args.use_gpu)
        self.assertTrue(args.use_gpu_limited)

    def test_custom_host_and_workers(self):
        args = parse_arguments(['--host', '127.0.0.1', '--workers', '5'])
        self.assertEqual(args.host, '127.0.0.1')
        self.assertEqual(args.workers, 5)

    def test_cpu_stage_worker_budget(self):
        args = parse_arguments(['--workers', '5', '--cpu-stage-workers', '4'])
        self.assertEqual(args.cpu_stage_workers, 4)
        self.assertIsNone(parse_arguments(['--cpu-stage-workers', 'auto']).cpu_stage_workers)
        with self.assertRaises(SystemExit):
            parse_arguments(['--cpu-stage-workers', '0'])

    def test_mutually_exclusive_gpu_flags(self):
        with self.assertRaises(SystemExit):
            parse_arguments(['--use-gpu', '--use-gpu-limited'])
        with self.assertRaises(SystemExit):
            parse_arguments(['--no-gpu', '--use-gpu-limited'])
        with self.assertRaises(SystemExit):
            parse_arguments(['--use-gpu', '--no-gpu'])


if __name__ == '__main__':
    unittest.main()
