#include "proving_policy.h"

#include <cstddef>
#include <cstring>

#include "../../gameplay_log.h"

namespace sunrise::server::gameplay::physics::host {
namespace {

/** This policy's own versioned identity. The runner refuses a zero id or version. */
constexpr world::BlueprintRef kBlueprint{0x50524F56494E4701ULL, 1};
/** Actor ids start above zero because the store treats a zero id as no actor. */
constexpr world::ActorId kFirstActorId = 0x50520001ULL;
/** Every spawned actor is its own first generation. */
constexpr std::uint64_t kFirstActorGeneration = 1;
/** The single counter this policy keeps: how many actors it believes it has standing. */
constexpr std::uint64_t kActorCounterId = 1;
/** A fresh counter has never been written, so the first assignment expects revision zero. */
constexpr std::uint64_t kFirstCounterRevision = 0;
/** Metres between the spawns and the scene origin. Nothing renders, so this only has to be inside
 *  the scene bounds the bridge declares. */
constexpr float kRingRadius = 4.0F;

/** @return A human-readable submit status, so a log line does not print a bare integer. */
[[nodiscard]] const char* status_name(world::CommandSubmitStatus status) noexcept {
    switch (status) {
    case world::CommandSubmitStatus::accepted:
        return "accepted";
    case world::CommandSubmitStatus::coalesced:
        return "coalesced";
    case world::CommandSubmitStatus::duplicate:
        return "duplicate";
    case world::CommandSubmitStatus::invalid:
        return "invalid";
    case world::CommandSubmitStatus::full:
        return "full";
    case world::CommandSubmitStatus::wrongWorld:
        return "wrong_world";
    case world::CommandSubmitStatus::late:
        return "late";
    case world::CommandSubmitStatus::policyDenied:
        return "policy_denied";
    }
    return "unknown";
}

/** @return A human-readable committed-event kind. */
[[nodiscard]] const char* event_name(world::CommittedEventKind kind) noexcept {
    switch (kind) {
    case world::CommittedEventKind::actorSpawned:
        return "actor_spawned";
    case world::CommittedEventKind::actorRemoved:
        return "actor_removed";
    case world::CommittedEventKind::authorityChanged:
        return "authority_changed";
    case world::CommittedEventKind::transformChanged:
        return "transform_changed";
    case world::CommittedEventKind::kinematicTargetChanged:
        return "kinematic_target_changed";
    case world::CommittedEventKind::actorTeleported:
        return "actor_teleported";
    case world::CommittedEventKind::timerFired:
        return "timer_fired";
    case world::CommittedEventKind::checkpointRequested:
        return "checkpoint_requested";
    case world::CommittedEventKind::rewardIntentSubmitted:
        return "reward_intent_submitted";
    case world::CommittedEventKind::controllerConfigured:
        return "controller_configured";
    case world::CommittedEventKind::pathRequested:
        return "path_requested";
    case world::CommittedEventKind::combatConfigured:
        return "combat_configured";
    case world::CommittedEventKind::damageCommitted:
        return "damage_committed";
    case world::CommittedEventKind::triggerCreated:
        return "trigger_created";
    case world::CommittedEventKind::triggerRemoved:
        return "trigger_removed";
    case world::CommittedEventKind::objectiveChanged:
        return "objective_changed";
    case world::CommittedEventKind::participantCreditChanged:
        return "participant_credit_changed";
    case world::CommittedEventKind::incidentSubmitted:
        return "incident_submitted";
    case world::CommittedEventKind::serviceTickCommitted:
        return "service_tick_committed";
    case world::CommittedEventKind::count:
        break;
    }
    return "unknown";
}

/**
 * @param index Spawn index below the actor budget.
 * @return One of four positions on a square around the scene origin.
 * No trigonometry, because the world hashes its canonical state and a checkpoint compares those
 * hashes across runs. Exact float literals keep that comparison honest.
 */
[[nodiscard]] world::Vector3 ring_position(std::uint32_t index) noexcept {
    switch (index % 4U) {
    case 0:
        return {kRingRadius, 0.0F, 0.0F};
    case 1:
        return {0.0F, 0.0F, kRingRadius};
    case 2:
        return {-kRingRadius, 0.0F, 0.0F};
    default:
        return {0.0F, 0.0F, -kRingRadius};
    }
}

} // namespace

/** Binds the policy to one scene's content build and clears the previous lifetime. */
void ProvingPolicy::configure(std::uint64_t contentBuildId) noexcept {
    contentBuildId_ = contentBuildId;
    world_ = 0;
    nextCommandId_ = 1;
    state_ = {};
    reported_ = {};
}

/** Declares the four actors, the one counter, and exactly the command bits used below. */
world::ActivityPolicyManifest ProvingPolicy::manifest() const noexcept {
    world::ActivityPolicyManifest result{};
    result.policy = kBlueprint;
    result.contentBuildId = contentBuildId_;
    result.fixedRateHz = world::kDefaultFixedRateHz;
    result.actorBudget = kProvingActorBudget;
    // Only what pre_tick submits. A bit this policy does not use is a bit it cannot be blamed for.
    result.allowedCommandMask =
        world::command_kind_mask(world::HostCommandKind::spawnActor)
        | world::command_kind_mask(world::HostCommandKind::configureCombat)
        | world::command_kind_mask(world::HostCommandKind::setObjectiveCounter);
    result.counterBudget = 1;
    result.persistenceSchemaVersion = 1;
    return result;
}

/** Records the world it opened in and leaves the queue empty. */
bool ProvingPolicy::initialize(const world::ActivityPolicyContext& context,
                               world::HostCommands& commands) noexcept {
    static_cast<void>(commands);
    world_ = context.world.activitySessionId;
    report(core::log::Level::info,
           "ev=policy stage=init policy=proving activity=0x%016llX generation=%llu seed=0x%016llX "
           "rate=%uhz actors=%u",
           static_cast<unsigned long long>(context.world.activitySessionId),
           static_cast<unsigned long long>(context.world.worldGeneration),
           static_cast<unsigned long long>(context.deterministicSeed),
           context.fixedRateHz,
           kProvingActorBudget);
    return true;
}

/**
 * Submits at most one command per tick: spawn every actor, then arm every actor, then count them.
 *
 * One per tick is not politeness. `pre_tick` runs inside the tick transaction and a queue overflow
 * rolls the whole tick back, which the bridge treats as a failed tick and closes the world for. At
 * 30 Hz the whole sequence still finishes in under a third of a second.
 *
 * The cursor advances whether or not a submission is accepted. A command that is refused every
 * tick would otherwise retry forever and bury the log line that named the refusal.
 */
void ProvingPolicy::pre_tick(const world::PolicyTickContext& context,
                             world::HostCommands& commands) noexcept {
    // Commands submitted during a tick execute on the next one; the writer refuses anything else.
    const world::TickId at = context.tick + 1U;
    world::PolicyCommandMeta meta{};
    meta.definition = kBlueprint;
    meta.executeTick = at;
    meta.policyOwnerId = kProvingPolicyOwnerId;
    meta.commandId = nextCommandId_++;

    if (state_.spawned < kProvingActorBudget) {
        const std::uint32_t index = state_.spawned++;
        world::SpawnActorCommand spawn{};
        spawn.actor = {kFirstActorId + index, kFirstActorGeneration};
        spawn.transform.position = ring_position(index);
        const world::CommandSubmitStatus status = commands.spawn_actor(meta, spawn);
        report(core::log::Level::info,
               "ev=policy stage=spawn policy=proving actor=0x%llX tick=%llu result=%s",
               static_cast<unsigned long long>(spawn.actor.id),
               static_cast<unsigned long long>(at),
               status_name(status));
        return;
    }

    if (state_.armed < state_.spawned) {
        const std::uint32_t index = state_.armed++;
        world::ConfigureCombatCommand configure{};
        configure.actor = {kFirstActorId + index, kFirstActorGeneration};
        configure.combatProfile = kProvingCombatProfile;
        const world::CommandSubmitStatus status = commands.configure_combat(meta, configure);
        report(core::log::Level::info,
               "ev=policy stage=arm policy=proving actor=0x%llX tick=%llu result=%s",
               static_cast<unsigned long long>(configure.actor.id),
               static_cast<unsigned long long>(at),
               status_name(status));
        return;
    }

    // The counter is written once, on the tick after the last actor is armed. Its revision moves
    // on every accepted write, so a second assignment at revision zero would be refused anyway.
    if (state_.counted == 0) {
        state_.counted = 1;
        world::SetObjectiveCounterCommand counter{};
        counter.counterId = kActorCounterId;
        counter.value = static_cast<std::int64_t>(kProvingActorBudget);
        counter.expectedRevision = kFirstCounterRevision;
        const world::CommandSubmitStatus status = commands.set_objective_counter(meta, counter);
        report(core::log::Level::info,
               "ev=policy stage=count policy=proving counter=%llu value=%u tick=%llu result=%s",
               static_cast<unsigned long long>(kActorCounterId),
               kProvingActorBudget,
               static_cast<unsigned long long>(at),
               status_name(status));
        return;
    }
    // Nothing further. The world keeps ticking and the actors keep standing. The id this tick
    // reserved goes back, so command ids stay dense and a log reading them stays legible.
    --nextCommandId_;
}

/**
 * Reports the first committed event of every kind, then goes quiet.
 *
 * A committed event is the only proof a command reached the world; an accepted submission only
 * means the queue took it. One line per kind per world keeps 30 ticks a second from burying that.
 */
void ProvingPolicy::post_tick(const world::PolicyTickContext& context,
                              std::span<const world::CommittedEvent> events,
                              world::HostCommands& commands) noexcept {
    static_cast<void>(context);
    static_cast<void>(commands);
    for (const world::CommittedEvent& event : events) {
        const auto slot = static_cast<std::size_t>(event.kind);
        if (slot >= reported_.size() || reported_[slot]) {
            continue;
        }
        reported_[slot] = true;
        report(core::log::Level::info,
               "ev=policy stage=event policy=proving kind=%s actor=0x%llX tick=%llu value=%llu",
               event_name(event.kind),
               static_cast<unsigned long long>(event.actor.id),
               static_cast<unsigned long long>(event.tick),
               static_cast<unsigned long long>(event.value));
    }
}

/** Writes the fixed spawn and arm cursors. */
bool ProvingPolicy::save(world::IPolicyStateWriter& writer) const noexcept {
    std::array<std::byte, sizeof(State)> bytes{};
    std::memcpy(bytes.data(), &state_, sizeof(State));
    return writer.write(bytes);
}

/** Restores the fixed spawn and arm cursors, refusing anything the world cannot honour. */
bool ProvingPolicy::load(world::IPolicyStateReader& reader) noexcept {
    std::array<std::byte, sizeof(State)> bytes{};
    if (!reader.read(bytes)) {
        return false;
    }
    State restored{};
    std::memcpy(&restored, bytes.data(), sizeof(State));
    if (restored.spawned > kProvingActorBudget || restored.armed > restored.spawned
        || restored.counted > 1) {
        return false;
    }
    state_ = restored;
    return true;
}

} // namespace sunrise::server::gameplay::physics::host
