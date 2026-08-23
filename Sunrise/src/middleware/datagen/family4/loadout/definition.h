#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

#include "../../../../state/account/inventory/inventory_state.h"
#include "../instance/instance_encoder.h"

namespace sunrise::middleware::datagen::family4::loadout {

/** Equipped plus unequipped authored items one character may publish. */
inline constexpr std::size_t kItemCapacity = state::account::inventory::kEquipmentSlotCount
                                             + state::account::inventory::kCharacterItemCapacity;

/**
 * Marks a resolved row whose item has no native equipment slot at all.
 *
 * Not every character item is equipment. The Emote Collection is the case that matters: it sits in
 * a one-row character bucket with `equipmentSlot` absent and four ordinary sockets, and the four
 * emote quick-slots read the plugs in those sockets. An item like that can only ever be carried,
 * so it publishes this value and never an equipment index.
 */
inline constexpr std::uint8_t kNoEquipmentSlot = 0xFF;

/** One installed-build-resolved item ready for character and instance encoding. */
struct ResolvedItem {
    std::uint16_t inventoryRow{};
    std::uint8_t equipmentSlot{};
    /** Only equipped items publish their instance SOID into the native equipment array. */
    bool equipped{};
    std::int32_t quantity{};
    /** Authored runtime generation copied into the native inventory row. */
    std::int32_t mutationSerial{};
    /** Accumulated native item-state bits copied into the native inventory row. */
    std::uint32_t flags{};
    instance::ResolvedInstance instance{};
};

/** One item instance together with the native equipment slot that owns it. */
struct SlottedInstance {
    std::uint8_t equipmentSlot{};
    instance::ResolvedInstance instance{};
};

/** Every item instance one character owns, whether or not that character is selected. */
struct ResolvedInstances {
    std::array<SlottedInstance, kItemCapacity> items{};
    std::size_t itemCount{};
};

/** Complete selected-character loadout committed only after every mapping resolves. */
struct ResolvedLoadout {
    std::array<ResolvedItem, kItemCapacity> items{};
    std::size_t itemCount{};
    std::uint32_t nextInventorySerial{};
};

} // namespace sunrise::middleware::datagen::family4::loadout
