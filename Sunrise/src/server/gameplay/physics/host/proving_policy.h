#pragma once

#include <array>
#include <cstdint>

#include "server/gameplay/physics/world/activity_policy.h"

namespace sunrise::server::gameplay::physics::host {

/** Logical actors this policy asks the world for. Well under `world::kActorCapacity`. */
inline constexpr std::uint32_t kProvingActorBudget = 4;
/**
 * Stamped on every command this policy submits.
 * The host matches it against the owner on a registered combat profile, so the bridge that
 * registers the profile has to use this same value.
 */
inline constexpr std::uint64_t kProvingPolicyOwnerId = 0x50524F56494E4701ULL;
/** The one combat profile this policy selects. The bridge registers it before the first tick. */
inline constexpr world::BlueprintRef kProvingCombatProfile{0x50524F56434D4201ULL, 1};

/**
 * The first activity policy that is not inert: it spawns, arms and counts, and reports every
 * answer the world gives it.
 *
 * Both shipped policies do nothing — `DefaultActivityPolicy` declares a zero command mask and a
 * zero actor budget, `ScriptlessPolicy` declares no actors either — so no command has ever reached
 * the writer from a policy source and `spawn_actor` has never had a caller. Every stage below it
 * exists: the actor store, the combat kernel with factions and health, triggers, objective
 * counters, timers, checkpoint hashing. None of it has run.
 *
 * So this policy's job is measurement, not gameplay. It spawns one actor per tick up to its
 * budget, gives each a combat profile, counts them on an objective counter, and logs the
 * `CommandSubmitStatus` of every submission and the kind of every committed event it sees. A
 * launch with this installed answers, in the log and without a debugger, which layer of the
 * mission engine works and which one refuses first.
 *
 * **It cannot produce a visible enemy, and is not meant to.** A logical actor is server-side only:
 * nothing calls `WorldCoordinator::prepare_frame`, the `gameplayExternalBody` gate is read by
 * nothing, and `serverDefaultEntity`'s own comment says the visible class chain and the live
 * outcome path do not exist. Those are the next three problems, and they are separate from this
 * one.
 */
class ProvingPolicy final : public world::IActivityPolicy {
public:
    /**
     * Binds the policy to the scene it will run in, before the world opens.
     * The host refuses a manifest whose content build disagrees with the scene's, so the value
     * cannot be a constant here.
     * @param contentBuildId Content build stamped on the scene being opened.
     */
    void configure(std::uint64_t contentBuildId) noexcept;

    /** @return Actor, trigger and counter budgets, and the exact command bits used below. */
    [[nodiscard]] world::ActivityPolicyManifest manifest() const noexcept override;
    /** @return True always; the spawns wait for a tick whose id the commands can carry. */
    [[nodiscard]] bool initialize(const world::ActivityPolicyContext& context,
                                  world::HostCommands& commands) noexcept override;
    /** Spawns one actor per tick to the budget, then arms and counts what took. */
    void pre_tick(const world::PolicyTickContext& context,
                  world::HostCommands& commands) noexcept override;
    /** Reports the committed events, which is the only proof a command reached the world. */
    void post_tick(const world::PolicyTickContext& context,
                   std::span<const world::CommittedEvent> events,
                   world::HostCommands& commands) noexcept override;
    /** @return True when the spawn cursor entered the snapshot. */
    [[nodiscard]] bool save(world::IPolicyStateWriter& writer) const noexcept override;
    /** @return True when a compatible spawn cursor was restored. */
    [[nodiscard]] bool load(world::IPolicyStateReader& reader) noexcept override;

private:
    /** Durable policy state. Fixed size, so `save` and `load` agree without a length prefix. */
    struct State final {
        std::uint32_t spawned{};
        std::uint32_t armed{};
        /** Nonzero once the counter has been written. It is written exactly once per world. */
        std::uint32_t counted{};
    };

    std::uint64_t contentBuildId_{};
    std::uint64_t world_{};
    /** Monotonic within one world lifetime, so no two commands share an id. */
    std::uint64_t nextCommandId_{1};
    State state_{};
    /** One line per event kind per world, so a 30 Hz tick cannot flood the log. */
    std::array<bool, static_cast<std::size_t>(world::CommittedEventKind::count)> reported_{};
};

} // namespace sunrise::server::gameplay::physics::host
