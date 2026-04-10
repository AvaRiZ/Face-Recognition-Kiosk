from __future__ import annotations

import tensorflow as tf
import torch


def log_header(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def log_step(message: str, status: str = "OK") -> None:
    print(f"[{status}] {message}")


def log_gpu_info() -> None:
    if torch.cuda.is_available():
        try:
            current_device = torch.cuda.current_device()
            torch_name = torch.cuda.get_device_name(current_device)
            log_step(f"Torch CUDA device: {torch_name} (device {current_device})")
        except Exception as exc:
            log_step(f"Torch CUDA info unavailable: {exc}", status="WARN")
    else:
        log_step("Torch CUDA not available", status="WARN")

    try:
        tf_gpus = tf.config.list_logical_devices("GPU")
        if tf_gpus:
            log_step(f"TensorFlow GPU visible: {tf_gpus[0].name}")
        else:
            log_step("TensorFlow GPU hidden (CPU mode)")
    except Exception as exc:
        log_step(f"TensorFlow GPU info unavailable: {exc}", status="WARN")
