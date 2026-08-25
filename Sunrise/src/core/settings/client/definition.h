#pragma once

#include <cstdint>

#include "../../ui/runtime/settings.h"
#include "external/definition.h"

namespace sunrise::core::settings::client {

/** A load this long has stopped making progress, so the spawn stops waiting for it. */
inline constexpr std::uint64_t kDefaultSpawnHoldMs = 30'000;
/** A load past this is a hang, not a slow machine, and holding the spawn would never end. */
inline constexpr std::uint64_t kMaximumSpawnHoldMs = 600'000;

/** Read-only Client settings parsed by Core. */
struct Settings {
    /** In-game UI visibility and input policy. */
    ui::runtime::Settings userInterface;
    /** Points the Client at a server outside this process. Off answers everything in process. */
    external::Settings externalServer;
    /**
     * Releases the world-transition fade channel at the in-world step.
     * The client only releases it on the player spawn, so this covers a spawn that never runs
     * and leaves the world black. On by default.
     */
    bool fadeRelease{true};
    /**
     * Forces the activity session's status 5-to-6 ready check.
     * Two of its five terms are client flags no host message reaches, so the host cannot open it.
     */
    bool forceJoinRequestReady{true};
    /**
     * Reports a public region as private to the region transition.
     * On, a public region loads solo. Off, it waits for a public activity host, which is the
     * route to the citizen join. A forced destination loads solo either way.
     */
    bool regionPrivate{false};
    /**
     * Pins the participation record to the replicated snapshot at `comp + 496`.
     * Off, the record is the local one at `comp + 1256`, whose spawn-gate byte no wire field
     * reaches.
     */
    bool pinReplicatedRecord{true};
    /**
     * Runs the player spawn after the world-transition fade is armed.
     * A spawn before the arm releases nothing, so the screen stays black. Settable because it is
     * the only thing that can turn an allowed spawn into a refusal.
     */
    bool holdSpawn{true};
    /** How long the spawn waits for a load. `hold_spawn` decides whether it waits at all. */
    std::uint64_t spawnHoldMs{kDefaultSpawnHoldMs};
    /**
     * Installs the SEntityModel class-lookup trace, which captures from start rather than from an
     * F8 window.
     *
     * Off by default, because it is not free: it detours the model lookup path and writes a line
     * per lookup, and a world load performs thousands. One measured session logged 42,000 of them,
     * 78% of the whole file, all of it during loading. It answered the custom-character work and
     * has no place in normal play - turn it on for a capture, then turn it off.
     */
    bool modelTrace{false};
    /**
     * Hides the stock race body - the leftover gloves and the bald head - under the custom mesh.
     *
     * This used to be a patch layer on `w64_globals_06dc`, and it cannot be: **that package hangs
     * a world load whenever it carries a layer of ours, whatever the layer contains.** Proven by
     * writing the entry back byte-identical to stock, which failed Titan exactly as the real edit
     * did. So the same edit is made here instead, to the serialized model on its way into the
     * renderer, where no package is patched and no world load can notice.
     *
     * The edit itself is the cheap one it always was: zero every part's index count, and a part
     * with no indices issues no draw.
     */
    bool blankRaceBody{true};
};

} // namespace sunrise::core::settings::client
