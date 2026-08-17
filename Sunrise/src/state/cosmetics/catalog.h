#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace sunrise::state::cosmetics::catalog {

/** Profile bucket holding ornaments. */
inline constexpr std::uint8_t kOrnamentBucketId = 13;
/** Profile bucket holding shaders. */
inline constexpr std::uint8_t kShaderBucketId = 14;

/**
 * Rows one pass can hold. An old build carries a few thousand appearance plugs, so this leaves
 * generous headroom; a pass that still overflows says so rather than silently truncating.
 */
inline constexpr std::size_t kEntryCapacity = 16'384;

/** One installed appearance plug. */
struct Entry {
    std::uint32_t definitionHash{};
    std::uint16_t definitionIndex{};
    std::uint8_t bucketId{};
};

/**
 * Fixed working storage for one pass, kept off the caller stack.
 * At 16,384 rows this is well past what any stack frame should carry, so callers own it as static
 * or heap storage.
 */
struct Storage {
    std::array<Entry, kEntryCapacity> entries{};
    std::size_t count{};
    /** Set when the installed build carries more appearance plugs than storage holds. */
    bool truncated{};
};

/**
 * Collects every installed ornament and shader in native definition-index order.
 *
 * A row qualifies only when it sits in an appearance bucket and the socket relation actually names
 * it as a plug. The bucket alone is not enough: the same buckets carry rows that no socket accepts,
 * and offering one would produce a plug the staging path always refuses.
 *
 * @param storage Pass storage receiving the rows and their count.
 * @return True when the item catalog and socket relation are published and a pass ran.
 */
[[nodiscard]] bool collect(Storage& storage) noexcept;

/**
 * Writes the collected catalog beside the settings file as readable text.
 *
 * Each row carries the hash in hex and in decimal, because the decimal form is what the public
 * Destiny 2 databases key on and hand-editing `settings.json` needs the hex form.
 *
 * @param module Loaded DLL, used to find the owned artifact directory.
 * @return True when a pass ran and the whole document reached the file.
 */
[[nodiscard]] bool dump(void* module) noexcept;

} // namespace sunrise::state::cosmetics::catalog
