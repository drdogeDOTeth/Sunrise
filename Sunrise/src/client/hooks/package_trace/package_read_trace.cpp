#include "package_read_trace.h"

#include <Windows.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <intrin.h>
#include <span>
#include <string_view>

#include "../../../core/logging/log.h"
#include "../../diagnostics/module_range.h"
#include "../../hooking/detour.h"

namespace sunrise::client::hooks::package_trace {
namespace {

/** Package reads use both forms in this client; attaching them together avoids a blind spot. */
enum class HookSlot : std::size_t {
    readFile,
    readFileEx,
    count,
};

inline constexpr std::size_t kHookCount = static_cast<std::size_t>(HookSlot::count);
/**
 * Bounded against a forgotten toggle, but large enough to span a world transition.
 *
 * 8,192 was sized for entering inspect from a static screen. A character-select-to-orbit load
 * outruns that long before the interesting part: `resourcerer process events` stalls roughly
 * twenty seconds into `cleanup`, and a capture that stops early records only the healthy prologue.
 * The hotkey is polled from the DXGI Present hook, so it cannot be pressed once the mainloop
 * blocks - the capture has to be armed beforehand and still be running when the stall arrives.
 *
 * The log rotates once per launch rather than by size, so a long capture costs disk, not history.
 */
constexpr std::uint32_t kCaptureLimit = 200000;
/** Returned for a read this module did not record, so its outcome is not reported either. */
constexpr std::uint32_t kNotTraced = 0xFFFFFFFFU;
/** Enough frames to pass through KernelBase/Detours and retain several game callers. */
constexpr ULONG kStackFrameLimit = 16;
/** Only game-image frames are serialized; four are enough to identify the loader chain. */
constexpr std::size_t kSerializedFrameLimit = 4;
/** Final paths are local package paths, but the buffer is bounded for a malformed handle. */
constexpr std::size_t kPathCapacity = 1024;
/** Tiger filenames are much shorter than this; longer names are safely truncated by conversion. */
constexpr std::size_t kFileNameCapacity = 192;
constexpr wchar_t kKernelModule[] = L"kernel32.dll";
constexpr char kReadFileExport[] = "ReadFile";
constexpr char kReadFileExExport[] = "ReadFileEx";

using ReadFileFunction = BOOL(WINAPI*)(HANDLE, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);
using ReadFileExFunction =
    BOOL(WINAPI*)(HANDLE, LPVOID, DWORD, LPOVERLAPPED, LPOVERLAPPED_COMPLETION_ROUTINE);

std::array<hooking::detour::Handle, kHookCount> g_handles{};
std::array<void*, kHookCount> g_targets{};
std::atomic_bool g_installed{};
std::atomic_bool g_capturing{};
std::atomic_bool g_limitReported{};
std::atomic_uint32_t g_eventCount{};
diagnostics::ModuleRange g_gameRange{};
HMODULE g_kernelModule{};

/** @return Lower-case ASCII for one path character; Tiger filenames are ASCII. */
[[nodiscard]] constexpr wchar_t lower_ascii(wchar_t value) noexcept {
    return value >= L'A' && value <= L'Z' ? static_cast<wchar_t>(value + (L'a' - L'A')) : value;
}

/** @return True when `text` ends in `suffix`, ignoring ASCII case. */
[[nodiscard]] bool ends_with_ascii_case(std::wstring_view text,
                                        std::wstring_view suffix) noexcept {
    if (suffix.size() > text.size()) {
        return false;
    }
    const std::size_t start = text.size() - suffix.size();
    for (std::size_t index = 0; index < suffix.size(); ++index) {
        if (lower_ascii(text[start + index]) != lower_ascii(suffix[index])) {
            return false;
        }
    }
    return true;
}

/** @return True when `text` contains `needle`, ignoring ASCII case. */
[[nodiscard]] bool contains_ascii_case(std::wstring_view text,
                                       std::wstring_view needle) noexcept {
    if (needle.empty() || needle.size() > text.size()) {
        return false;
    }
    for (std::size_t start = 0; start + needle.size() <= text.size(); ++start) {
        bool equal = true;
        for (std::size_t index = 0; index < needle.size(); ++index) {
            if (lower_ascii(text[start + index]) != lower_ascii(needle[index])) {
                equal = false;
                break;
            }
        }
        if (equal) {
            return true;
        }
    }
    return false;
}

/** @return Basename view inside one final path. */
[[nodiscard]] std::wstring_view basename(std::wstring_view path) noexcept {
    std::size_t start = path.size();
    while (start != 0 && path[start - 1] != L'\\' && path[start - 1] != L'/') {
        --start;
    }
    return path.substr(start);
}

/**
 * Converts the package basename to UTF-8 for one structured log event.
 * @return Converted byte count, excluding the terminator.
 */
[[nodiscard]] std::size_t utf8_name(std::wstring_view name,
                                    std::span<char> output) noexcept {
    if (name.empty() || output.size() < 2) {
        return 0;
    }
    const int converted = WideCharToMultiByte(CP_UTF8,
                                              0,
                                              name.data(),
                                              static_cast<int>(name.size()),
                                              output.data(),
                                              static_cast<int>(output.size() - 1),
                                              nullptr,
                                              nullptr);
    if (converted <= 0) {
        return 0;
    }
    output[static_cast<std::size_t>(converted)] = '\0';
    return static_cast<std::size_t>(converted);
}

/** @return Original export/trampoline for one slot, even during a detach transition. */
template <typename Function> [[nodiscard]] Function original(HookSlot slot) noexcept {
    const std::size_t index = static_cast<std::size_t>(slot);
    void* entry = g_handles[index].original;
    if (entry == nullptr) {
        entry = g_targets[index];
    }
    return reinterpret_cast<Function>(entry);
}

/** @return Byte offset named by an overlapped read, or the current synchronous file position. */
[[nodiscard]] bool read_offset(HANDLE file,
                               const OVERLAPPED* overlapped,
                               std::uint64_t& output) noexcept {
    if (overlapped != nullptr) {
        output = (static_cast<std::uint64_t>(overlapped->OffsetHigh) << 32U)
                 | static_cast<std::uint64_t>(overlapped->Offset);
        return true;
    }
    LARGE_INTEGER zero{};
    LARGE_INTEGER position{};
    if (SetFilePointerEx(file, zero, &position, FILE_CURRENT) == FALSE) {
        return false;
    }
    output = static_cast<std::uint64_t>(position.QuadPart);
    return true;
}

/** Serializes the first few captured return addresses that belong to destiny2.exe. */
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

/**
 * Records one game-originated package read before it is forwarded unchanged.
 * @return The sequence number this read was recorded under, or `kNotTraced`.
 */
[[nodiscard]] std::uint32_t trace_read(std::string_view api,
                                       HANDLE file,
                                       DWORD byteCount,
                                       const OVERLAPPED* overlapped,
                                       const void* caller) noexcept {
    const auto callerAddress = reinterpret_cast<std::uintptr_t>(caller);
    if (!g_capturing.load(std::memory_order_relaxed)
        || !diagnostics::contains(g_gameRange, callerAddress)) {
        return kNotTraced;
    }

    std::array<wchar_t, kPathCapacity> path{};
    const DWORD pathLength = GetFinalPathNameByHandleW(
        file, path.data(), static_cast<DWORD>(path.size()), FILE_NAME_NORMALIZED);
    if (pathLength == 0 || pathLength >= path.size()) {
        return kNotTraced;
    }
    const std::wstring_view pathView(path.data(), pathLength);
    if (!contains_ascii_case(pathView, L"\\packages\\")
        || !ends_with_ascii_case(pathView, L".pkg")) {
        return kNotTraced;
    }

    std::uint64_t offset = 0;
    if (!read_offset(file, overlapped, offset)) {
        return kNotTraced;
    }
    const std::uint32_t sequence = g_eventCount.fetch_add(1, std::memory_order_relaxed);
    if (sequence >= kCaptureLimit) {
        if (!g_limitReported.exchange(true, std::memory_order_relaxed)) {
            std::array<char, 96> reached{};
            const int length = std::snprintf(reached.data(),
                                             reached.size(),
                                             "ev=package_trace stage=read result=limit events=%u",
                                             kCaptureLimit);
            if (length > 0) {
                core::log::write(core::log::Channel::client,
                                 core::log::Level::warn,
                                 std::string_view(reached.data(),
                                                  static_cast<std::size_t>(length)));
            }
        }
        return kNotTraced;
    }

    std::array<char, kFileNameCapacity> name{};
    const std::size_t nameLength = utf8_name(basename(pathView), name);
    if (nameLength == 0) {
        return kNotTraced;
    }
    std::array<char, core::log::kLineCapacity> event{};
    const int written = std::snprintf(
        event.data(),
        event.size(),
        "ev=package_trace stage=read seq=%u api=%.*s file=%.*s offset=0x%llX size=%lu "
        "caller=0x%llX thread=%lu",
        sequence,
        static_cast<int>(api.size()),
        api.data(),
        static_cast<int>(nameLength),
        name.data(),
        static_cast<unsigned long long>(offset),
        static_cast<unsigned long>(byteCount),
        static_cast<unsigned long long>(callerAddress - g_gameRange.base),
        static_cast<unsigned long>(GetCurrentThreadId()));
    if (written <= 0) {
        return kNotTraced;
    }
    std::size_t length = (std::min)(static_cast<std::size_t>(written), event.size() - 1);
    length = append_game_stack(event, length);
    core::log::write(core::log::Channel::client,
                     core::log::Level::info,
                     std::string_view(event.data(), length));
    return sequence;
}

/**
 * Reports a read the operating system refused, which the pre-call record alone cannot show.
 *
 * Every package read in this client is `ReadFileEx`, whose data arrives through a completion
 * routine rather than a return value. A refusal there is silent by construction: the caller is
 * left waiting for a completion that will never run, which is indistinguishable from a slow disk
 * until a watchdog fires twenty seconds later. `ERROR_IO_PENDING` is not a refusal - it is the
 * normal answer for an overlapped `ReadFile` - so it is not reported.
 *
 * @param api Name of the entry point that was called.
 * @param sequence Sequence number of the matching read record.
 * @param error Last error captured immediately after the call.
 */
void trace_failure(std::string_view api, std::uint32_t sequence, DWORD error) noexcept {
    if (sequence == kNotTraced || error == ERROR_IO_PENDING) {
        return;
    }
    std::array<char, 160> line{};
    const int written = std::snprintf(line.data(),
                                      line.size(),
                                      "ev=package_trace stage=read_result seq=%u api=%.*s "
                                      "result=fail error=%lu",
                                      sequence,
                                      static_cast<int>(api.size()),
                                      api.data(),
                                      static_cast<unsigned long>(error));
    if (written <= 0) {
        return;
    }
    const std::size_t length = (std::min)(static_cast<std::size_t>(written), line.size() - 1);
    core::log::write(
        core::log::Channel::client, core::log::Level::error, std::string_view(line.data(), length));
}

/** Exact ReadFile ABI replacement. */
__declspec(noinline) BOOL WINAPI read_file(HANDLE file,
                                           LPVOID buffer,
                                           DWORD byteCount,
                                           LPDWORD bytesRead,
                                           LPOVERLAPPED overlapped) noexcept {
    const std::uint32_t sequence =
        trace_read("ReadFile", file, byteCount, overlapped, _ReturnAddress());
    const ReadFileFunction next = original<ReadFileFunction>(HookSlot::readFile);
    if (next == nullptr) {
        return FALSE;
    }
    const BOOL result = next(file, buffer, byteCount, bytesRead, overlapped);
    // The game reads its own last error after this returns, so the reporting path must not be
    // allowed to overwrite it.
    const DWORD error = GetLastError();
    if (result == FALSE) {
        trace_failure("ReadFile", sequence, error);
    }
    SetLastError(error);
    return result;
}

/** Exact ReadFileEx ABI replacement. */
__declspec(noinline) BOOL WINAPI read_file_ex(HANDLE file,
                                              LPVOID buffer,
                                              DWORD byteCount,
                                              LPOVERLAPPED overlapped,
                                              LPOVERLAPPED_COMPLETION_ROUTINE completion) noexcept {
    const std::uint32_t sequence =
        trace_read("ReadFileEx", file, byteCount, overlapped, _ReturnAddress());
    const ReadFileExFunction next = original<ReadFileExFunction>(HookSlot::readFileEx);
    if (next == nullptr) {
        return FALSE;
    }
    const BOOL result = next(file, buffer, byteCount, overlapped, completion);
    const DWORD error = GetLastError();
    if (result == FALSE) {
        trace_failure("ReadFileEx", sequence, error);
    }
    SetLastError(error);
    return result;
}

/** Releases the retained System32 module after every detour is gone. */
void release_module() noexcept {
    if (g_kernelModule != nullptr) {
        (void)FreeLibrary(g_kernelModule);
        g_kernelModule = nullptr;
    }
}

} // namespace

/** Attaches dormant diagnostic guards to both file-read exports. */
bool install() noexcept {
    if (g_installed.load(std::memory_order_acquire)) {
        return true;
    }
    if (!diagnostics::module_range(GetModuleHandleW(nullptr), g_gameRange)) {
        return false;
    }
    g_kernelModule = LoadLibraryExW(kKernelModule, nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (g_kernelModule == nullptr) {
        g_gameRange = {};
        return false;
    }
    g_targets[static_cast<std::size_t>(HookSlot::readFile)] =
        reinterpret_cast<void*>(GetProcAddress(g_kernelModule, kReadFileExport));
    g_targets[static_cast<std::size_t>(HookSlot::readFileEx)] =
        reinterpret_cast<void*>(GetProcAddress(g_kernelModule, kReadFileExExport));
    for (void* target : g_targets) {
        if (target == nullptr) {
            g_targets = {};
            g_gameRange = {};
            release_module();
            return false;
        }
    }

    const std::array specs{
        hooking::detour::Spec{g_targets[static_cast<std::size_t>(HookSlot::readFile)],
                              reinterpret_cast<void*>(&read_file)},
        hooking::detour::Spec{g_targets[static_cast<std::size_t>(HookSlot::readFileEx)],
                              reinterpret_cast<void*>(&read_file_ex)},
    };
    static_assert(specs.size() == kHookCount);
    if (!hooking::detour::install(specs, g_handles)) {
        g_targets = {};
        g_gameRange = {};
        release_module();
        core::log::write(core::log::Channel::client,
                         core::log::Level::warn,
                         "ev=package_trace stage=install result=fail reason=attach");
        return false;
    }

    g_installed.store(true, std::memory_order_release);
    std::array<char, 96> ready{};
    const int length = std::snprintf(ready.data(),
                                     ready.size(),
                                     "ev=package_trace stage=install result=ok toggle=F8 limit=%u",
                                     kCaptureLimit);
    if (length > 0) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::info,
                         std::string_view(ready.data(), static_cast<std::size_t>(length)));
    }
    return true;
}

/** Detaches both file-read guards. */
bool uninstall() noexcept {
    g_capturing.store(false, std::memory_order_release);
    if (!g_installed.load(std::memory_order_acquire)) {
        release_module();
        return true;
    }
    if (!hooking::detour::uninstall(g_handles)) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::warn,
                         "ev=package_trace stage=uninstall result=fail");
        return false;
    }
    g_installed.store(false, std::memory_order_release);
    g_targets = {};
    g_gameRange = {};
    release_module();
    return true;
}

/** Toggles one bounded capture on each F8 transition. */
void poll_hotkey() noexcept {
    if (!g_installed.load(std::memory_order_acquire)
        || (GetAsyncKeyState(VK_F8) & 1) == 0) {
        return;
    }
    const bool start = !g_capturing.load(std::memory_order_relaxed);
    if (start) {
        g_eventCount.store(0, std::memory_order_relaxed);
        g_limitReported.store(false, std::memory_order_relaxed);
        g_capturing.store(true, std::memory_order_release);
        core::log::write(core::log::Channel::client,
                         core::log::Level::info,
                         "ev=package_trace stage=capture result=started key=F8");
        return;
    }
    g_capturing.store(false, std::memory_order_release);
    const std::uint32_t events =
        (std::min)(g_eventCount.load(std::memory_order_relaxed), kCaptureLimit);
    std::array<char, 128> event{};
    const int written = std::snprintf(event.data(),
                                      event.size(),
                                      "ev=package_trace stage=capture result=stopped key=F8 events=%u",
                                      events);
    if (written > 0) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::info,
                         std::string_view(event.data(), static_cast<std::size_t>(written)));
    }
}

/** @return True while the F8 capture window is open. */
bool capturing() noexcept {
    return g_capturing.load(std::memory_order_acquire);
}

} // namespace sunrise::client::hooks::package_trace
