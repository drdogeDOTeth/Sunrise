#pragma once

namespace sunrise::client::content::items::packages {

/**
 * Publishes the dense item table from the installed packages, once.
 * @return True when State already holds the table or a full pass publishes it.
 */
[[nodiscard]] bool build() noexcept;

/**
 * Reports whether the pass can read anything yet.
 * It needs the bootstrap token and the installed key table, both published before the Client
 * worker starts. Until both are there, every call returns at once.
 * @return True when the block keys the pass borrows are there.
 */
[[nodiscard]] bool readable() noexcept;

/**
 * Extracts whatever `<artifact>\dump\request.txt` asks for, once per process.
 *
 * This lives inside the mod rather than in `tools/pkg` because it has to. Around 80% of package
 * blocks are encrypted and the families holding art are effectively all of them, so nothing useful
 * can be read offline. The block keys are recovered from the running game and deliberately never
 * written to disk, so the extraction is brought to the keys instead of the other way round.
 *
 * Requests are read from a file so changing what is extracted needs no rebuild. When no request
 * file exists one is written documenting the format. Class listings need no keys and always run;
 * tag reads are skipped when the keys are not available yet.
 */
void dump_if_requested() noexcept;

} // namespace sunrise::client::content::items::packages
