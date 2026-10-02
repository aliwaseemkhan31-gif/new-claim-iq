"""Measuring what this installation's hardware actually is.

Every probe here is best-effort and records what it found. None of them may
raise: this runs inside an administration request and inside a worker, and a
missing ``nvidia-smi`` is a fact about the machine, not a server error.

``psutil`` is deliberately not a dependency. Reading ``/proc/meminfo`` and
calling ``GlobalMemoryStatusEx`` is a few lines each, and the air-gapped
deployment story is better for not adding a package to do it.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from claimiq.ai.domain.hardware import GpuDevice, HostCapacity
from claimiq.core.logging import get_logger

logger = get_logger("ai.capacity")

_BYTES_PER_GB = 1024**3
#: ``nvidia-smi`` on a machine with a wedged driver can hang. Short leash.
_SMI_TIMEOUT_SECONDS = 8


def probe_host_capacity(
    *, runtime_vram_bytes: int | None = None, runtime_total_vram_bytes: int | None = None
) -> HostCapacity:
    """Measure this host, optionally told what the model runtime observed.

    Args:
        runtime_vram_bytes: VRAM a loaded model was seen to occupy, from
            Ollama's ``/api/ps``. This is the only evidence that reflects the
            runtime's machine rather than Django's, so pass it when available.
        runtime_total_vram_bytes: Total device VRAM where it could be
            established.
    """
    notes: list[str] = []

    cpu_count = os.cpu_count() or 0
    if not cpu_count:
        notes.append("The processor count could not be read.")

    ram_gb = _total_ram_gb(notes)
    gpus = _probe_gpus(notes)

    if runtime_vram_bytes:
        notes.append(
            "The model runtime reported video memory in use during the run. "
            "That is proof of at least that much GPU memory on the runtime's "
            "machine, and is used only where it exceeds what this host could "
            "see for itself."
        )
    elif not gpus:
        notes.append(
            "No GPU was found on the host running the application. Note that "
            "where the model runtime is a separate container or machine, its "
            "GPU is invisible from here — running a detection will establish "
            "what the runtime itself has."
        )

    return HostCapacity(
        cpu_count=cpu_count,
        total_ram_gb=ram_gb,
        gpus=tuple(gpus),
        runtime_vram_gb=(runtime_vram_bytes / _BYTES_PER_GB) if runtime_vram_bytes else None,
        runtime_total_vram_gb=(
            runtime_total_vram_bytes / _BYTES_PER_GB if runtime_total_vram_bytes else None
        ),
        notes=tuple(notes),
    )


def _total_ram_gb(notes: list[str]) -> float:
    """System memory in GB, by whichever route this platform offers."""
    # Linux and most containers.
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    kilobytes = float(line.split()[1])
                    return kilobytes / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        pass

    # POSIX without /proc, e.g. macOS.
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and page_size > 0:
            return (pages * page_size) / _BYTES_PER_GB
    except (AttributeError, ValueError, OSError):
        pass

    # Windows.
    if os.name == "nt":
        try:
            import ctypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.ullTotalPhys / _BYTES_PER_GB
        except Exception:  # noqa: BLE001 - a probe failure is a fact, not an error
            pass

    notes.append("System memory could not be read on this platform.")
    return 0.0


def _probe_gpus(notes: list[str]) -> list[GpuDevice]:
    """GPUs visible to this process, strongest first."""
    gpus = _gpus_from_nvidia_smi(notes)
    if gpus:
        return gpus
    return _gpus_from_torch(notes)


def _gpus_from_nvidia_smi(notes: list[str]) -> list[GpuDevice]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    try:
        completed = subprocess.run(  # noqa: S603 - fixed executable, fixed arguments
            [
                executable,
                "--query-gpu=name,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=_SMI_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        notes.append(f"nvidia-smi could not be run ({type(exc).__name__}).")
        return []

    if completed.returncode != 0:
        notes.append("nvidia-smi is present but reported an error.")
        return []

    gpus: list[GpuDevice] = []
    for line in completed.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            total_mib = float(parts[1])
            used_mib = float(parts[2]) if len(parts) > 2 else 0.0
        except ValueError:
            continue
        gpus.append(
            GpuDevice(
                name=parts[0] or "GPU",
                total_vram_gb=total_mib / 1024,
                used_vram_gb=used_mib / 1024,
            )
        )
    gpus.sort(key=lambda gpu: gpu.total_vram_gb, reverse=True)
    return gpus


def _gpus_from_torch(notes: list[str]) -> list[GpuDevice]:
    """Fall back to torch, which the OCR path may already have installed."""
    try:
        import torch
    except ImportError:
        return []
    try:
        if not torch.cuda.is_available():
            return []
        gpus = []
        for index in range(torch.cuda.device_count()):
            properties = torch.cuda.get_device_properties(index)
            gpus.append(
                GpuDevice(
                    name=properties.name,
                    total_vram_gb=properties.total_memory / _BYTES_PER_GB,
                )
            )
        gpus.sort(key=lambda gpu: gpu.total_vram_gb, reverse=True)
        return gpus
    except Exception as exc:  # noqa: BLE001 - torch raises freely on odd drivers
        notes.append(f"A CUDA device could not be inspected ({type(exc).__name__}).")
        return []
