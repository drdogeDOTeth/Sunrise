/**
 * Draw-time replacement for the character dye PS supporting dynamic VRM/GLB model profiles.
 *
 * Live dye PS contract (v18 baseline):
 * - TEXCOORD3.xy is the mesh UV. TEXCOORD0..2 are TBN.
 * - Flat n_map (0,0,1) -> worldN = TEXCOORD0.xyz.
 * - o1.xyz = saturate(worldN * ~0.375 + 0.5). o1.w is 0.
 * - o2.x = roughness (must stay > 0.05). o2.y = 0.5 (open AO). o2.z = 0 (metallic). o2.w = TEXCOORD0.w.
 * - o0.w fallback is ~0.2.
 * - G-buffer gate: nrt>=3 f0=29 f1=24 f2=28. Skip nrt=1 f0=26.
 * - Supports runtime switching between model profiles loaded from models/ directories.
 */

#include "custom_albedo.h"

#include <Windows.h>
#include <shellapi.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <d3dcompiler.h>
#include <string>
#include <string_view>
#include <vector>
#include <wincodec.h>

#include "../../../core/filesystem/path.h"
#include "../../../core/logging/log.h"
#include "../../hooking/detour.h"

namespace sunrise::client::hooks::custom_albedo {
namespace {

constexpr UINT kDrawIndexedMethod = 12;
constexpr UINT kDrawIndexedInstancedMethod = 20;
constexpr UINT kCreatePixelShaderMethod = 15;

using DrawIndexed = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT, UINT, INT);
using DrawIndexedInstanced =
    void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT, UINT, UINT, INT, UINT);
using CreatePixelShaderFn = HRESULT(STDMETHODCALLTYPE*)(
    ID3D11Device*, const void*, SIZE_T, ID3D11ClassLinkage*, ID3D11PixelShader**);

struct GpuImage {
    ID3D11Texture2D* texture{};
    ID3D11ShaderResourceView* view{};
};

template <typename Interface> void release_com(Interface*& object) noexcept {
    if (object != nullptr) {
        object->Release();
        object = nullptr;
    }
}

struct RuntimePart {
    UINT start{};
    UINT count{};
    std::string name{};
    std::wstring albedoFile{};
    std::wstring materialFile{};
    GpuImage albedoImage{};
    GpuImage materialImage{};
};

struct LoadedProfile {
    std::string id{};
    std::string name{};
    std::string author{};
    std::string version{};
    std::wstring folderPath{};
    UINT vertexCount{};
    std::vector<RuntimePart> parts{};
    bool isDefault{};
};

constexpr char kShaderSource[] = R"(
Texture2D Atlas : register(t0);
Texture2D Material : register(t1);
SamplerState Samp : register(s0);

struct Input {
    float4 tangent : TEXCOORD0;
    float4 bitangent : TEXCOORD1;
    float3 geometric : TEXCOORD2;
    float4 uv : TEXCOORD3;
    float3 unused4 : TEXCOORD4;
    float4 position : SV_Position;
    bool isFrontFace : SV_IsFrontFace;
};

struct Output {
    float4 albedo : SV_Target0;
    float4 encodedNormal : SV_Target1;
    float4 material : SV_Target2;
};

Output main(Input input)
{
    Output output;
    const float3 color = Atlas.SampleLevel(Samp, input.uv.xy, 0).rgb;
    output.albedo = float4(color, 0.2);
    const float3 worldN = normalize(input.tangent.xyz);
    output.encodedNormal = float4(saturate(worldN * 0.375 + 0.5), 0.0);
    const float roughness = Material.SampleLevel(Samp, input.uv.xy, 0).g;
    output.material = float4(max(roughness, 0.051), 0.5, 0.0, input.tangent.w);
    return output;
}
)";

constexpr UINT kSamplerSlots = 8;
constexpr UINT kSrvSlots = 16;
constexpr UINT kClassInstances = 256;
constexpr UINT kRenderTargets = D3D11_SIMULTANEOUS_RENDER_TARGET_COUNT;
constexpr UINT kGBufferAlbedo = 29;   // R8G8B8A8_UNORM_SRGB
constexpr UINT kGBufferNormal = 24;   // R10G10B10A2_UNORM
constexpr UINT kGBufferMaterial = 28; // R8G8B8A8_UNORM
constexpr UINT kMaxTargetLogs = 12;
constexpr UINT kMaxSkipLogs = 12;

struct BoundTargets {
    UINT bound{};
    UINT formats[3]{};
    UINT hasDepth{};
};

struct SavedPs {
    ID3D11PixelShader* shader{};
    ID3D11ClassInstance* instances[kClassInstances]{};
    UINT instanceCount{kClassInstances};
    ID3D11SamplerState* samplers[kSamplerSlots]{};
    ID3D11ShaderResourceView* srvs[kSrvSlots]{};
    ID3D11BlendState* blend{};
    FLOAT blendFactor[4]{};
    UINT sampleMask{};
};

struct PsRecord {
    ID3D11PixelShader* shader{};
    std::vector<std::uint8_t> bytecode{};
};

ID3D11Device* g_device{};
hooking::detour::Handle g_drawIndexed{};
hooking::detour::Handle g_drawIndexedInstanced{};
hooking::detour::Handle g_createPixelShader{};
ID3D11PixelShader* g_shader{};
ID3D11SamplerState* g_sampler{};
ID3D11BlendState* g_blend{};

SRWLOCK g_modelLock{SRWLOCK_INIT};
bool g_enabled = true;
std::string g_activeModelId = "default";
LoadedProfile g_activeProfile{};
std::vector<ModelProfile> g_discoveredModels{};

std::uint32_t g_loggedParts{};
std::uint32_t g_loggedMisses{};
bool g_loggedTargets{};
bool g_loggedDepthOnly{};
bool g_dumpedPs{};
std::array<UINT64, kMaxTargetLogs> g_seenTargetKeys{};
UINT g_seenTargetCount{};
UINT g_loggedSkips{};
SRWLOCK g_psLock{SRWLOCK_INIT};
std::vector<PsRecord> g_pixelShaders{};

[[nodiscard]] HMODULE owning_module() noexcept {
    HMODULE module = nullptr;
    (void)GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS
                                 | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                             reinterpret_cast<LPCWSTR>(&owning_module),
                             &module);
    return module;
}

std::string to_utf8(std::wstring_view wide) {
    if (wide.empty()) return "";
    const int len = WideCharToMultiByte(CP_UTF8, 0, wide.data(), static_cast<int>(wide.size()), nullptr, 0, nullptr, nullptr);
    if (len <= 0) return "";
    std::string result(len, '\0');
    WideCharToMultiByte(CP_UTF8, 0, wide.data(), static_cast<int>(wide.size()), result.data(), len, nullptr, nullptr);
    return result;
}

std::wstring to_wide(std::string_view utf8) {
    if (utf8.empty()) return L"";
    const int len = MultiByteToWideChar(CP_UTF8, 0, utf8.data(), static_cast<int>(utf8.size()), nullptr, 0);
    if (len <= 0) return L"";
    std::wstring result(len, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, utf8.data(), static_cast<int>(utf8.size()), result.data(), len);
    return result;
}

std::vector<std::wstring> get_search_directories_internal() noexcept {
    std::vector<std::wstring> candidates;
    core::path::Buffer buf{};

    if (core::path::artifact_directory(owning_module(), buf)) {
        std::wstring p1(buf.chars.data(), buf.length);
        if (!p1.empty() && p1.back() != L'\\') p1.push_back(L'\\');
        p1 += L"models";
        candidates.push_back(p1);
        CreateDirectoryW(p1.c_str(), nullptr);
    }

    if (core::path::module_directory(owning_module(), buf)) {
        std::wstring p2(buf.chars.data(), buf.length);
        if (!p2.empty() && p2.back() != L'\\') p2.push_back(L'\\');
        candidates.push_back(p2 + L"models");
        CreateDirectoryW((p2 + L"models").c_str(), nullptr);

        std::wstring p3(buf.chars.data(), buf.length);
        while (!p3.empty() && p3.back() == L'\\') {
            p3.pop_back();
        }
        const size_t slash1 = p3.rfind(L'\\');
        if (slash1 != std::wstring::npos) {
            p3.resize(slash1);
            const size_t slash2 = p3.rfind(L'\\');
            if (slash2 != std::wstring::npos) {
                p3.resize(slash2);
                candidates.push_back(p3 + L"\\models");
                candidates.push_back(p3 + L"\\bin\\x64\\Sunrise\\models");
                CreateDirectoryW((p3 + L"\\models").c_str(), nullptr);
            }
        }
    }

    std::vector<std::wstring> uniqueDirs;
    for (const auto& d : candidates) {
        if (std::find(uniqueDirs.begin(), uniqueDirs.end(), d) == uniqueDirs.end()) {
            uniqueDirs.push_back(d);
        }
    }
    return uniqueDirs;
}

[[nodiscard]] bool load_png_file(const std::wstring& fullPath,
                                 std::vector<std::uint32_t>& pixels,
                                 UINT& width,
                                 UINT& height) noexcept {
    IWICImagingFactory* factory = nullptr;
    if (FAILED(CoCreateInstance(
            CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&factory)))) {
        return false;
    }
    IWICBitmapDecoder* decoder = nullptr;
    IWICBitmapFrameDecode* frame = nullptr;
    IWICBitmapSource* converted = nullptr;
    width = 0;
    height = 0;
    const bool decoded =
        SUCCEEDED(factory->CreateDecoderFromFilename(fullPath.c_str(),
                                                     nullptr,
                                                     GENERIC_READ,
                                                     WICDecodeMetadataCacheOnDemand,
                                                     &decoder))
        && SUCCEEDED(decoder->GetFrame(0, &frame))
        && SUCCEEDED(WICConvertBitmapSource(GUID_WICPixelFormat32bppRGBA, frame, &converted))
        && SUCCEEDED(converted->GetSize(&width, &height)) && width > 0 && height > 0;
    bool copied = false;
    if (decoded) {
        pixels.resize(static_cast<std::size_t>(width) * height);
        const UINT stride = width * 4;
        copied = SUCCEEDED(converted->CopyPixels(
            nullptr, stride, stride * height, reinterpret_cast<BYTE*>(pixels.data())));
    }
    release_com(converted);
    release_com(frame);
    release_com(decoder);
    release_com(factory);
    return copied;
}

[[nodiscard]] bool upload_image(ID3D11Device* device,
                                const std::vector<std::uint32_t>& pixels,
                                UINT width,
                                UINT height,
                                DXGI_FORMAT format,
                                GpuImage& image) noexcept {
    if (device == nullptr) {
        return false;
    }
    D3D11_TEXTURE2D_DESC description{};
    description.Width = width;
    description.Height = height;
    description.MipLevels = 1;
    description.ArraySize = 1;
    description.Format = format;
    description.SampleDesc.Count = 1;
    description.Usage = D3D11_USAGE_IMMUTABLE;
    description.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    const D3D11_SUBRESOURCE_DATA initial{pixels.data(), width * 4, 0};
    return SUCCEEDED(device->CreateTexture2D(&description, &initial, &image.texture))
        && image.texture != nullptr
        && SUCCEEDED(device->CreateShaderResourceView(image.texture, nullptr, &image.view))
        && image.view != nullptr;
}

void release_profile_gpu_resources(LoadedProfile& profile) noexcept {
    for (RuntimePart& part : profile.parts) {
        release_com(part.albedoImage.view);
        release_com(part.albedoImage.texture);
        release_com(part.materialImage.view);
        release_com(part.materialImage.texture);
    }
}

std::string find_json_string(std::string_view json, std::string_view key) {
    const std::string search = "\"" + std::string(key) + "\"";
    const size_t pos = json.find(search);
    if (pos == std::string_view::npos) return "";
    const size_t colon = json.find(':', pos + search.length());
    if (colon == std::string_view::npos) return "";
    const size_t quote1 = json.find('\"', colon + 1);
    if (quote1 == std::string_view::npos) return "";
    const size_t quote2 = json.find('\"', quote1 + 1);
    if (quote2 == std::string_view::npos) return "";
    return std::string(json.substr(quote1 + 1, quote2 - quote1 - 1));
}

UINT find_json_uint(std::string_view json, std::string_view key, UINT fallback = 0) {
    const std::string search = "\"" + std::string(key) + "\"";
    const size_t pos = json.find(search);
    if (pos == std::string_view::npos) return fallback;
    const size_t colon = json.find(':', pos + search.length());
    if (colon == std::string_view::npos) return fallback;
    size_t start = colon + 1;
    while (start < json.length() && (json[start] == ' ' || json[start] == '\t' || json[start] == '\r' || json[start] == '\n')) {
        ++start;
    }
    size_t end = start;
    while (end < json.length() && json[end] >= '0' && json[end] <= '9') {
        ++end;
    }
    if (end > start) {
        return static_cast<UINT>(std::strtoul(std::string(json.substr(start, end - start)).c_str(), nullptr, 10));
    }
    return fallback;
}

std::string read_file_to_string(const std::wstring& path) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return "";
    LARGE_INTEGER size{};
    if (!GetFileSizeEx(file, &size) || size.QuadPart > 20 * 1024 * 1024) {
        CloseHandle(file);
        return "";
    }
    std::string content(static_cast<std::size_t>(size.QuadPart), '\0');
    DWORD read = 0;
    ReadFile(file, content.data(), static_cast<DWORD>(content.size()), &read, nullptr);
    CloseHandle(file);
    return content;
}

LoadedProfile create_default_profile() noexcept {
    core::path::Buffer directory{};
    std::wstring rootDir = L"";
    if (core::path::artifact_directory(owning_module(), directory)) {
        rootDir.assign(directory.chars.data(), directory.length);
    }

    LoadedProfile def{};
    def.id = "default";
    def.name = "Scatterhorn Void Gas Mask";
    def.author = "Chiliz / Sunrise";
    def.version = "GLB";
    def.isDefault = true;
    def.vertexCount = 23512;
    def.folderPath = rootDir;

    def.parts.push_back({0, 74358, "tank", rootDir + L"\\custom_tank.png", rootDir + L"\\custom_tank_mr.png"});
    def.parts.push_back({74358, 11574, "mask", rootDir + L"\\custom_mask.png", rootDir + L"\\custom_mask_mr.png"});
    def.parts.push_back({85932, 3570, "necklace", rootDir + L"\\custom_necklace.png", rootDir + L"\\custom_necklace_mr.png"});
    def.parts.push_back({89502, 42666, "skin", rootDir + L"\\custom_skin.png", rootDir + L"\\custom_skin_mr.png"});
    def.parts.push_back({132168, 6036, "twirl", rootDir + L"\\custom_twirl.png", L""});
    return def;
}

void load_profile_textures(ID3D11Device* device, LoadedProfile& profile) noexcept {
    for (RuntimePart& part : profile.parts) {
        std::vector<std::uint32_t> pixels;
        UINT width = 0, height = 0;
        UINT albedoWidth = 0, albedoHeight = 0;

        if (!part.albedoFile.empty() && load_png_file(part.albedoFile, pixels, albedoWidth, albedoHeight)) {
            (void)upload_image(device, pixels, albedoWidth, albedoHeight, DXGI_FORMAT_R8G8B8A8_UNORM_SRGB, part.albedoImage);
        }

        pixels.clear();
        width = 0;
        height = 0;
        bool mrLoaded = false;
        if (!part.materialFile.empty()) {
            mrLoaded = load_png_file(part.materialFile, pixels, width, height);
        }
        if (!mrLoaded) {
            pixels.assign(1, 0xFF008DFF); // default 0.55 roughness
            width = 1;
            height = 1;
        }
        (void)upload_image(device, pixels, width, height, DXGI_FORMAT_R8G8B8A8_UNORM, part.materialImage);
    }
}

void scan_models_directory(std::vector<ModelProfile>& output) noexcept {
    output.clear();

    // 1. Baseline default profile
    ModelProfile def{};
    def.id = "default";
    def.name = "Default (Gas Mask)";
    def.author = "Chiliz / Sunrise";
    def.version = "GLB";
    def.partCount = 5;
    def.vertexCount = 23512;
    def.isDefault = true;
    output.push_back(def);

    const std::vector<std::wstring> searchDirs = get_search_directories_internal();

    for (const std::wstring& dir : searchDirs) {
        const std::wstring searchPattern = dir + L"\\*";
        WIN32_FIND_DATAW findData{};
        HANDLE findHandle = FindFirstFileW(searchPattern.c_str(), &findData);
        if (findHandle == INVALID_HANDLE_VALUE) {
            continue;
        }

        do {
            if (std::wcscmp(findData.cFileName, L".") == 0 || std::wcscmp(findData.cFileName, L"..") == 0) {
                continue;
            }

            const std::wstring itemName = findData.cFileName;
            const std::wstring itemPath = dir + L"\\" + itemName;

            if ((findData.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
                // Check folder for manifest.json or profile.json
                std::wstring manifestPath = itemPath + L"\\manifest.json";
                std::string json = read_file_to_string(manifestPath);
                if (json.empty()) {
                    manifestPath = itemPath + L"\\profile.json";
                    json = read_file_to_string(manifestPath);
                }

                ModelProfile prof{};
                prof.folderPath = to_utf8(itemPath);

                if (!json.empty()) {
                    prof.id = find_json_string(json, "id");
                    if (prof.id.empty()) prof.id = to_utf8(itemName);
                    prof.name = find_json_string(json, "name");
                    if (prof.name.empty()) prof.name = prof.id;
                    prof.author = find_json_string(json, "author");
                    prof.version = find_json_string(json, "version");
                    prof.vertexCount = find_json_uint(json, "vertex_count", 0);
                    prof.isDefault = false;

                    size_t partPos = 0;
                    UINT pCount = 0;
                    while ((partPos = json.find("\"start\"", partPos)) != std::string::npos) {
                        ++pCount;
                        partPos += 7;
                    }
                    prof.partCount = pCount;
                } else {
                    // Auto-detect PNG textures in this folder
                    WIN32_FIND_DATAW pngFind{};
                    HANDLE pngHandle = FindFirstFileW((itemPath + L"\\*.png").c_str(), &pngFind);
                    UINT pngCount = 0;
                    if (pngHandle != INVALID_HANDLE_VALUE) {
                        do {
                            ++pngCount;
                        } while (FindNextFileW(pngHandle, &pngFind) != FALSE);
                        FindClose(pngHandle);
                    }

                    if (pngCount > 0) {
                        prof.id = to_utf8(itemName);
                        prof.name = to_utf8(itemName);
                        prof.author = "Custom Folder";
                        prof.version = "Textures";
                        prof.partCount = 5;
                        prof.vertexCount = 23512;
                        prof.isDefault = false;
                    } else {
                        continue; // skip empty folders
                    }
                }

                // Check for duplicates by ID
                bool duplicate = false;
                for (const auto& existing : output) {
                    if (existing.id == prof.id) {
                        duplicate = true;
                        break;
                    }
                }
                if (!duplicate) {
                    std::string logMsg = "ev=custom_albedo stage=scan_models found=" + prof.name + " id=" + prof.id + " path=" + prof.folderPath;
                    core::log::write(core::log::Channel::client, core::log::Level::info, logMsg);
                    output.push_back(prof);
                }
            }
        } while (FindNextFileW(findHandle, &findData) != FALSE);

        FindClose(findHandle);
    }
}

LoadedProfile load_profile_by_id(std::string_view profileId) {
    if (profileId == "default" || profileId.empty()) {
        return create_default_profile();
    }

    std::wstring folderPath;
    for (const auto& mod : g_discoveredModels) {
        if (mod.id == profileId) {
            folderPath = to_wide(mod.folderPath);
            break;
        }
    }

    if (folderPath.empty()) {
        const std::vector<std::wstring> dirs = get_search_directories_internal();
        for (const auto& dir : dirs) {
            const std::wstring candidate = dir + L"\\" + to_wide(profileId);
            if (GetFileAttributesW(candidate.c_str()) != INVALID_FILE_ATTRIBUTES) {
                folderPath = candidate;
                break;
            }
        }
    }

    if (folderPath.empty()) {
        return create_default_profile();
    }

    std::wstring manifestPath = folderPath + L"\\manifest.json";
    std::string json = read_file_to_string(manifestPath);
    if (json.empty()) {
        manifestPath = folderPath + L"\\profile.json";
        json = read_file_to_string(manifestPath);
    }

    LoadedProfile prof{};
    prof.id = std::string(profileId);
    prof.folderPath = folderPath;
    prof.isDefault = false;

    if (!json.empty()) {
        prof.name = find_json_string(json, "name");
        if (prof.name.empty()) prof.name = prof.id;
        prof.author = find_json_string(json, "author");
        prof.version = find_json_string(json, "version");
        prof.vertexCount = find_json_uint(json, "vertex_count", 0);

        // Parse parts
        size_t pos = json.find("\"parts\"");
        if (pos != std::string::npos) {
            size_t arrayStart = json.find('[', pos);
            size_t arrayEnd = json.rfind(']');
            if (arrayStart != std::string::npos && arrayEnd != std::string::npos && arrayEnd > arrayStart) {
                std::string_view partsView = std::string_view(json).substr(arrayStart, arrayEnd - arrayStart + 1);
                size_t objStart = 0;
                while ((objStart = partsView.find('{', objStart)) != std::string_view::npos) {
                    size_t objEnd = partsView.find('}', objStart);
                    if (objEnd == std::string_view::npos) break;
                    std::string_view obj = partsView.substr(objStart, objEnd - objStart + 1);

                    RuntimePart part{};
                    part.start = find_json_uint(obj, "start");
                    part.count = find_json_uint(obj, "count");
                    part.name = find_json_string(obj, "name");

                    std::string albedo = find_json_string(obj, "albedo");
                    if (!albedo.empty()) {
                        part.albedoFile = folderPath + L"\\" + to_wide(albedo);
                    }
                    std::string mat = find_json_string(obj, "material");
                    if (!mat.empty()) {
                        part.materialFile = folderPath + L"\\" + to_wide(mat);
                    }
                    prof.parts.push_back(part);
                    objStart = objEnd + 1;
                }
            }
        }
    }

    if (prof.parts.empty()) {
        // Fallback to default Scatterhorn ranges with local PNGs if available
        prof.name = prof.id;
        prof.parts.push_back({0, 74358, "tank", folderPath + L"\\custom_tank.png", folderPath + L"\\custom_tank_mr.png"});
        prof.parts.push_back({74358, 11574, "mask", folderPath + L"\\custom_mask.png", folderPath + L"\\custom_mask_mr.png"});
        prof.parts.push_back({85932, 3570, "necklace", folderPath + L"\\custom_necklace.png", folderPath + L"\\custom_necklace_mr.png"});
        prof.parts.push_back({89502, 42666, "skin", folderPath + L"\\custom_skin.png", folderPath + L"\\custom_skin_mr.png"});
        prof.parts.push_back({132168, 6036, "twirl", folderPath + L"\\custom_twirl.png", L""});
    }

    return prof;
}

[[nodiscard]] const RuntimePart* match(UINT start, UINT count) noexcept {
    if (!g_enabled) {
        return nullptr;
    }
    for (const RuntimePart& part : g_activeProfile.parts) {
        if (part.start == start && part.count == count) {
            return &part;
        }
    }
    return nullptr;
}

void note_miss(UINT start, UINT count) noexcept {
    if (g_loggedMisses >= 16 || count == 0 || !g_enabled) {
        return;
    }
    ++g_loggedMisses;
    std::array<char, 160> line{};
    const int written = std::snprintf(line.data(),
                                      line.size(),
                                      "ev=custom_albedo stage=miss start=%u count=%u",
                                      start,
                                      count);
    if (written > 0) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::info,
                         std::string_view(line.data(), static_cast<std::size_t>(written)));
    }
}

void capture_ps(ID3D11DeviceContext* context, SavedPs& saved) noexcept {
    saved.instanceCount = kClassInstances;
    context->PSGetShader(&saved.shader, saved.instances, &saved.instanceCount);
    context->PSGetSamplers(0, kSamplerSlots, saved.samplers);
    context->PSGetShaderResources(0, kSrvSlots, saved.srvs);
    context->OMGetBlendState(&saved.blend, saved.blendFactor, &saved.sampleMask);
}

void restore_ps(ID3D11DeviceContext* context, SavedPs& saved) noexcept {
    context->PSSetShader(saved.shader, saved.instances, saved.instanceCount);
    context->PSSetSamplers(0, kSamplerSlots, saved.samplers);
    context->PSSetShaderResources(0, kSrvSlots, saved.srvs);
    context->OMSetBlendState(saved.blend, saved.blendFactor, saved.sampleMask);
}

void release_saved(SavedPs& saved) noexcept {
    release_com(saved.shader);
    for (UINT i = 0; i < saved.instanceCount && i < kClassInstances; ++i) {
        release_com(saved.instances[i]);
    }
    for (ID3D11SamplerState*& sampler : saved.samplers) {
        release_com(sampler);
    }
    for (ID3D11ShaderResourceView*& srv : saved.srvs) {
        release_com(srv);
    }
    release_com(saved.blend);
}

[[nodiscard]] BoundTargets inspect_targets(ID3D11DeviceContext* context) noexcept {
    BoundTargets targets{};
    ID3D11RenderTargetView* views[kRenderTargets]{};
    ID3D11DepthStencilView* depth = nullptr;
    context->OMGetRenderTargets(kRenderTargets, views, &depth);
    for (UINT slot = 0; slot < kRenderTargets; ++slot) {
        if (views[slot] == nullptr) {
            continue;
        }
        D3D11_RENDER_TARGET_VIEW_DESC description{};
        views[slot]->GetDesc(&description);
        if (slot < 3) {
            targets.formats[slot] = static_cast<UINT>(description.Format);
        }
        ++targets.bound;
        views[slot]->Release();
    }
    targets.hasDepth = depth != nullptr ? 1u : 0u;
    if (depth != nullptr) {
        depth->Release();
    }
    return targets;
}

[[nodiscard]] bool is_character_gbuffer(const BoundTargets& targets) noexcept {
    return targets.bound >= 3 && targets.formats[0] == kGBufferAlbedo
        && targets.formats[1] == kGBufferNormal && targets.formats[2] == kGBufferMaterial;
}

[[nodiscard]] bool compile_shader(ID3D11Device* device) noexcept {
    ID3DBlob* bytecode = nullptr;
    ID3DBlob* errors = nullptr;
    const HRESULT compiled = D3DCompile(kShaderSource,
                                         std::strlen(kShaderSource),
                                         "custom_albedo.hlsl",
                                         nullptr,
                                         nullptr,
                                         "main",
                                         "ps_5_0",
                                         0,
                                         0,
                                         &bytecode,
                                         &errors);
    if (FAILED(compiled) || bytecode == nullptr) {
        if (errors != nullptr) {
            const auto* text = static_cast<const char*>(errors->GetBufferPointer());
            const std::size_t size = errors->GetBufferSize();
            if (text != nullptr && size > 0) {
                core::log::write(core::log::Channel::client,
                                 core::log::Level::error,
                                 std::string_view(text, size));
            }
            errors->Release();
        }
        return false;
    }
    if (errors != nullptr) {
        errors->Release();
    }
    if (FAILED(device->CreatePixelShader(
            bytecode->GetBufferPointer(), bytecode->GetBufferSize(), nullptr, &g_shader))
        || g_shader == nullptr) {
        bytecode->Release();
        return false;
    }
    bytecode->Release();
    D3D11_SAMPLER_DESC sampler{};
    sampler.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    sampler.AddressU = D3D11_TEXTURE_ADDRESS_WRAP;
    sampler.AddressV = D3D11_TEXTURE_ADDRESS_WRAP;
    sampler.AddressW = D3D11_TEXTURE_ADDRESS_WRAP;
    sampler.MaxLOD = D3D11_FLOAT32_MAX;
    if (FAILED(device->CreateSamplerState(&sampler, &g_sampler)) || g_sampler == nullptr) {
        return false;
    }
    D3D11_BLEND_DESC blend{};
    blend.IndependentBlendEnable = TRUE;
    for (UINT slot = 0; slot < 8; ++slot) {
        blend.RenderTarget[slot].BlendEnable = FALSE;
        blend.RenderTarget[slot].RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
    }
    return SUCCEEDED(device->CreateBlendState(&blend, &g_blend)) && g_blend != nullptr;
}

template <typename Draw>
void draw_replaced(ID3D11DeviceContext* context,
                   const RuntimePart& part,
                   UINT /*start*/,
                   UINT /*count*/,
                   Draw&& draw) noexcept {
    if (g_shader == nullptr || !g_enabled) {
        draw();
        return;
    }
    const BoundTargets targets = inspect_targets(context);
    if (targets.bound == 0 || !is_character_gbuffer(targets)) {
        draw();
        return;
    }

    ID3D11ShaderResourceView* views[2]{part.albedoImage.view, part.materialImage.view};
    SavedPs saved{};
    capture_ps(context, saved);
    context->PSSetShader(g_shader, nullptr, 0);
    if (g_sampler != nullptr) {
        context->PSSetSamplers(0, 1, &g_sampler);
    }
    context->PSSetShaderResources(0, 2, views);
    if (g_blend != nullptr) {
        const FLOAT opaque[4]{1.0f, 1.0f, 1.0f, 1.0f};
        context->OMSetBlendState(g_blend, opaque, 0xFFFFFFFFu);
    }
    draw();
    restore_ps(context, saved);
    release_saved(saved);
}

void STDMETHODCALLTYPE draw_indexed(ID3D11DeviceContext* context,
                                    UINT indexCount,
                                    UINT startIndex,
                                    INT baseVertex) noexcept {
    const DrawIndexed next = reinterpret_cast<DrawIndexed>(g_drawIndexed.original);
    if (next == nullptr) {
        return;
    }
    AcquireSRWLockShared(&g_modelLock);
    const RuntimePart* part = match(startIndex, indexCount);
    if (part == nullptr) {
        ReleaseSRWLockShared(&g_modelLock);
        note_miss(startIndex, indexCount);
        next(context, indexCount, startIndex, baseVertex);
        return;
    }
    draw_replaced(context, *part, startIndex, indexCount, [&]() noexcept {
        next(context, indexCount, startIndex, baseVertex);
    });
    ReleaseSRWLockShared(&g_modelLock);
}

void STDMETHODCALLTYPE draw_indexed_instanced(ID3D11DeviceContext* context,
                                              UINT indexCountPerInstance,
                                              UINT instanceCount,
                                              UINT startIndex,
                                              INT baseVertex,
                                              UINT startInstance) noexcept {
    const DrawIndexedInstanced next =
        reinterpret_cast<DrawIndexedInstanced>(g_drawIndexedInstanced.original);
    if (next == nullptr) {
        return;
    }
    AcquireSRWLockShared(&g_modelLock);
    const RuntimePart* part = match(startIndex, indexCountPerInstance);
    if (part == nullptr) {
        ReleaseSRWLockShared(&g_modelLock);
        note_miss(startIndex, indexCountPerInstance);
        next(context, indexCountPerInstance, instanceCount, startIndex, baseVertex, startInstance);
        return;
    }
    draw_replaced(context, *part, startIndex, indexCountPerInstance, [&]() noexcept {
        next(context, indexCountPerInstance, instanceCount, startIndex, baseVertex, startInstance);
    });
    ReleaseSRWLockShared(&g_modelLock);
}

HRESULT STDMETHODCALLTYPE create_pixel_shader(ID3D11Device* device,
                                              const void* bytecode,
                                              SIZE_T length,
                                              ID3D11ClassLinkage* linkage,
                                              ID3D11PixelShader** shader) noexcept {
    const CreatePixelShaderFn next =
        reinterpret_cast<CreatePixelShaderFn>(g_createPixelShader.original);
    if (next == nullptr) {
        return E_FAIL;
    }
    const HRESULT created = next(device, bytecode, length, linkage, shader);
    if (SUCCEEDED(created) && shader != nullptr && *shader != nullptr && bytecode != nullptr
        && length != 0) {
        PsRecord record{};
        record.shader = *shader;
        record.bytecode.resize(length);
        std::memcpy(record.bytecode.data(), bytecode, length);
        AcquireSRWLockExclusive(&g_psLock);
        g_pixelShaders.push_back(std::move(record));
        ReleaseSRWLockExclusive(&g_psLock);
    }
    return created;
}

[[nodiscard]] bool install_hooks(ID3D11Device* device, ID3D11DeviceContext* context) noexcept {
    void** const deviceTable = *reinterpret_cast<void***>(device);
    void** const contextTable = *reinterpret_cast<void***>(context);
    const std::array specs{
        hooking::detour::Spec{contextTable[kDrawIndexedMethod], reinterpret_cast<void*>(&draw_indexed)},
        hooking::detour::Spec{contextTable[kDrawIndexedInstancedMethod],
                              reinterpret_cast<void*>(&draw_indexed_instanced)},
        hooking::detour::Spec{deviceTable[kCreatePixelShaderMethod],
                              reinterpret_cast<void*>(&create_pixel_shader)},
    };
    std::array<hooking::detour::Handle, 3> handles{};
    if (!hooking::detour::install(specs, handles)) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::error,
                         "ev=custom_albedo stage=detour result=fail");
        return false;
    }
    g_drawIndexed = handles[0];
    g_drawIndexedInstanced = handles[1];
    g_createPixelShader = handles[2];
    return true;
}

} // namespace

void attach(ID3D11Device* device, ID3D11DeviceContext* context) noexcept {
    detach();
    if (device == nullptr || context == nullptr) {
        return;
    }
    g_device = device;
    if (!compile_shader(device) || !install_hooks(device, context)) {
        detach();
        return;
    }

    AcquireSRWLockExclusive(&g_modelLock);
    scan_models_directory(g_discoveredModels);
    g_activeProfile = load_profile_by_id(g_activeModelId);
    load_profile_textures(device, g_activeProfile);
    ReleaseSRWLockExclusive(&g_modelLock);

    core::log::write(core::log::Channel::client,
                     core::log::Level::info,
                     "ev=custom_albedo stage=attach result=ok mode=ours");
}

void detach() noexcept {
    std::array handles{g_drawIndexed, g_drawIndexedInstanced, g_createPixelShader};
    if (handles[0].attached || handles[1].attached || handles[2].attached) {
        (void)hooking::detour::uninstall(handles);
    }
    g_drawIndexed = {};
    g_drawIndexedInstanced = {};
    g_createPixelShader = {};
    release_com(g_shader);
    release_com(g_sampler);
    release_com(g_blend);

    AcquireSRWLockExclusive(&g_modelLock);
    release_profile_gpu_resources(g_activeProfile);
    g_activeProfile = {};
    ReleaseSRWLockExclusive(&g_modelLock);

    AcquireSRWLockExclusive(&g_psLock);
    g_pixelShaders.clear();
    ReleaseSRWLockExclusive(&g_psLock);
    g_device = nullptr;
    g_loggedParts = 0;
    g_loggedMisses = 0;
    g_loggedTargets = false;
    g_loggedDepthOnly = false;
    g_dumpedPs = false;
    g_seenTargetKeys = {};
    g_seenTargetCount = 0;
    g_loggedSkips = 0;
}

bool is_attached() noexcept {
    return g_drawIndexed.attached && g_shader != nullptr;
}

bool is_enabled() noexcept {
    AcquireSRWLockShared(&g_modelLock);
    const bool enabled = g_enabled;
    ReleaseSRWLockShared(&g_modelLock);
    return enabled;
}

void set_enabled(bool enabled) noexcept {
    AcquireSRWLockExclusive(&g_modelLock);
    g_enabled = enabled;
    ReleaseSRWLockExclusive(&g_modelLock);
}

std::vector<ModelProfile> get_available_models() noexcept {
    AcquireSRWLockShared(&g_modelLock);
    std::vector<ModelProfile> list = g_discoveredModels;
    for (ModelProfile& item : list) {
        item.active = (item.id == g_activeModelId);
    }
    ReleaseSRWLockShared(&g_modelLock);
    return list;
}

std::string get_active_model_id() noexcept {
    AcquireSRWLockShared(&g_modelLock);
    const std::string id = g_activeModelId;
    ReleaseSRWLockShared(&g_modelLock);
    return id;
}

bool select_model(std::string_view modelId) noexcept {
    AcquireSRWLockExclusive(&g_modelLock);
    release_profile_gpu_resources(g_activeProfile);
    g_activeModelId = std::string(modelId);
    g_activeProfile = load_profile_by_id(g_activeModelId);
    if (g_device != nullptr) {
        load_profile_textures(g_device, g_activeProfile);
    }
    ReleaseSRWLockExclusive(&g_modelLock);

    std::string msg = "ev=custom_albedo stage=select_model id=" + g_activeModelId + " parts=" + std::to_string(g_activeProfile.parts.size());
    core::log::write(core::log::Channel::client, core::log::Level::info, msg);
    return true;
}

void refresh_models() noexcept {
    AcquireSRWLockExclusive(&g_modelLock);
    scan_models_directory(g_discoveredModels);
    ReleaseSRWLockExclusive(&g_modelLock);

    std::string msg = "ev=custom_albedo stage=refresh_models total=" + std::to_string(g_discoveredModels.size());
    core::log::write(core::log::Channel::client, core::log::Level::info, msg);
}

std::vector<std::string> get_model_search_paths() noexcept {
    const std::vector<std::wstring> dirs = get_search_directories_internal();
    std::vector<std::string> result;
    for (const auto& d : dirs) {
        result.push_back(to_utf8(d));
    }
    return result;
}

void open_models_folder() noexcept {
    const std::vector<std::wstring> dirs = get_search_directories_internal();
    if (!dirs.empty()) {
        CreateDirectoryW(dirs[0].c_str(), nullptr);
        ShellExecuteW(nullptr, L"open", dirs[0].c_str(), nullptr, nullptr, SW_SHOWNORMAL);
    }
}

} // namespace sunrise::client::hooks::custom_albedo
