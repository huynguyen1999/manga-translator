import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from server import worker_runtime


class WorkerRuntimeTest(unittest.TestCase):
    def test_start_process_preserves_command_environment_and_registration(self):
        proc = object()
        registrations = []
        params = SimpleNamespace(
            use_gpu=True,
            use_gpu_limited=False,
            ignore_errors=True,
            verbose=True,
            models_ttl=60,
            pre_dict="pre.csv",
            post_dict="post.csv",
        )

        with patch.dict(os.environ, {}, clear=True), patch(
            "server.worker_runtime.subprocess.Popen", return_value=proc
        ) as popen:
            result = worker_runtime.start_translator_client_proc(
                "127.0.0.1",
                8001,
                "nonce",
                params,
                worker_id=2,
                gpu_id="3",
                result_root=Path("/results"),
                register_executor=registrations.append,
            )

        self.assertIs(result, proc)
        cmd = popen.call_args.args[0]
        cwd = popen.call_args.kwargs["cwd"]
        env = popen.call_args.kwargs["env"]
        self.assertEqual(
            cmd,
            [
                os.sys.executable,
                "-m", "manga_translator", "shared",
                "--host", "127.0.0.1", "--port", "8001", "--nonce", "nonce",
                "--use-gpu", "--ignore-errors", "--verbose", "--models-ttl=60",
                "--pre-dict", "pre.csv", "--post-dict", "post.csv",
            ],
        )
        self.assertEqual(cwd, str(Path(worker_runtime.__file__).resolve().parent.parent))
        self.assertEqual(env["MANGA_RESULT_ROOT"], "/results")
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "3")
        self.assertEqual(env["OMP_NUM_THREADS"], "4")
        self.assertEqual(len(registrations), 1)
        self.assertEqual(registrations[0].worker_id, 2)

    def test_setup_subprocess_workers_keeps_worker_order_and_supervisor(self):
        args = SimpleNamespace(port=8000, host="0.0.0.0", gpu_ids="2, 5")
        procs = [object(), object()]
        worker_procs = []
        start_worker = Mock(side_effect=procs)
        supervise = Mock()

        with patch("server.worker_runtime.threading.Thread") as thread, patch(
            "server.worker_runtime.signal.signal"
        ) as signal:
            result = worker_runtime.setup_subprocess_workers(
                args,
                2,
                get_nonce=lambda: "nonce",
                worker_procs=worker_procs,
                start_worker=start_worker,
                supervise=supervise,
                set_shutting_down=Mock(),
                logger=Mock(),
            )

        self.assertEqual(result, procs)
        self.assertEqual([item["port"] for item in worker_procs], [8001, 8002])
        self.assertEqual([item["gpu_id"] for item in worker_procs], ["2", "5"])
        self.assertEqual([item["host"] for item in worker_procs], ["127.0.0.1"] * 2)
        self.assertEqual([call.kwargs["worker_id"] for call in start_worker.call_args_list], [0, 1])
        thread.assert_called_once_with(target=supervise, daemon=True)
        thread.return_value.start.assert_called_once_with()
        self.assertEqual(signal.call_count, 2)

    def test_supervisor_restarts_exited_process(self):
        class ExitedProcess:
            returncode = 1

            def poll(self):
                return 1

        worker = {
            "proc": ExitedProcess(),
            "worker_id": 0,
            "port": 8001,
            "gpu_id": None,
            "host": "127.0.0.1",
            "args": object(),
        }
        restarted = object()
        stop_states = iter([False, False, False, True])
        start_worker = Mock(return_value=restarted)

        with patch("server.worker_runtime.time.sleep") as sleep:
            worker_runtime.supervise_subprocess_workers(
                [worker],
                is_shutting_down=lambda: next(stop_states),
                get_nonce=lambda: "nonce",
                start_worker=start_worker,
                logger=Mock(),
                interval=10,
            )

        self.assertIs(worker["proc"], restarted)
        start_worker.assert_called_once_with(
            "127.0.0.1", 8001, "nonce", worker["args"], worker_id=0, gpu_id=None
        )
        sleep.assert_called_once_with(10)


if __name__ == "__main__":
    unittest.main()
