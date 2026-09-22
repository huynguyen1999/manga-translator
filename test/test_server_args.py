import unittest
from server.args import parse_arguments


class TestServerArgs(unittest.TestCase):
    def test_default_arguments(self):
        args = parse_arguments([])
        self.assertEqual(args.host, '0.0.0.0')
        self.assertEqual(args.port, 8000)
        self.assertEqual(args.workers, 3)
        self.assertTrue(args.use_gpu)
        self.assertFalse(args.use_gpu_limited)
        self.assertEqual(args.executor_mode, 'inprocess')

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

    def test_mutually_exclusive_gpu_flags(self):
        with self.assertRaises(SystemExit):
            parse_arguments(['--use-gpu', '--use-gpu-limited'])
        with self.assertRaises(SystemExit):
            parse_arguments(['--no-gpu', '--use-gpu-limited'])
        with self.assertRaises(SystemExit):
            parse_arguments(['--use-gpu', '--no-gpu'])


if __name__ == '__main__':
    unittest.main()
