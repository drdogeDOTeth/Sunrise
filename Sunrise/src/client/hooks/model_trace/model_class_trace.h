#pragma once

namespace sunrise::client::hooks::model_trace {

/**
 * Attaches a dormant diagnostic guard to the reflected-class lookup used by package resources.
 * The guard forwards every lookup unchanged and records only SEntityModel while F8 capture is on.
 * @return True when the lookup guard is attached or was already attached.
 */
[[nodiscard]] bool install() noexcept;

/** Detaches the reflected-class lookup guard. @return True when no guard remains attached. */
[[nodiscard]] bool uninstall() noexcept;

} // namespace sunrise::client::hooks::model_trace
