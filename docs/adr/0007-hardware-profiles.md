# ADR 0007 — Hardware profiles and graceful degradation

**Status:** Accepted
**Date:** 2026-09-12

## Context

Deployments will not look alike. Plausible hosts range from a repurposed office
server with 16 GB of RAM and no GPU, to a workstation with a 24 GB card, to a
locked-down machine in a secure facility with whatever hardware procurement
approved two years ago.

A single hardcoded model choice fails everyone: too large and it will not run
on the small host; too small and the capable host is wasted. The prototype
hardcoded `mistral` and had no notion of the machine underneath it.

Two lazier options were rejected. **Auto-detecting hardware and choosing
silently** produces a system whose behaviour changes between environments for
reasons the administrator cannot see. **Refusing to run below some minimum**
excludes exactly the air-gapped, modest-hardware deployments this product is
aimed at.

## Decision

A **hardware profile** is explicit deployment configuration, and it shapes both
the image and the model recommendations.

```
cpu         no GPU assumed
gpu-entry   ~8 GB VRAM
gpu-mid     ~16 GB VRAM
gpu-high    24 GB+ VRAM
```

`HARDWARE_PROFILE` is read at two points:

- **Build time** — selects the torch wheel. The CPU-only build is roughly 2 GB
  smaller than the CUDA build, and a host with no GPU should not carry CUDA
  libraries it can never use.
- **Runtime** — filters model recommendations in `ModelRegistry`.

Four rules govern behaviour:

1. **`cpu` is the default.** The stack starts on any host. A deployment that
   never sets the variable gets something that works, slowly, rather than
   something that fails to start.
2. **Profiles are inclusive downward.** `gpu-high` can run everything
   `gpu-entry` can. Ranked, not enumerated.
3. **Recommendations are filtered, not enforced.** An administrator may select
   any model the runtime has, including one outside the profile. They may know
   something the registry does not — the estimates here are approximate, and
   quantisation, offloading and shared memory all move the real figure.
4. **Oversized selections warn.** Clearly, with the numbers:

   > *Qwen 2.5 32B Instruct needs roughly 20.0 GB of VRAM; this profile assumes
   > about 8 GB. Expect offloading to system memory and much slower generation.*

   A warning, not a refusal. Refusing on an approximate estimate is worse than
   letting an informed operator proceed.

Model recommendations stay within one family (Qwen 2.5 Instruct) across every
profile. That is the property that decided the family: prompts behave
consistently as a deployment scales up or down, so a prompt tuned on `gpu-mid`
does not have to be re-validated when a site runs `cpu`.

## Consequences

**Positive.**
The product installs on modest hardware without pretending that is the same as
a capable host. Nothing changes silently between environments. Image size
matches the deployment. Adding a profile — an NPU tier, a multi-GPU tier — is a
data change in `PROFILE_RANK` and `PROFILE_VRAM_GB`.

**Negative.**
The profile must be set correctly, and getting it wrong produces poor
performance rather than a clear error. Changing it requires a rebuild, because
it is baked into the image at the torch layer. And there is a real quality
difference between profiles: a 3B model on CPU is meaningfully weaker at
multi-clause reasoning than a 32B model on a large GPU. That is stated in the
model notes rather than hidden, so an operator on `cpu` knows what they have.

**Accepted limit.**
VRAM figures are estimates for common quantisations. They inform a warning; they
are not a scheduler and must not be treated as one.

## Amendment — 2 October 2026: measured profiles and model detection

The negative consequence above — *"the profile must be set correctly, and
getting it wrong produces poor performance rather than a clear error"* — turned
out to be the one that bit. `HARDWARE_PROFILE` was a hand-set string, so in
practice it stayed at `cpu`, and installations with a GPU ran the model the
installer happened to pick. The same was true of the model names: changing one
meant editing an environment variable and restarting, so nobody changed one.

**Administration → System → Model selection** now measures rather than assumes,
and stores the result in `ai_model_configuration` — resolved at call time by
`claimiq.ai.services.configuration.resolve_ai_settings`, which every service
that needs a model name goes through. A blank column falls back to the
environment, so an installation that never uses the feature behaves exactly as
before.

**This does not reverse the rejection of "auto-detecting hardware and choosing
silently" above.** The objection was to silence, not to measurement. The run is
started by an operator, shows every measurement it took and the reasoning for
each suggestion, and changes nothing until they apply it. Rule 3 above holds:
recommendations are filtered, not enforced, and an operator may still select a
model outside the profile.

Three things the measurement taught us, each now encoded:

1. **The host probe is the weaker evidence.** Where Ollama runs in its own
   container, Django sees no GPU at all. What a loaded model occupies, from
   `/api/ps`, describes the right machine. But occupied VRAM is a **floor**, not
   a capacity: taking it as capacity classified a GPU host down to `cpu` on the
   strength of having run a small model.
2. **Throughput must be measured on the real task.** Tokens per second on
   open prose says nothing about whether a model can hold a JSON schema, and
   grounding depends on that (ADR 0005). A model that fails the schema is never
   recommended, however fast.
3. **One ranking is not enough.** Answering is interactive and drafting is not,
   so the same measurements rank differently per role. On the development
   machine — a 4 GB laptop GPU, below the `gpu-entry` floor — the 3B answers in
   12 s and the 7B takes 2.1 min at 42% GPU residency. The first is the right
   answering model and the second is the right drafting model, and no single
   score expresses that.

The build-time half of `HARDWARE_PROFILE` is unchanged: it still selects the
torch wheel and still requires a rebuild.
