#include <Windows.h>

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <span>
#include <string_view>
#include <vector>

#include "../../../../core/filesystem/path.h"
#include "../../../../core/logging/log.h"
#include "build.h"
#include "internal.h"

namespace sunrise::client::content::items::packages {
namespace {

/** Everything this feature reads and writes sits below one directory beside the settings file. */
constexpr std::wstring_view kDumpDirectory = L"\\dump";
/** What to extract is read from here, so changing it needs no rebuild. */
constexpr std::wstring_view kRequestFile = L"\\request.txt";
/** A request file is written with this when none exists, so the format documents itself. */
constexpr char kRequestTemplate[] =
    "# Sunrise package dump requests. One per line; blank lines and # comments are ignored.\r\n"
    "#\r\n"
    "# class <hex>   list every installed tag of one class to class_<hex>.txt\r\n"
    "# tag <hex>     write one entry's decrypted bytes to tag_<hex>.bin\r\n"
    "#\r\n"
    "# Class listing needs no keys and runs early. Reading a tag needs the block keys, which are\r\n"
    "# only available inside the running game - that is the whole reason this lives here rather\r\n"
    "# than in tools/pkg.\r\n"
    "#\r\n"
    "# class 0x00000008\r\n";

/**
 * Requests are bounded so a malformed file cannot make the pass run unboundedly long.
 * Sized for every vertex buffer of one destination package: the largest carries 97, and dumping a
 * partial set is worse than useless when the point is to modify a mesh without disturbing the
 * fields the format is not understood well enough to regenerate.
 */
constexpr std::size_t kRequestLimit = 256;
/** One request line, already parsed. */
struct Request {
    bool isClass{};
    std::uint32_t value{};
};

/** A class listing stops here. The widest installed class has far fewer members than this. */
constexpr std::size_t kListingLimit = 65536;

/** Reader storage is several megabytes and must not sit on a stack. */
reader::Scratch g_scratch{};

/** @return This DLL's module handle, so artifacts land beside the settings file. */
[[nodiscard]] HMODULE current_module() noexcept {
    HMODULE module{};
    GetModuleHandleExW(
        GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        reinterpret_cast<LPCWSTR>(&current_module),
        &module);
    return module;
}

/** Writes one whole file, replacing any previous contents. */
[[nodiscard]] bool write_file(const wchar_t* path, std::span<const std::byte> bytes) noexcept {
    const HANDLE file = CreateFileW(
        path, GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return false;
    }
    DWORD written = 0;
    const bool ok = bytes.empty()
                    || (WriteFile(file,
                                  bytes.data(),
                                  static_cast<DWORD>(bytes.size()),
                                  &written,
                                  nullptr)
                            != FALSE
                        && written == bytes.size());
    CloseHandle(file);
    return ok;
}

/** Reads one whole file into caller storage. */
[[nodiscard]] bool read_file(const wchar_t* path, std::vector<char>& bytes) noexcept {
    const HANDLE file = CreateFileW(
        path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return false;
    }
    LARGE_INTEGER size{};
    if (GetFileSizeEx(file, &size) == FALSE || size.QuadPart > (1 << 20)) {
        CloseHandle(file);
        return false;
    }
    bytes.resize(static_cast<std::size_t>(size.QuadPart));
    DWORD read = 0;
    const bool ok = bytes.empty()
                    || (ReadFile(file, bytes.data(), static_cast<DWORD>(bytes.size()), &read,
                                 nullptr)
                            != FALSE
                        && read == bytes.size());
    CloseHandle(file);
    return ok;
}

/** Parses the request file into bounded storage, ignoring blanks and comments. */
void parse_requests(std::span<const char> text,
                    std::array<Request, kRequestLimit>& requests,
                    std::size_t& count) noexcept {
    count = 0;
    std::size_t at = 0;
    while (at < text.size() && count < requests.size()) {
        std::size_t end = at;
        while (end < text.size() && text[end] != '\n' && text[end] != '\r') {
            ++end;
        }
        std::string_view line(text.data() + at, end - at);
        at = end + 1;
        while (!line.empty() && (line.front() == ' ' || line.front() == '\t')) {
            line.remove_prefix(1);
        }
        if (line.empty() || line.front() == '#') {
            continue;
        }
        const bool isClass = line.starts_with("class ");
        const bool isTag = line.starts_with("tag ");
        if (!isClass && !isTag) {
            continue;
        }
        line.remove_prefix(isClass ? 6 : 4);
        std::array<char, 32> number{};
        const std::size_t copied = (std::min)(line.size(), number.size() - 1);
        for (std::size_t index = 0; index < copied; ++index) {
            number[index] = line[index];
        }
        char* stop = nullptr;
        const unsigned long value = std::strtoul(number.data(), &stop, 0);
        if (stop == number.data()) {
            continue;
        }
        requests[count++] = Request{isClass, static_cast<std::uint32_t>(value)};
    }
}

/** Collects tags during a class scan. */
struct Listing {
    std::vector<char>* text{};
    std::size_t count{};
};

/** Appends one scanned tag and its package family to the listing. */
bool collect_tag(void* context, const reader::ClassEntry& entry) noexcept {
    auto* const listing = static_cast<Listing*>(context);
    if (listing->count >= kListingLimit) {
        return false;
    }
    std::array<char, 160> line{};
    std::array<char, 96> family{};
    const int converted = WideCharToMultiByte(CP_UTF8,
                                              0,
                                              entry.packageFamily.data(),
                                              static_cast<int>(entry.packageFamily.size()),
                                              family.data(),
                                              static_cast<int>(family.size() - 1),
                                              nullptr,
                                              nullptr);
    if (converted > 0) {
        family[static_cast<std::size_t>(converted)] = '\0';
    }
    const int length =
        std::snprintf(line.data(), line.size(), "0x%08X  %s\r\n", entry.tag, family.data());
    if (length > 0) {
        listing->text->insert(listing->text->end(), line.data(), line.data() + length);
        ++listing->count;
    }
    return true;
}

/** Runs one `class <hex>` request, writing the listing beside the request file. */
void run_class_request(const core::path::Buffer& directory,
                       std::wstring_view packages,
                       std::uint32_t classId) noexcept {
    std::vector<char> text;
    Listing listing{&text, 0};
    reader::ScanResult result{};
    const bool scanned =
        reader::scan_class_entries(packages, classId, &collect_tag, &listing, result);

    core::path::Buffer output = directory;
    std::array<wchar_t, 64> leaf{};
    std::swprintf(leaf.data(), leaf.size(), L"\\class_%08X.txt", classId);
    if (!core::path::append(output, leaf.data())) {
        return;
    }
    const bool written = write_file(
        output.chars.data(),
        std::span(reinterpret_cast<const std::byte*>(text.data()), text.size()));

    std::array<char, 192> event{};
    const int length = std::snprintf(event.data(),
                                     event.size(),
                                     "ev=package_dump stage=class class=0x%08X result=%s "
                                     "packages=%zu entries=%zu matches=%zu written=%s",
                                     classId,
                                     scanned ? "ok" : "partial",
                                     result.packages,
                                     result.entries,
                                     result.matches,
                                     written ? "ok" : "fail");
    if (length > 0) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::info,
                         std::string_view(event.data(), static_cast<std::size_t>(length)));
    }
}

/** Runs one `tag <hex>` request, writing the decrypted entry beside the request file. */
void run_tag_request(const core::path::Buffer& directory,
                     const reader::Source& source,
                     std::uint32_t tag) noexcept {
    std::vector<std::byte> bytes;
    std::uint32_t classId = 0;
    const bool read = reader::read_tag(source, g_scratch, tag, bytes, classId);

    bool written = false;
    if (read) {
        core::path::Buffer output = directory;
        std::array<wchar_t, 64> leaf{};
        std::swprintf(leaf.data(), leaf.size(), L"\\tag_%08X.bin", tag);
        written = core::path::append(output, leaf.data())
                  && write_file(output.chars.data(), bytes);
    }

    std::array<char, 192> event{};
    const int length = std::snprintf(event.data(),
                                     event.size(),
                                     "ev=package_dump stage=tag tag=0x%08X result=%s class=0x%08X "
                                     "bytes=%zu written=%s",
                                     tag,
                                     read ? "ok" : "fail",
                                     classId,
                                     bytes.size(),
                                     written ? "ok" : "fail");
    if (length > 0) {
        core::log::write(core::log::Channel::client,
                         read ? core::log::Level::info : core::log::Level::warn,
                         std::string_view(event.data(), static_cast<std::size_t>(length)));
    }
}

} // namespace

/** Extracts whatever the request file asks for, once per process. */
void dump_if_requested() noexcept {
    static bool done = false;
    if (done) {
        return;
    }
    done = true;

    core::path::Buffer directory{};
    if (!core::path::artifact_directory(current_module(), directory)
        || !core::path::append(directory, kDumpDirectory)) {
        return;
    }
    if (!CreateDirectoryW(directory.chars.data(), nullptr)
        && GetLastError() != ERROR_ALREADY_EXISTS) {
        return;
    }

    core::path::Buffer requestPath = directory;
    if (!core::path::append(requestPath, kRequestFile)) {
        return;
    }
    std::vector<char> text;
    if (!read_file(requestPath.chars.data(), text)) {
        // No request yet. Leaving a documented template costs one file and saves explaining it.
        (void)write_file(requestPath.chars.data(),
                         std::span(reinterpret_cast<const std::byte*>(kRequestTemplate),
                                   sizeof kRequestTemplate - 1));
        return;
    }

    std::array<Request, kRequestLimit> requests{};
    std::size_t count = 0;
    parse_requests(text, requests, count);
    if (count == 0) {
        return;
    }

    core::path::Buffer packages{};
    if (!package_directory(packages)) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::warn,
                         "ev=package_dump stage=resolve result=no_package_directory");
        return;
    }
    const std::wstring_view packagesView(packages.chars.data(), packages.length);

    // Class listings read entry tables only, so they run whether or not keys are available. Tag
    // reads need the block keys, and those only exist once the game has signed on.
    reader::BlockKeys keys{};
    const bool haveKeys = collect_keys(keys);
    const reader::Source source{packagesView, &keys};

    std::size_t classes = 0;
    std::size_t tags = 0;
    for (std::size_t index = 0; index < count; ++index) {
        if (requests[index].isClass) {
            run_class_request(directory, packagesView, requests[index].value);
            ++classes;
        } else if (haveKeys) {
            run_tag_request(directory, source, requests[index].value);
            ++tags;
        }
    }
    SecureZeroMemory(&keys, sizeof keys);
    reader::close_files(g_scratch);

    std::array<char, 160> event{};
    const int length = std::snprintf(event.data(),
                                     event.size(),
                                     "ev=package_dump stage=done requests=%zu classes=%zu "
                                     "tags=%zu keys=%s",
                                     count,
                                     classes,
                                     tags,
                                     haveKeys ? "ok" : "unavailable");
    if (length > 0) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::info,
                         std::string_view(event.data(), static_cast<std::size_t>(length)));
    }
}

} // namespace sunrise::client::content::items::packages
