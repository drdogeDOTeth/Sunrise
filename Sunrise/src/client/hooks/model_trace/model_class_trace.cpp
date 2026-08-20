#include "model_class_trace.h"

#include <Windows.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <intrin.h>
#include <span>
#include <string_view>

#include "../../../core/logging/log.h"
#include "../../diagnostics/module_range.h"
#include "../../hooking/detour.h"
#include "../../patterns/image_scan.h"
#include "../../patterns/signature_text.h"
#include "../package_trace/package_read_trace.h"

namespace sunrise::client::hooks::model_trace {
namespace {

/** Shadowkeep renderer schema id for SEntityModel. */
constexpr std::uint32_t kEntityModelClass = 0x808073A5U;
/**
 * A focused inspect capture stays in the low hundreds, but a world transition constructs every
 * streamed entity at once. Sized for the transition case, because that is the capture where the
 * player body is actually built rather than already resident.
 */
constexpr std::uint32_t kEventLimit = 49152;
constexpr ULONG kStackFrameLimit = 16;
constexpr std::size_t kSerializedFrameLimit = 8;

/**
 * Tiger handle range. The base is a numeric bias that is *added* to the shifted 12-bit package id,
 * so valid installed handles run past 0x80FFFFFF - globals_06dc is addressed at 0x815Bxxxx. Bounding
 * this with an OR-style 0x80FFFFFF ceiling is what previously made a fifth of the install invisible.
 */
constexpr std::uint32_t kTagMin = 0x80800000U;
constexpr std::uint32_t kTagMax = 0x80800000U + (0x0FFFU << 13U) + 0x1FFFU;
/**
 * Bytes of a live instance to scan. SEntityModel itself is 0xA0, but the mesh records carrying the
 * vertex and index buffer handles sit in the same deserialized blob, so the window is widened to
 * reach them. Every read is clamped to the committed region first.
 */
constexpr std::size_t kTagWindowBytes = 0x800;
/** Enough handles to identify an entry without crowding the stack chain out of the line. */
constexpr std::size_t kTagSampleLimit = 24;

/**
 * Unique prologue of the reflected tag-class lookup in this retail client. Its first argument is
 * the encoded class id and its two optional outputs receive the class record and lookup flags.
 */
constexpr std::string_view kClassLookupText =
    "48 8B C4 4C 89 40 18 48 89 50 10 53 41 54 48 83 EC 48 48 89 68 20 4D 8B E0 "
    "48 89 70 E8 44 0F B7 E1 48 89 78 E0 8B F9";
constexpr auto kClassLookup =
    patterns::signature<patterns::signature_length(kClassLookupText)>(kClassLookupText);

using ClassLookup = void(__fastcall*)(std::uint32_t, void**, std::uint32_t*, std::uintptr_t);
using ResourceConstructor =
    void*(__fastcall*)(void*, std::uint32_t, std::uint32_t, const void*);

enum class HookSlot : std::size_t {
    classLookup,
    resourceConstructor,
    count,
};

constexpr std::size_t kHookCount = static_cast<std::size_t>(HookSlot::count);
std::array<hooking::detour::Handle, kHookCount> g_handles{};
std::atomic_bool g_installed{};
std::atomic_uint32_t g_eventCount{};
std::atomic_bool g_limitReported{};
/**
 * Records from install rather than from the F8 key.
 *
 * The player's own character is materialized during login, long before anyone can reach orbit and
 * press a key. Three separate captures - the inspect paperdoll, and two Tower runs, one with armour
 * equipped and one without - all missed it for exactly this reason, and each returned the same set
 * of ambient Tower content instead. Gating on F8 can only ever catch models built *after* the
 * window opens, which the player's body never is.
 *
 * F8 still bounds the read trace, which is noisy and physical; this one is bounded by kEventLimit.
 */
std::atomic_bool g_captureFromStart{true};

/** @return True while SEntityModel construction should be recorded. */
[[nodiscard]] bool capture_active() noexcept {
    return g_captureFromStart.load(std::memory_order_relaxed) || package_trace::capturing();
}
diagnostics::ModuleRange g_gameRange{};
thread_local bool g_expectEntityModelConstructor{};
thread_local ULONGLONG g_entityModelExpectationDeadline{};

/** @return Installed trampoline for one hook slot, or null during a detach transition. */
template <typename Function> [[nodiscard]] Function original(HookSlot slot) noexcept {
    return reinterpret_cast<Function>(g_handles[static_cast<std::size_t>(slot)].original);
}

/** Appends the game-image return-address chain to one structured event. */
[[nodiscard]] std::size_t append_game_stack(std::span<char> event,
                                            std::size_t length) noexcept {
    std::array<void*, kStackFrameLimit> frames{};
    const USHORT count = CaptureStackBackTrace(0,
                                               static_cast<DWORD>(frames.size()),
                                               frames.data(),
                                               nullptr);
    std::size_t emitted = 0;
    for (USHORT index = 0; index < count && emitted < kSerializedFrameLimit; ++index) {
        const auto address = reinterpret_cast<std::uintptr_t>(frames[index]);
        if (!diagnostics::contains(g_gameRange, address)) {
            continue;
        }
        const int written = std::snprintf(event.data() + length,
                                          event.size() - length,
                                          "%s0x%llX",
                                          emitted == 0 ? " stack=" : ",",
                                          static_cast<unsigned long long>(address
                                                                          - g_gameRange.base));
        if (written <= 0 || static_cast<std::size_t>(written) >= event.size() - length) {
            break;
        }
        length += static_cast<std::size_t>(written);
        ++emitted;
    }
    return length;
}

/** @return True when a page protection permits reading without raising. */
[[nodiscard]] bool protection_allows_read(DWORD protect) noexcept {
    if ((protect & (PAGE_GUARD | PAGE_NOACCESS)) != 0) {
        return false;
    }
    constexpr DWORD kReadable = PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY
                                | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE
                                | PAGE_EXECUTE_WRITECOPY;
    return (protect & kReadable) != 0;
}

/**
 * @param address Candidate pointer taken from a live register or argument.
 * @param wanted Bytes the caller would like to read.
 * @return Readable bytes at `address`, clamped to the committed region, or 0.
 */
[[nodiscard]] std::size_t readable_bytes(std::uintptr_t address, std::size_t wanted) noexcept {
    if (address == 0) {
        return 0;
    }
    MEMORY_BASIC_INFORMATION memory{};
    if (VirtualQuery(reinterpret_cast<LPCVOID>(address), &memory, sizeof(memory))
        != sizeof(memory)) {
        return 0;
    }
    if (memory.State != MEM_COMMIT || !protection_allows_read(memory.Protect)) {
        return 0;
    }
    const auto regionEnd =
        reinterpret_cast<std::uintptr_t>(memory.BaseAddress) + memory.RegionSize;
    if (regionEnd <= address) {
        return 0;
    }
    return (std::min)(wanted, static_cast<std::size_t>(regionEnd - address));
}

/**
 * Appends every distinct Tiger handle found in a bounded window of one live object.
 *
 * This is what makes the trace self-resolving. A heap pointer means nothing once the process
 * exits, so a capture that records only `r9` can never say *which* model was drawn. A materialized
 * instance, by contrast, carries its own mesh and buffer handles, and a buffer handle resolves
 * offline through the plain (unencrypted) entry tables to exactly one package and entry - turning
 * identification from a blank-probe search over 21,240 models into a table lookup.
 */
[[nodiscard]] std::size_t append_tag_window(std::span<char> event,
                                            std::size_t length,
                                            const char* label,
                                            std::uintptr_t address) noexcept {
    const std::size_t bytes = readable_bytes(address, kTagWindowBytes);
    if (bytes < sizeof(std::uint32_t)) {
        return length;
    }
    std::array<std::uint32_t, kTagSampleLimit> seen{};
    std::size_t found = 0;
    const std::size_t words = bytes / sizeof(std::uint32_t);
    for (std::size_t index = 0; index < words && found < seen.size(); ++index) {
        std::uint32_t value = 0;
        std::memcpy(&value,
                    reinterpret_cast<const void*>(address + index * sizeof(std::uint32_t)),
                    sizeof(value));
        if (value < kTagMin || value > kTagMax) {
            continue;
        }
        if (std::find(seen.begin(), seen.begin() + static_cast<std::ptrdiff_t>(found), value)
            != seen.begin() + static_cast<std::ptrdiff_t>(found)) {
            continue;
        }
        const int written =
            found == 0 ? std::snprintf(event.data() + length,
                                       event.size() - length,
                                       " %s=0x%08X",
                                       label,
                                       value)
                       : std::snprintf(event.data() + length,
                                       event.size() - length,
                                       ",0x%08X",
                                       value);
        if (written <= 0 || static_cast<std::size_t>(written) >= event.size() - length) {
            break;
        }
        seen[found] = value;
        length += static_cast<std::size_t>(written);
        ++found;
    }
    return length;
}

/** Serializes one completed SEntityModel class lookup without changing either output. */
void trace_lookup(const void* caller,
                  void* record,
                  std::uint32_t flags,
                  std::uintptr_t fourthArgument) noexcept {
    if (!capture_active()) {
        return;
    }
    const std::uint32_t sequence = g_eventCount.fetch_add(1, std::memory_order_relaxed);
    if (sequence >= kEventLimit) {
        if (!g_limitReported.exchange(true, std::memory_order_relaxed)) {
            core::log::write(core::log::Channel::client,
                             core::log::Level::warn,
                             "ev=model_class_trace stage=lookup result=limit");
        }
        return;
    }

    const auto callerAddress = reinterpret_cast<std::uintptr_t>(caller);
    const auto recordAddress = reinterpret_cast<std::uintptr_t>(record);
    const bool callerInGame = diagnostics::contains(g_gameRange, callerAddress);
    const bool recordInGame = diagnostics::contains(g_gameRange, recordAddress);
    std::array<char, core::log::kLineCapacity> event{};
    const int written = std::snprintf(
        event.data(),
        event.size(),
        "ev=model_class_trace stage=lookup seq=%u class=0x%08X caller=%s0x%llX "
        "record=%s0x%llX flags=0x%08X r9=0x%llX thread=%lu",
        sequence,
        kEntityModelClass,
        callerInGame ? "rva:" : "abs:",
        static_cast<unsigned long long>(callerInGame ? callerAddress - g_gameRange.base
                                                     : callerAddress),
        recordInGame ? "rva:" : "abs:",
        static_cast<unsigned long long>(recordInGame ? recordAddress - g_gameRange.base
                                                     : recordAddress),
        flags,
        static_cast<unsigned long long>(fourthArgument),
        static_cast<unsigned long>(GetCurrentThreadId()));
    if (written <= 0) {
        return;
    }
    std::size_t length = (std::min)(static_cast<std::size_t>(written), event.size() - 1);
    // The handles come before the stack chain: the stack is now corroborating detail, whereas these
    // are the only field that survives the process and names a concrete entry.
    length = append_tag_window(event, length, "r9tags", fourthArgument);
    length = append_game_stack(event, length);
    core::log::write(core::log::Channel::client,
                     core::log::Level::info,
                     std::string_view(event.data(), length));
}

/** Records the tag supplied to the constructor selected by an SEntityModel class lookup. */
void trace_resource(const void* caller,
                    std::uint32_t tag,
                    const void* destination,
                    const void* payload) noexcept {
    const std::uint32_t sequence = g_eventCount.fetch_add(1, std::memory_order_relaxed);
    if (sequence >= kEventLimit) {
        if (!g_limitReported.exchange(true, std::memory_order_relaxed)) {
            core::log::write(core::log::Channel::client,
                             core::log::Level::warn,
                             "ev=model_class_trace stage=resource result=limit");
        }
        return;
    }
    const auto callerAddress = reinterpret_cast<std::uintptr_t>(caller);
    const bool callerInGame = diagnostics::contains(g_gameRange, callerAddress);
    std::array<char, core::log::kLineCapacity> event{};
    const int written = std::snprintf(
        event.data(),
        event.size(),
        "ev=model_class_trace stage=resource seq=%u class=0x%08X tag=0x%08X "
        "caller=%s0x%llX destination=0x%llX payload=0x%llX thread=%lu",
        sequence,
        kEntityModelClass,
        tag,
        callerInGame ? "rva:" : "abs:",
        static_cast<unsigned long long>(callerInGame ? callerAddress - g_gameRange.base
                                                     : callerAddress),
        static_cast<unsigned long long>(reinterpret_cast<std::uintptr_t>(destination)),
        static_cast<unsigned long long>(reinterpret_cast<std::uintptr_t>(payload)),
        static_cast<unsigned long>(GetCurrentThreadId()));
    if (written <= 0) {
        return;
    }
    std::size_t length = (std::min)(static_cast<std::size_t>(written), event.size() - 1);
    // `payload` is the serialized source and `destination` the object being built. Either may carry
    // the mesh handles, and which one does is exactly what this capture is meant to establish.
    length = append_tag_window(event,
                               length,
                               "paytags",
                               reinterpret_cast<std::uintptr_t>(payload));
    length = append_tag_window(event,
                               length,
                               "dsttags",
                               reinterpret_cast<std::uintptr_t>(destination));
    length = append_game_stack(event, length);
    core::log::write(core::log::Channel::client,
                     core::log::Level::info,
                     std::string_view(event.data(), length));
}

/**
 * Every distinct reflected class the game asks for, logged once each.
 *
 * The trace has only ever detoured `0x808073A5` (SEntityModel), on the assumption that characters
 * and items are built from it. Four captures - inspect, two Tower runs, and one covering login,
 * character select and the inventory screen - contain **no player body and no weapon**, while the
 * weapon renders solidly on screen throughout. Either the assumption is wrong, or that path is
 * reached some other way.
 *
 * Rather than guess the next class id, this records what the client actually resolves. Each class
 * is emitted once with its call stack, which is bounded and cheap, and the histogram at the end
 * names the classes worth detouring next.
 */
constexpr std::size_t kClassTableSize = 512;
std::array<std::atomic_uint32_t, kClassTableSize> g_seenClasses{};
std::atomic_uint32_t g_seenCount{};

/** Logs a class id the first time it is ever requested. Races may log one twice; harmless. */
void note_class(std::uint32_t classId, const void* caller) noexcept {
    const std::uint32_t seen = g_seenCount.load(std::memory_order_relaxed);
    for (std::uint32_t index = 0; index < seen && index < kClassTableSize; ++index) {
        if (g_seenClasses[index].load(std::memory_order_relaxed) == classId) {
            return;
        }
    }
    if (seen >= kClassTableSize) {
        return;
    }
    g_seenClasses[seen].store(classId, std::memory_order_relaxed);
    g_seenCount.store(seen + 1, std::memory_order_relaxed);

    const auto callerAddress = reinterpret_cast<std::uintptr_t>(caller);
    const bool inGame = diagnostics::contains(g_gameRange, callerAddress);
    std::array<char, core::log::kLineCapacity> event{};
    const int written = std::snprintf(
        event.data(), event.size(),
        "ev=class_census stage=first class=0x%08X ordinal=%u caller=%s0x%llX thread=%lu",
        classId,
        seen,
        inGame ? "rva:" : "abs:",
        static_cast<unsigned long long>(inGame ? callerAddress - g_gameRange.base : callerAddress),
        static_cast<unsigned long>(GetCurrentThreadId()));
    if (written <= 0) {
        return;
    }
    std::size_t length = (std::min)(static_cast<std::size_t>(written), event.size() - 1);
    length = append_game_stack(event, length);
    core::log::write(core::log::Channel::client,
                     core::log::Level::info,
                     std::string_view(event.data(), length));
}

/** Exact reflected-class lookup ABI replacement. */
__declspec(noinline) void __fastcall class_lookup(std::uint32_t classId,
                                                   void** recordOutput,
                                                   std::uint32_t* flagsOutput,
                                                   std::uintptr_t fourthArgument) noexcept {
    const ClassLookup next = original<ClassLookup>(HookSlot::classLookup);
    if (next != nullptr) {
        next(classId, recordOutput, flagsOutput, fourthArgument);
    }
    if (capture_active()) {
        note_class(classId, _ReturnAddress());
    }
    if (classId == kEntityModelClass && capture_active()) {
        // A metadata helper may perform another reflected lookup before invoking the constructor.
        // Keep the model expectation for one short call-chain window instead of letting that
        // intervening lookup erase the only link between the class id and the concrete tag.
        g_expectEntityModelConstructor = true;
        g_entityModelExpectationDeadline = GetTickCount64() + 100;
    } else if (!capture_active()) {
        g_expectEntityModelConstructor = false;
        g_entityModelExpectationDeadline = 0;
    }
    if (classId != kEntityModelClass || !capture_active()) {
        return;
    }
    void* const record = recordOutput != nullptr ? *recordOutput : nullptr;
    const std::uint32_t flags = flagsOutput != nullptr ? *flagsOutput : 0;
    trace_lookup(_ReturnAddress(), record, flags, fourthArgument);
}

/**
 * Constructor selected by the reflected lookup. R8 carries the concrete Tiger tag at every
 * materialization call site, including cached resources which never reach ReadFile.
 */
__declspec(noinline) void* __fastcall resource_constructor(void* destination,
                                                            std::uint32_t mode,
                                                            std::uint32_t tag,
                                                            const void* payload) noexcept {
    const ResourceConstructor next =
        original<ResourceConstructor>(HookSlot::resourceConstructor);
    void* const result =
        next != nullptr ? next(destination, mode, tag, payload) : destination;
    if (g_expectEntityModelConstructor && capture_active()
        && GetTickCount64() <= g_entityModelExpectationDeadline) {
        g_expectEntityModelConstructor = false;
        g_entityModelExpectationDeadline = 0;
        trace_resource(_ReturnAddress(), tag, destination, payload);
    } else if (g_expectEntityModelConstructor
               && GetTickCount64() > g_entityModelExpectationDeadline) {
        g_expectEntityModelConstructor = false;
        g_entityModelExpectationDeadline = 0;
    }
    return result;
}

} // namespace

/** Attaches the dormant SEntityModel class-lookup trace. */
bool install() noexcept {
    if (g_installed.load(std::memory_order_acquire)) {
        return true;
    }
    if (!diagnostics::module_range(GetModuleHandleW(nullptr), g_gameRange)) {
        return false;
    }
    std::byte* const target =
        patterns::scan_main_image_unique(kClassLookup, "model_class_lookup");
    if (target == nullptr) {
        g_gameRange = {};
        core::log::write(core::log::Channel::client,
                         core::log::Level::warn,
                         "ev=model_class_trace stage=install result=fail reason=signature");
        return false;
    }
    void* constructor = nullptr;
    std::uint32_t constructorFlags = 0;
    reinterpret_cast<ClassLookup>(target)(
        kEntityModelClass, &constructor, &constructorFlags, 0);
    const auto constructorAddress = reinterpret_cast<std::uintptr_t>(constructor);
    if (!diagnostics::contains(g_gameRange, constructorAddress)) {
        g_gameRange = {};
        core::log::write(core::log::Channel::client,
                         core::log::Level::warn,
                         "ev=model_class_trace stage=install result=fail reason=constructor");
        return false;
    }
    const std::array<hooking::detour::Spec, kHookCount> specs{
        hooking::detour::Spec{target, reinterpret_cast<void*>(&class_lookup)},
        hooking::detour::Spec{constructor, reinterpret_cast<void*>(&resource_constructor)},
    };
    if (!hooking::detour::install(specs, g_handles)) {
        g_handles = {};
        g_gameRange = {};
        core::log::write(core::log::Channel::client,
                         core::log::Level::warn,
                         "ev=model_class_trace stage=install result=fail reason=attach");
        return false;
    }
    g_eventCount.store(0, std::memory_order_relaxed);
    g_limitReported.store(false, std::memory_order_relaxed);
    g_installed.store(true, std::memory_order_release);
    core::log::write(core::log::Channel::client,
                     core::log::Level::info,
                     "ev=model_class_trace stage=install result=ok class=0x808073A5 "
                     "resource_tag=enabled capture=from_start");
    return true;
}

/** Detaches the SEntityModel class-lookup trace. */
bool uninstall() noexcept {
    if (!g_installed.load(std::memory_order_acquire)) {
        return true;
    }
    if (!hooking::detour::uninstall(g_handles)) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::warn,
                         "ev=model_class_trace stage=uninstall result=fail");
        return false;
    }
    g_installed.store(false, std::memory_order_release);
    g_handles = {};
    g_gameRange = {};
    return true;
}

} // namespace sunrise::client::hooks::model_trace
