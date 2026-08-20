#pragma once

namespace sunrise::client::hooks::package_trace {

/**
 * Attaches diagnostic guards to the Windows file-read exports used by the retail package loader.
 * The guards forward every read unchanged and stay dormant until capture is toggled.
 * @return True when both read exports are attached or were already attached.
 */
[[nodiscard]] bool install() noexcept;

/** Detaches both read guards. @return True when no read guard remains attached. */
[[nodiscard]] bool uninstall() noexcept;

/**
 * Toggles a bounded package-read capture when F8 is pressed.
 * Called from the presentation thread so no input work is done on an I/O completion thread.
 */
void poll_hotkey() noexcept;

/** @return True while package reads are being recorded. */
[[nodiscard]] bool capturing() noexcept;

} // namespace sunrise::client::hooks::package_trace
