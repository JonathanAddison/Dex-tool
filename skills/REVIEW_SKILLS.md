# Dex-tool specialist review skills (offline, evidence-first)

## Project profile
Dex-tool is an Android runtime inspector built from C++/NDK, Frida GumJS, Dobby, Dear ImGui, EGL/OpenGL, JNI and a Java/Kotlin sample app. Outputs target `armeabi-v7a` and `arm64-v8a`, Android API 26+. The build workflow fetches Frida Gum, Dobby, ImGui and smali; a source-only review runner does not automatically possess these dependencies.

## Skill: C++ concurrency and lifetime
- Trace every shared container and mutable global across JS, render, callback, signal and worker threads.
- Check lock ownership, lock order, recursive-lock assumptions, references/iterators invalidated by vector growth/erase, callback reentrancy and lock-held blocking operations.
- Check atomics for correct memory ordering and coherent multi-field state; separate atomics do not make a compound invariant atomic.
- Audit JNI global/local references, thread attachment/detachment, VM lifetime, Java object disposal, and callbacks after teardown.
- For every fix, identify all writers/readers and shutdown/context-loss paths. Avoid speculative race claims without a concrete interleaving.

## Skill: Android NDK / ABI / loader
- Verify both `arm64-v8a` and `armeabi-v7a`, API 26 minimum, NDK 25.2.9519653, STL/ABI compatibility, PIC, visibility and linker flags.
- Inspect `JNI_OnLoad`, symbol exports, `dlopen`/`dlsym`, ELF assumptions, page size/alignment, pointer width, `uintptr_t`, packed structures and 32-bit truncation.
- Distinguish host-process ABI from Android SDK/NDK host tools. Do not infer a successful Android build from host-native compilation.
- Check signal handlers are async-signal-safe; avoid allocator, locks, stdio, loader calls or C++ runtime work in handlers.

## Skill: Frida GumJS / Java bridge
- Check `Java.perform` scheduling, class-loader assumptions, overloaded method resolution, wrapper lifetime, Java.choose retention, thread affinity and exceptions crossing JS/native boundaries.
- Audit Interceptor attach/detach/revert symmetry, NativeCallback/NativeFunction lifetimes, replacement recursion, instruction/cache assumptions and hook teardown.
- Check Memory APIs for bounds, page permissions, architecture-specific instruction size/alignment and restore-on-failure.
- Treat app-specific class names and APIs as dynamic; handle missing classes/methods gracefully and report partial capability instead of hanging.

## Skill: ImGui / EGL / OpenGL lifecycle
- Check all ImGui calls happen on the owning render thread and under the intended context.
- Audit EGL context loss/recreation, surface changes, backend init/shutdown, texture/font lifetime, GL object ownership and no-GL-call parked states.
- Ensure touch coordinates, density scaling, safe areas, IME and NativeActivity paths use consistent coordinate spaces.
- Look for per-frame allocations, unbounded logs/maps, retained JNI refs and expensive JS/native synchronous calls.

## Skill: JavaScript runtime safety and performance
- Validate syntax with `node --check` and inspect every callback for exception containment, reentrancy and stale references.
- Bound arrays, maps, event queues, logs and retained object handles. Check cleanup on tab close, hook removal and unload.
- Avoid synchronous heap scans and repeated reflection on frame-critical paths; paginate, cap and cache with invalidation.
- Ensure asynchronous callbacks carry request IDs/generations so stale responses cannot mutate a newer UI state.

## Skill: build and reproducibility
- Source of truth: `src/`, `agent/agent.js`, `sample-app/`, `CMakeLists.txt`, `.github/workflows/` and README.
- Build pipeline generates `src/agent_bundle_embed.h` and builds two Android ABIs; verify generated bundle is reproducible and does not overwrite tracked source unexpectedly.
- Check pinned dependency revisions, cache keys, SDK environment consistency, Gradle wrapper, artifact paths and `set -euo pipefail`.
- Do not claim full Android build success unless the actual NDK + Frida + Dobby + ImGui build ran. Host cppcheck or Node syntax is not equivalent.

## Evidence and patch rules
- Every finding must cite exact path, line range, symbol and a short source quote.
- Trace the full call path before calling a known approximation a bug.
- Separate confirmed defects, plausible risks and design limitations.
- Prefer minimal unified diffs; run the fixed sandbox suite against the exact patch hash.
- If required Android dependencies/device/runtime are absent, mark the corresponding validation as NOT RUN.
