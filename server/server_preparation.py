"""Configure server resources and select its executor startup path."""


def prepare_server(args, runtime):
    """Configure worker/resource limits through the live server's patchable hooks."""
    runtime._init_server_environment(args)

    executor_mode = getattr(args, "executor_mode", runtime.EXECUTOR_MODE_INPROCESS)
    num_workers = max(1, getattr(args, "workers", 3))
    runtime.inference_page_batch_size = num_workers
    cpu_workers = runtime._cpu_stage_worker_count(
        num_workers, getattr(args, "cpu_stage_workers", None)
    )
    gpu_available = False
    if getattr(args, "start_instance", False):
        import torch
        requested_gpu = bool(getattr(args, "use_gpu", False) or getattr(args, "use_gpu_limited", False))
        gpu_available = requested_gpu and (
            torch.xpu.is_available()
            or torch.backends.mps.is_available()
            or torch.cuda.is_available()
        )
    runtime.model_executor_concurrency = 1 if gpu_available else runtime.MODEL_EXECUTOR_CONCURRENCY
    runtime.configure_cpu_stage_workers(cpu_workers)
    runtime.batch_resource_limits = runtime.stage_resource_limits(
        num_workers, cpu_workers, gpu_concurrency=runtime.model_executor_concurrency
    )
    runtime.logger.info(
        "Pipeline resources: workers=%d inference-page-batch=%d CPU-heavy=%d CPU-light=%d GPU=%d network=%d I/O=%d local-model/process=%d",
        num_workers,
        runtime.inference_page_batch_size,
        runtime.batch_resource_limits[runtime.ResourceClass.CPU_HEAVY],
        runtime.batch_resource_limits[runtime.ResourceClass.CPU_LIGHT],
        runtime.batch_resource_limits[runtime.ResourceClass.GPU],
        runtime.batch_resource_limits[runtime.ResourceClass.NETWORK],
        runtime.batch_resource_limits[runtime.ResourceClass.IO],
        runtime.model_executor_concurrency,
    )

    if not args.start_instance:
        return []
    if executor_mode == runtime.EXECUTOR_MODE_INPROCESS:
        return runtime._setup_inprocess_workers(args, num_workers, runtime.model_executor_concurrency)
    return runtime._setup_subprocess_workers(args, num_workers)
