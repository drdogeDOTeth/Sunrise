#pragma once

namespace sunrise::client::ui::character {

/**
 * Draws the character switcher: the profiles found under `characters\`, which one is drawn, and
 * the part table it produced.
 *
 * Runs on the render thread inside Present, which is also the thread that services DrawIndexed,
 * so selecting a profile here swaps the part table and its textures without racing a draw.
 */
void draw() noexcept;

} // namespace sunrise::client::ui::character
