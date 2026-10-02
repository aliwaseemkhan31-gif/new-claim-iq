"""Host capacity and the hardware profile it implies.

``HARDWARE_PROFILE`` was a hand-set environment string, which means it was
wrong on most installations: nobody edits an env var to say "this box has a
16 GB GPU now". This module turns measured facts into the profile the model
registry already understands.

Two sources of truth, deliberately kept apart:

* **The host probe** — CPU count, system memory, GPUs visible to this process.
  Cheap, but it describes the machine Django runs on. In the Docker deployment
  that is not the machine Ollama runs on, and a container typically sees no GPU
  at all.
* **The runtime observation** — what Ollama reports after it has actually
  loaded a model (``/api/ps`` gives ``size_vram``). It describes the right
  machine and cannot be fooled, but it only exists once something has run.

:func:`profile_for` prefers the runtime observation and falls back to the
probe, recording which it used. A profile asserted on a guess is worse than
one that says it was a guess.

Pure stdlib; no Django import. See ADR 0001.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from claimiq.ai.domain.model_registry import HardwareProfile

#: VRAM floors for each profile, in GB.
#:
#: Set below each profile's nominal figure because reported VRAM is always
#: less than the number on the box, and on a workstation the desktop
#: compositor has already taken a slice of it. A 24 GB card that reports
#: 23.6 GB with 0.8 GB in use must still read as ``gpu-high``.
PROFILE_VRAM_FLOOR_GB: tuple[tuple[str, float], ...] = (
    (HardwareProfile.GPU_HIGH, 21.0),
    (HardwareProfile.GPU_MID, 14.0),
    (HardwareProfile.GPU_ENTRY, 6.0),
)

#: Below this much system memory, CPU inference of even the smallest
#: recommended model will swap. Worth saying plainly rather than letting the
#: administrator discover it as a five-minute response.
MIN_USABLE_RAM_GB = 6.0


@dataclass(frozen=True)
class GpuDevice:
    name: str
    total_vram_gb: float
    used_vram_gb: float | None = None

    @property
    def free_vram_gb(self) -> float:
        if self.used_vram_gb is None:
            return self.total_vram_gb
        return max(0.0, self.total_vram_gb - self.used_vram_gb)


@dataclass(frozen=True)
class HostCapacity:
    """What was measured about the machine, and how.

    Attributes:
        cpu_count: Logical processors, or 0 when unknown.
        total_ram_gb: System memory, or 0.0 when it could not be read.
        gpus: GPUs visible to the probing process, strongest first.
        runtime_vram_gb: VRAM Ollama was observed to have in use for a loaded
            model. Evidence that a GPU exists *on the runtime's machine*,
            which is the only machine that matters.
        runtime_total_vram_gb: Total VRAM on the runtime's device where that
            could be established.
        notes: Human-readable account of what each probe found or could not
            find. Surfaced in the UI — "no GPU detected" is only useful
            alongside *how* it was looked for.
    """

    cpu_count: int = 0
    total_ram_gb: float = 0.0
    gpus: tuple[GpuDevice, ...] = ()
    runtime_vram_gb: float | None = None
    runtime_total_vram_gb: float | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def probed_vram_gb(self) -> float:
        return max((gpu.total_vram_gb for gpu in self.gpus), default=0.0)

    @property
    def effective_vram_gb(self) -> float:
        """The VRAM figure a profile should be decided on.

        The runtime's reported *total* is authoritative where it exists. Where
        only occupied VRAM is known, it is a **floor** and never a ceiling: a
        2.2 GB model resident on a 4 GB card says the machine has at least
        2.2 GB, not that it has only that. Taking it as capacity would classify
        a GPU installation down to processor-only on the strength of having run
        a small model, which is exactly backwards.
        """
        if self.runtime_total_vram_gb:
            return self.runtime_total_vram_gb
        return max(self.probed_vram_gb, self.runtime_vram_gb or 0.0)

    @property
    def vram_evidence(self) -> str:
        """``runtime`` | ``probe`` | ``none`` — which figure decided capacity."""
        if self.runtime_total_vram_gb:
            return "runtime"
        if not self.probed_vram_gb and not self.runtime_vram_gb:
            return "none"
        if (self.runtime_vram_gb or 0.0) > self.probed_vram_gb:
            return "runtime"
        return "probe"

    @property
    def ram_is_tight(self) -> bool:
        return 0.0 < self.total_ram_gb < MIN_USABLE_RAM_GB

    def as_dict(self) -> dict:
        return {
            "cpu_count": self.cpu_count,
            "total_ram_gb": round(self.total_ram_gb, 1) if self.total_ram_gb else None,
            "gpus": [
                {
                    "name": gpu.name,
                    "total_vram_gb": round(gpu.total_vram_gb, 1),
                    "free_vram_gb": round(gpu.free_vram_gb, 1),
                }
                for gpu in self.gpus
            ],
            "runtime_vram_gb": (
                round(self.runtime_vram_gb, 1) if self.runtime_vram_gb else None
            ),
            "runtime_total_vram_gb": (
                round(self.runtime_total_vram_gb, 1) if self.runtime_total_vram_gb else None
            ),
            "effective_vram_gb": round(self.effective_vram_gb, 1) or None,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ProfileVerdict:
    """A hardware profile with its justification."""

    profile: str
    basis: str
    """``runtime`` | ``probe`` | ``assumed`` — which evidence decided it."""
    explanation: str

    def as_dict(self) -> dict:
        return {"profile": self.profile, "basis": self.basis, "explanation": self.explanation}


def profile_for(capacity: HostCapacity) -> ProfileVerdict:
    """Classify ``capacity`` into one of the registry's hardware profiles."""
    vram = capacity.effective_vram_gb
    evidence = capacity.vram_evidence
    if evidence == "runtime":
        basis = "runtime"
        source = (
            "the model runtime reported GPU memory while a model was loaded"
            if not capacity.runtime_total_vram_gb
            else "the model runtime reported the device's GPU memory"
        )
    elif evidence == "probe":
        basis = "probe"
        names = ", ".join(gpu.name for gpu in capacity.gpus) or "an unnamed device"
        source = f"a GPU was found on this host ({names})"
    else:
        return ProfileVerdict(
            profile=HardwareProfile.CPU,
            basis="assumed" if not capacity.cpu_count else "probe",
            explanation=(
                "No GPU was found, so models will run on the processor. "
                "Generation will take tens of seconds to minutes per response."
            ),
        )

    for profile, floor in PROFILE_VRAM_FLOOR_GB:
        if vram >= floor:
            return ProfileVerdict(
                profile=profile,
                basis=basis,
                explanation=(
                    f"About {vram:.0f} GB of video memory is available, and "
                    f"{source}."
                ),
            )

    floor_caveat = ""
    if basis == "runtime" and not capacity.runtime_total_vram_gb:
        # Occupied VRAM is a lower bound. On a large card running a small model
        # it understates the device badly, and the administrator — who can see
        # the machine — is better placed to correct it than this is.
        floor_caveat = (
            " That figure is what a loaded model occupied, which is a lower "
            "bound on the device rather than its size; if the GPU is larger, "
            "set the profile by hand."
        )

    return ProfileVerdict(
        profile=HardwareProfile.CPU,
        basis=basis,
        explanation=(
            f"A GPU was found but only about {vram:.0f} GB of video memory is "
            f"accounted for, which is below what the smallest GPU profile "
            f"assumes, so this installation is treated as processor-only."
            f"{floor_caveat}"
        ),
    )
