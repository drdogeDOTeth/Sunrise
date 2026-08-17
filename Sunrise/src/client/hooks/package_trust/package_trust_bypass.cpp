#include "package_trust_bypass.h"

#include <Windows.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string_view>

#include "../../../core/logging/log.h"
#include "../../hooking/detour.h"
#include "../../patterns/image_scan.h"

namespace sunrise::client::hooks::package_trust {
namespace {

using patterns::scan_main_image_unique;
using patterns::signature;
using patterns::signature_length;

/**
 * Native package-header validator. The matched prologue tests its sixth argument and returns -93
 * when RSA verification did not mark the header trusted. The following bytes enter the ordinary
 * structural checks, keeping this target distinct from the RSA implementation itself.
 */
constexpr std::string_view kValidatorSignatureText =
    "40 53 48 83 EC 20 80 7C 24 58 00 44 0F B7 DA 4C 8B D1 BB 01 00 00 00 "
    "75 0D BB A3 FF FF FF 8B C3 48 83 C4 20 5B C3";
/** Masked form of the validator text, which is the form the image scan takes. */
constexpr auto kValidatorSignature =
    signature<signature_length(kValidatorSignatureText)>(kValidatorSignatureText);

/**
 * The patchable registrar's extended-header authentication failure. Native code loads -89 here,
 * then joins the common result/cleanup path. The site is unique in this client build.
 */
constexpr std::string_view kExtendedHeaderFailureText = "B8 A7 FF FF FF E9 ? ? ? ?";
constexpr auto kExtendedHeaderFailure =
    signature<signature_length(kExtendedHeaderFailureText)>(kExtendedHeaderFailureText);

/**
 * The patchable registrar's remaining rejection, seen as
 * "Patchable package registration failed with result  (-86)" when the game is offered a package
 * this project wrote. -86 is 0xFFFFFFAA, and the site is expected to take the same shape as the
 * -89 failure above: load the result, then join the common cleanup path.
 *
 * Unlike the two failures above, this one is not known to be unique, so it is resolved and reported
 * rather than assumed. The game is VMProtect-packed, so the site cannot be located by scanning the
 * file on disk; only the unpacked in-memory image carries it.
 */
constexpr std::string_view kPatchableRegistrationFailureText = "B8 AA FF FF FF E9 ? ? ? ?";
constexpr auto kPatchableRegistrationFailure =
    signature<signature_length(kPatchableRegistrationFailureText)>(
        kPatchableRegistrationFailureText);

/** The bare result load, counted only to tell a wrong pattern apart from a repeating one. */
constexpr std::string_view kPatchableRegistrationResultText = "B8 AA FF FF FF";
constexpr auto kPatchableRegistrationResult =
    signature<signature_length(kPatchableRegistrationResultText)>(
        kPatchableRegistrationResultText);

/** Bound on reported match addresses. The count matters; the addresses are for diagnosis. */
constexpr std::size_t kResultSiteReportLimit = 8;

/**
 * Cached-data authentication gate used while registering/loading base packages. The hash routine
 * returns a boolean in AL. Native code conditionally jumps to the ordinary success continuation;
 * otherwise it enters the unique "Failed to validate cached data hash" error path with -89.
 */
constexpr std::string_view kCachedDataHashGateText =
    "84 C0 0F 85 ? ? ? ? E9 ? ? ? ? 48 8B 45 48 89 08";
/** Masked form of the gate text, which is the form the image scan takes. */
constexpr auto kCachedDataHashGate =
    signature<signature_length(kCachedDataHashGateText)>(kCachedDataHashGateText);

/** Only the MOV EAX immediate changes; the native continuation remains untouched. */
constexpr std::size_t kResultImmediateOffset = 1;
constexpr std::array<std::byte, 4> kSuccessResult{
    std::byte{0x01}, std::byte{0x00}, std::byte{0x00}, std::byte{0x00}};

/** Replace JNZ rel32 (0F 85) with NOP; JMP rel32 (90 E9), retaining its native destination. */
constexpr std::size_t kCachedDataBranchOffset = 2;
constexpr std::array<std::byte, 2> kAlwaysTakeSuccessBranch{std::byte{0x90}, std::byte{0xE9}};

/** ABI recovered from the validator's native call site. */
using ValidateHeader = std::int32_t(__fastcall*)(const std::uint32_t* validationMask,
                                                 std::uint16_t packageGroup,
                                                 std::uint64_t buildSignature,
                                                 std::int32_t expectedFileSize,
                                                 std::uint16_t localeToken,
                                                 std::uint8_t rsaTrusted,
                                                 const void* header) noexcept;

hooking::detour::Handle g_handle{};
std::byte* g_extendedHeaderResult{};
std::array<std::byte, kSuccessResult.size()> g_extendedHeaderOriginal{};
std::byte* g_cachedDataBranch{};
std::array<std::byte, kAlwaysTakeSuccessBranch.size()> g_cachedDataBranchOriginal{};
std::byte* g_registrationResult{};
std::array<std::byte, kSuccessResult.size()> g_registrationOriginal{};

/** Writes instruction bytes and restores the page's original protection. */
template <std::size_t Size>
[[nodiscard]] bool write_code(std::byte* destination,
                              const std::array<std::byte, Size>& value) noexcept {
    if (destination == nullptr) {
        return false;
    }
    DWORD originalProtection = 0;
    if (VirtualProtect(destination, value.size(), PAGE_EXECUTE_READWRITE, &originalProtection)
        == FALSE) {
        return false;
    }
    std::memcpy(destination, value.data(), value.size());
    FlushInstructionCache(GetCurrentProcess(), destination, value.size());
    DWORD ignored = 0;
    return VirtualProtect(destination, value.size(), originalProtection, &ignored) != FALSE;
}

/** Runs every native validation rule while forcing only the RSA result to trusted. */
std::int32_t __fastcall validate_header(const std::uint32_t* validationMask,
                                        std::uint16_t packageGroup,
                                        std::uint64_t buildSignature,
                                        std::int32_t expectedFileSize,
                                        std::uint16_t localeToken,
                                        std::uint8_t,
                                        const void* header) noexcept {
    if (header != nullptr) {
        const auto* const bytes = static_cast<const std::byte*>(header);
        std::uint16_t packageId = 0;
        std::uint16_t patchId = 0;
        std::uint32_t headerFileSize = 0;
        std::memcpy(&packageId, bytes + 0x04, sizeof packageId);
        std::memcpy(&patchId, bytes + 0x20, sizeof patchId);
        std::memcpy(&headerFileSize, bytes + 0x164, sizeof headerFileSize);
        if (headerFileSize != static_cast<std::uint32_t>(expectedFileSize)) {
            std::array<char, 256> event{};
            const int length = std::snprintf(event.data(),
                                             event.size(),
                                             "ev=package_trust stage=header_size result=mismatch "
                                             "package=0x%04X patch=%u header=%u expected=%u",
                                             packageId,
                                             patchId,
                                             headerFileSize,
                                             static_cast<std::uint32_t>(expectedFileSize));
            if (length > 0) {
                core::log::write(core::log::Channel::client,
                                 core::log::Level::error,
                                 std::string_view(event.data(),
                                                  (std::min)(static_cast<std::size_t>(length),
                                                             event.size() - 1)));
            }
        }
    }
    const auto original = reinterpret_cast<ValidateHeader>(g_handle.original);
    return original(
        validationMask, packageGroup, buildSignature, expectedFileSize, localeToken, 1, header);
}

/**
 * Forces the patchable registrar's -86 rejection to report success.
 *
 * Deliberately non-fatal. The -93 and -89 bypasses are load-bearing and already proven on this
 * build; a pattern that does not resolve here must not take them down with it. A miss is reported
 * with the raw site count so the next attempt starts from evidence rather than from a guess.
 */
void install_registration_bypass() noexcept {
    std::array<std::byte*, kResultSiteReportLimit> sites{};
    const std::size_t total =
        patterns::count_main_image_matches(kPatchableRegistrationResult,
                                           "package_registration_result",
                                           std::span(sites));
    std::byte* const failure = scan_main_image_unique(kPatchableRegistrationFailure,
                                                      "package_registration_failure");
    std::array<char, 192> event{};
    if (failure == nullptr) {
        const int length = std::snprintf(event.data(),
                                         event.size(),
                                         "ev=package_trust stage=registration result=unresolved "
                                         "sites=%zu reason=%s",
                                         total,
                                         total == 0 ? "no_result_load" : "not_unique_with_jump");
        if (length > 0) {
            core::log::write(core::log::Channel::client,
                             core::log::Level::warn,
                             std::string_view(event.data(), static_cast<std::size_t>(length)));
        }
        return;
    }
    g_registrationResult = failure + kResultImmediateOffset;
    std::memcpy(g_registrationOriginal.data(), g_registrationResult, g_registrationOriginal.size());
    if (!write_code(g_registrationResult, kSuccessResult)) {
        g_registrationResult = nullptr;
        core::log::write(core::log::Channel::client,
                         core::log::Level::warn,
                         "ev=package_trust stage=registration result=write_fail");
        return;
    }
    const int length = std::snprintf(event.data(),
                                     event.size(),
                                     "ev=package_trust stage=registration result=ok sites=%zu",
                                     total);
    if (length > 0) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::info,
                         std::string_view(event.data(), static_cast<std::size_t>(length)));
    }
}

} // namespace

/** Attaches the native package-header trust bypass. */
bool install() noexcept {
    if (g_handle.attached) {
        return true;
    }
    std::byte* const target =
        scan_main_image_unique(kValidatorSignature, "package_header_validator");
    std::byte* const extendedHeaderFailure =
        scan_main_image_unique(kExtendedHeaderFailure, "package_extended_header_failure");
    std::byte* const cachedDataHashGate =
        scan_main_image_unique(kCachedDataHashGate, "package_cached_data_hash_gate");
    if (target == nullptr || extendedHeaderFailure == nullptr || cachedDataHashGate == nullptr) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::error,
                         "ev=package_trust stage=resolve result=fail");
        return false;
    }
    const hooking::detour::Spec spec{target, reinterpret_cast<void*>(&validate_header)};
    if (!hooking::detour::install(spec, g_handle)) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::error,
                         "ev=package_trust stage=attach result=fail");
        return false;
    }
    g_extendedHeaderResult = extendedHeaderFailure + kResultImmediateOffset;
    std::memcpy(
        g_extendedHeaderOriginal.data(), g_extendedHeaderResult, g_extendedHeaderOriginal.size());
    if (!write_code(g_extendedHeaderResult, kSuccessResult)) {
        (void)hooking::detour::uninstall(g_handle);
        g_extendedHeaderResult = nullptr;
        core::log::write(core::log::Channel::client,
                         core::log::Level::error,
                         "ev=package_trust stage=extended_header result=fail");
        return false;
    }
    g_cachedDataBranch = cachedDataHashGate + kCachedDataBranchOffset;
    std::memcpy(
        g_cachedDataBranchOriginal.data(), g_cachedDataBranch, g_cachedDataBranchOriginal.size());
    if (!write_code(g_cachedDataBranch, kAlwaysTakeSuccessBranch)) {
        (void)write_code(g_extendedHeaderResult, g_extendedHeaderOriginal);
        (void)hooking::detour::uninstall(g_handle);
        g_extendedHeaderResult = nullptr;
        g_cachedDataBranch = nullptr;
        core::log::write(core::log::Channel::client,
                         core::log::Level::error,
                         "ev=package_trust stage=cached_data result=fail");
        return false;
    }
    core::log::write(core::log::Channel::client,
                     core::log::Level::info,
                     "ev=package_trust stage=attach result=ok mode=package_integrity_bypass");
    install_registration_bypass();
    return true;
}

/** Detaches the native package-header trust bypass. */
bool uninstall() noexcept {
    bool restored = true;
    if (g_registrationResult != nullptr) {
        const bool registrationRestored = write_code(g_registrationResult, g_registrationOriginal);
        restored = restored && registrationRestored;
        if (registrationRestored) {
            g_registrationResult = nullptr;
        }
    }
    if (g_cachedDataBranch != nullptr) {
        const bool cachedDataRestored = write_code(g_cachedDataBranch, g_cachedDataBranchOriginal);
        restored = restored && cachedDataRestored;
        if (cachedDataRestored) {
            g_cachedDataBranch = nullptr;
        }
    }
    if (g_extendedHeaderResult != nullptr) {
        const bool extendedHeaderRestored =
            write_code(g_extendedHeaderResult, g_extendedHeaderOriginal);
        restored = restored && extendedHeaderRestored;
        if (extendedHeaderRestored) {
            g_extendedHeaderResult = nullptr;
        }
    }
    const bool detached = !g_handle.attached || hooking::detour::uninstall(g_handle);
    return restored && detached;
}

/** @return True while the validator detour is attached. */
bool is_installed() noexcept {
    return g_handle.attached && g_extendedHeaderResult != nullptr && g_cachedDataBranch != nullptr;
}

} // namespace sunrise::client::hooks::package_trust
