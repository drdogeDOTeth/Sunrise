#pragma once

#include <cstddef>
#include <d3d11.h>
#include <string_view>

namespace sunrise::client::hooks::custom_albedo {

/**
 * Compiles the RGB albedo pixel shader and attaches DrawIndexed hooks on this context.
 * The dye PS luma-gates unique RGB. Replaces that shader on the exact
 * index ranges in the active character profile (or the shipped six-part defaults)
 * when the select G-buffer (29/24/28) is bound, then restores the previous
 * PS including class instances. v18 uses GLB roughness on t1 and open AO.
 * Do not sample Destiny t2. v16 TEXCOORD2 is closed. Do not skip the
 * gauntlet-hand draw — first-person needs those hands visible.
 * Dumps the live game PS once.
 * @param device Device that owns the compiled shader.
 * @param context Immediate context whose DrawIndexed vtable is hooked.
 */
void attach(ID3D11Device* device, ID3D11DeviceContext* context) noexcept;

/** Detaches the draw hooks and releases the compiled shader. Idempotent. */
void detach() noexcept;

/**
 * One character the switcher can draw: a directory under `characters\` holding a `parts.txt`
 * and the textures it names. The profile a part table came from is what makes a character
 * swappable, because everything except the injected geometry is read from these files.
 */
inline constexpr std::size_t kMaxProfiles = 16;

/** @return Number of character profiles found by the last scan. Zero means the shipped defaults. */
[[nodiscard]] std::size_t profile_count() noexcept;

/** @param index Profile slot. @return Its directory name, or empty when out of range. */
[[nodiscard]] std::string_view profile_name(std::size_t index) noexcept;

/** @return Slot of the profile currently drawn, or `profile_count()` when none is. */
[[nodiscard]] std::size_t active_profile() noexcept;

/** Re-reads `characters\` so a profile added while the game runs is offered. */
void rescan_profiles() noexcept;

/**
 * Swaps the drawn character: re-reads that profile's part table and uploads its textures.
 * No detour is touched and no package is read, so this is immediate and reversible - but it
 * changes only paint and visibility. Geometry lives in package layers and cannot swap here.
 * @param index Profile slot to draw.
 * @return True when the profile was read and its textures uploaded.
 */
[[nodiscard]] bool select_profile(std::size_t index) noexcept;

/** One row of the active part table, for the panel to report. */
struct PartView {
    std::string_view name{};
    unsigned start{};
    unsigned count{};
    /** True when this range is skipped at draw time rather than painted. */
    bool hidden{};
};

/** @return Number of parts in the active table. */
[[nodiscard]] std::size_t part_count() noexcept;

/** @param index Part slot. @return Its row, or a default when out of range. */
[[nodiscard]] PartView part_at(std::size_t index) noexcept;

} // namespace sunrise::client::hooks::custom_albedo
