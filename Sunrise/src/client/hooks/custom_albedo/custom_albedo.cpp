/**
 * Draw-time replacement for the Scatterhorn-chest dye PS on the five custom
 * parts. Goal is the GLB / Blender look (green SkinTats, charcoal tank, black
 * mask, twirl, teal necklace) — not a tint on the dye tile.
 *
 * Live dye PS (objs/live_chest_ps.asm) contract:
 * - TEXCOORD3.xy is the mesh UV. TEXCOORD0..2 are TBN.
 * - Flat n_map (0,0,1) → worldN = TEXCOORD0.xyz (v0*nz + v1*nx + v2*ny).
 * - v16 TEXCOORD2 is closed: select went dark, world went solid black.
 * - o1.xyz = saturate(worldN * ~0.375 + 0.5). o1.w is 0 or ~0.33, never 1.
 * - o2.x = GLB metalRough.g (must stay > 0.05). o2.y = 0.5 (open AO — the
 *   GLB has no occlusion map). v17 sampled Destiny t2 and stamped Scatterhorn
 *   designs onto the body. Do not sample game t2 again. o2.z = 0 (GLB
 *   metallicFactor is 0 on four parts). o2.w = TEXCOORD0.w.
 * - o0.w fallback is ~0.2.
 *
 * v12/v14 is the Blender look on character select. v13 two-pass left the dye
 * quilt on screen (second draw never won RT0). Stay single-pass.
 *
 * v14 also wrote this 3-target PS into destination shadow/lighting draws of
 * the same index counts, and restored the game PS with no class instances.
 * World went SkinTats-green. v15 only replaces the select G-buffer layout
 * (29/24/28) and puts the previous PS / SRVs / samplers / blend back.
 */

#include "custom_albedo.h"

#include <Windows.h>

#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <d3dcompiler.h>
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

/** Longest texture path a part can name: `\characters\<profile>\<file>.png`. */
constexpr std::size_t kPathCapacity = 160;

struct Part {
    UINT start{};
    UINT count{};
    char name[32]{};
    wchar_t file[kPathCapacity]{};
    wchar_t material[kPathCapacity]{};
    bool hasMaterial{};
    /**
     * Skipped at draw rather than painted. A part table names `hide` where a texture would go to
     * drop geometry the injected mesh replaces - stock gloves over custom hands, the race head
     * under a custom one, or another character's ranges while this one is drawn. It is the only
     * blank that works: the same edit as a package patch, at the one place nothing caches it.
     */
    bool hidden{};
};

struct PartSpec {
    UINT start{};
    UINT count{};
    const char* name{};
    const wchar_t* file{};
    const wchar_t* material{};
};

constexpr UINT kMaxParts = 16;
constexpr std::array<PartSpec, 6> kDefaultParts{{
    {0, 74358, "tank", L"\\custom_tank.png", L"\\custom_tank_mr.png"},
    {74358, 11580, "mask", L"\\custom_mask.png", L"\\custom_mask_mr.png"},
    {85938, 3570, "necklace", L"\\custom_necklace.png", L"\\custom_necklace_mr.png"},
    {89508, 26760, "skin", L"\\custom_skin.png", L"\\custom_skin_mr.png"},
    {116268, 6036, "twirl", L"\\custom_twirl.png", nullptr},
    {0, 16326, "hands", L"\\custom_skin.png", L"\\custom_skin_mr.png"},
}};

std::array<Part, kMaxParts> g_parts{};
UINT g_partCount{};

constexpr UINT kMeshIndices = 122304;

/** Directory under the artifact root that holds one subdirectory per character. */
constexpr std::wstring_view kProfileRoot = L"\\characters";
/** Part table every profile directory must carry. */
constexpr std::wstring_view kProfileTable = L"\\parts.txt";
/** Remembers the switcher's choice across launches, beside the profiles it names. */
constexpr std::wstring_view kActiveFile = L"\\characters\\active.txt";
/**
 * Presence of this file turns on the texture survey. A file rather than a settings key so it can
 * be switched on for one launch and off again without a rebuild, and so it costs one existence
 * check at init rather than anything per draw.
 */
constexpr std::wstring_view kSurveyFile = L"\\menu_survey.txt";
/** Distinct textures the survey will name before it stops looking. */
constexpr std::size_t kMaxSurveyed = 64;
/** Probe list: `<width> <height> RRGGBB` per line, tinting a matching texture flat. */
constexpr std::wstring_view kProbeFile = L"\\menu_probe.txt";
constexpr std::size_t kMaxProbes = 16;
/** Sizes to save to PNG once, so a menu texture can be edited and bound back. */
constexpr std::wstring_view kDumpFile = L"\\menu_dump.txt";
/** PS texture slots the probe inspects and restores. */
constexpr UINT kProbeSlots = 4;
/** Longest profile directory name kept. */
constexpr std::size_t kProfileNameCapacity = 48;

struct Profile {
    /** Directory name, which is what the panel lists and `active.txt` records. */
    char name[kProfileNameCapacity]{};
};

std::array<Profile, kMaxProfiles> g_profiles{};
std::size_t g_profileCount{};
/** Slot being drawn. Equal to `g_profileCount` while the shipped defaults are in use. */
std::size_t g_activeProfile{};
/** Held from attach so a profile swap can upload textures without reinstalling the detours. */
ID3D11Device* g_device{};

struct GpuImage {
    ID3D11Texture2D* texture{};
    ID3D11ShaderResourceView* view{};
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
    const float4 texel = Atlas.SampleLevel(Samp, input.uv.xy, 0);
    // Cutout. A character's eye and mouth planes are sprite sheets that are ~91% transparent, so
    // ignoring alpha drew the whole quad as an opaque black slab across the face. Discarding also
    // suppresses the depth write, which is what a cutout needs. A texture with no alpha channel
    // decodes to a=1 and is never clipped, so opaque parts are unaffected.
    clip(texel.a - 0.5);
    const float3 color = texel.rgb;
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

template <typename Interface> void release_com(Interface*& object) noexcept {
    if (object != nullptr) {
        object->Release();
        object = nullptr;
    }
}

struct PsRecord {
    ID3D11PixelShader* shader{};
    std::vector<std::uint8_t> bytecode{};
};

hooking::detour::Handle g_drawIndexed{};
hooking::detour::Handle g_drawIndexedInstanced{};
hooking::detour::Handle g_createPixelShader{};
ID3D11PixelShader* g_shader{};
ID3D11SamplerState* g_sampler{};
ID3D11BlendState* g_blend{};
std::array<GpuImage, kMaxParts> g_images{};
std::array<GpuImage, kMaxParts> g_materials{};
std::uint32_t g_loggedParts{};
std::uint32_t g_loggedMisses{};
constexpr UINT kMaxLoggedMisses = 64;
std::array<UINT64, kMaxLoggedMisses> g_seenMisses{};
bool g_loggedTargets{};
bool g_loggedDepthOnly{};
bool g_dumpedPs{};
std::array<UINT64, kMaxTargetLogs> g_seenTargetKeys{};
UINT g_seenTargetCount{};
UINT g_loggedSkips{};
SRWLOCK g_psLock{SRWLOCK_INIT};
std::vector<PsRecord> g_pixelShaders{};

[[nodiscard]] HMODULE owning_module() noexcept;

[[nodiscard]] const Part* match(UINT start, UINT count) noexcept {
    // Exact pairs only. v13 subset-match treated start=0 UI quads as the tank
    // and smashed character select (ALWAYS depth + RT0 RGB).
    for (UINT i = 0; i < g_partCount && i < kMaxParts; ++i) {
        if (g_parts[i].start == start && g_parts[i].count == count) {
            return &g_parts[i];
        }
    }
    return nullptr;
}

void note_miss(UINT start, UINT count) noexcept {
    // Leftover race gloves will not share the chest index range. Log unique
    // unmatched draws so one launch can name them. Do not subset-match.
    if (count < 3 || g_loggedMisses >= kMaxLoggedMisses) {
        return;
    }
    const UINT64 key = (static_cast<UINT64>(start) << 32) | static_cast<UINT64>(count);
    for (UINT i = 0; i < g_loggedMisses; ++i) {
        if (g_seenMisses[i] == key) {
            return;
        }
    }
    g_seenMisses[g_loggedMisses] = key;
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

void note_first(const Part& part, UINT start, UINT count, const BoundTargets& targets) noexcept {
    const std::uint32_t bit = 1u << static_cast<std::uint32_t>(&part - g_parts.data());
    if ((g_loggedParts & bit) != 0) {
        return;
    }
    g_loggedParts |= bit;
    std::array<char, 192> line{};
    const int written = std::snprintf(line.data(),
                                      line.size(),
                                      "ev=custom_albedo stage=draw part=%s start=%u count=%u "
                                      "nrt=%u f0=%u f1=%u f2=%u result=ok",
                                      part.name,
                                      start,
                                      count,
                                      targets.bound,
                                      targets.formats[0],
                                      targets.formats[1],
                                      targets.formats[2]);
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

[[nodiscard]] UINT64 target_key(const BoundTargets& targets) noexcept {
    return (static_cast<UINT64>(targets.bound) << 48) | (static_cast<UINT64>(targets.hasDepth) << 40)
        | (static_cast<UINT64>(targets.formats[0]) << 24)
        | (static_cast<UINT64>(targets.formats[1]) << 12) | targets.formats[2];
}

void note_targets(const BoundTargets& targets) noexcept {
    const UINT64 key = target_key(targets);
    for (UINT i = 0; i < g_seenTargetCount; ++i) {
        if (g_seenTargetKeys[i] == key) {
            return;
        }
    }
    if (g_seenTargetCount >= g_seenTargetKeys.size()) {
        return;
    }
    g_seenTargetKeys[g_seenTargetCount++] = key;
    if (targets.bound == 0) {
        g_loggedDepthOnly = true;
    } else {
        g_loggedTargets = true;
    }
    std::array<char, 192> line{};
    const int written = std::snprintf(line.data(),
                                      line.size(),
                                      "ev=custom_albedo stage=targets nrt=%u "
                                      "f0=%u f1=%u f2=%u depth=%u",
                                      targets.bound,
                                      targets.formats[0],
                                      targets.formats[1],
                                      targets.formats[2],
                                      targets.hasDepth);
    if (written > 0) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::info,
                         std::string_view(line.data(), static_cast<std::size_t>(written)));
    }
}

void note_skip_rt(const Part& part, const BoundTargets& targets) noexcept {
    if (g_loggedSkips >= kMaxSkipLogs) {
        return;
    }
    ++g_loggedSkips;
    std::array<char, 192> line{};
    const int written = std::snprintf(line.data(),
                                      line.size(),
                                      "ev=custom_albedo stage=skip reason=rt part=%s "
                                      "nrt=%u f0=%u f1=%u f2=%u depth=%u",
                                      part.name,
                                      targets.bound,
                                      targets.formats[0],
                                      targets.formats[1],
                                      targets.formats[2],
                                      targets.hasDepth);
    if (written > 0) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::info,
                         std::string_view(line.data(), static_cast<std::size_t>(written)));
    }
}

[[nodiscard]] HMODULE owning_module() noexcept {
    HMODULE module = nullptr;
    (void)GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS
                                 | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                             reinterpret_cast<LPCWSTR>(&owning_module),
                             &module);
    return module;
}

void dump_game_ps(ID3D11PixelShader* shader) noexcept {
    if (g_dumpedPs || shader == nullptr) {
        return;
    }
    AcquireSRWLockShared(&g_psLock);
    const std::uint8_t* bytes = nullptr;
    std::size_t size = 0;
    for (const PsRecord& record : g_pixelShaders) {
        if (record.shader == shader) {
            bytes = record.bytecode.data();
            size = record.bytecode.size();
            break;
        }
    }
    ReleaseSRWLockShared(&g_psLock);
    if (bytes == nullptr || size == 0) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::info,
                         "ev=custom_albedo stage=dump_ps result=miss");
        g_dumpedPs = true;
        return;
    }
    core::path::Buffer directory{};
    if (!core::path::artifact_directory(owning_module(), directory)
        || !core::path::append(directory, L"\\dump")) {
        g_dumpedPs = true;
        return;
    }
    CreateDirectoryW(directory.chars.data(), nullptr);
    core::path::Buffer path = directory;
    if (!core::path::append(path, L"\\live_chest_ps.bin")) {
        g_dumpedPs = true;
        return;
    }
    HANDLE file = CreateFileW(path.chars.data(),
                              GENERIC_WRITE,
                              0,
                              nullptr,
                              CREATE_ALWAYS,
                              FILE_ATTRIBUTE_NORMAL,
                              nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::error,
                         "ev=custom_albedo stage=dump_ps result=fail");
        g_dumpedPs = true;
        return;
    }
    DWORD written = 0;
    const BOOL ok = WriteFile(file, bytes, static_cast<DWORD>(size), &written, nullptr);
    CloseHandle(file);
    g_dumpedPs = true;
    std::array<char, 96> line{};
    const int n = std::snprintf(line.data(),
                                line.size(),
                                "ev=custom_albedo stage=dump_ps result=%s bytes=%u",
                                ok != FALSE ? "ok" : "fail",
                                written);
    if (n > 0) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::info,
                         std::string_view(line.data(), static_cast<std::size_t>(n)));
    }
}

[[nodiscard]] bool load_png(std::wstring_view file,
                            std::vector<std::uint32_t>& pixels,
                            UINT& width,
                            UINT& height) noexcept {
    core::path::Buffer directory{};
    if (!core::path::artifact_directory(owning_module(), directory)
        || !core::path::append(directory, file)) {
        return false;
    }
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
        SUCCEEDED(factory->CreateDecoderFromFilename(directory.chars.data(),
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

void seed_part(UINT index,
               UINT start,
               UINT count,
               const char* name,
               const wchar_t* file,
               const wchar_t* material) noexcept {
    Part& part = g_parts[index];
    part = {};
    part.start = start;
    part.count = count;
    (void)std::snprintf(part.name, sizeof(part.name), "%s", name);
    if (file != nullptr) {
        wcsncpy_s(part.file, file, _TRUNCATE);
    }
    if (material != nullptr && material[0] != L'\0') {
        wcsncpy_s(part.material, material, _TRUNCATE);
        part.hasMaterial = true;
    }
}

void seed_defaults() noexcept {
    g_partCount = 0;
    for (const PartSpec& spec : kDefaultParts) {
        if (g_partCount >= kMaxParts) {
            break;
        }
        seed_part(g_partCount, spec.start, spec.count, spec.name, spec.file, spec.material);
        ++g_partCount;
    }
}

/** Appends one narrow string to a wide buffer already `at` characters long. */
[[nodiscard]] std::size_t append_narrow(const char* text,
                                        wchar_t* dest,
                                        std::size_t at,
                                        std::size_t destCount) noexcept {
    for (; text != nullptr && text[0] != '\0' && at + 1 < destCount; ++text, ++at) {
        dest[at] = static_cast<wchar_t>(static_cast<unsigned char>(text[0]));
    }
    dest[at] = L'\0';
    return at;
}

/**
 * Builds the artifact-relative suffix `load_png` wants.
 * A profile's textures sit beside its part table, so naming the profile is what keeps two
 * characters' files from colliding on one name like `skin.png`.
 * @param name Texture file named by the part table.
 * @param profile Profile directory, or nullptr for the flat pre-profile layout.
 */
void to_wide_suffix(const char* name,
                    const char* profile,
                    wchar_t* dest,
                    std::size_t destCount) noexcept {
    dest[0] = L'\\';
    dest[1] = L'\0';
    if (name == nullptr || destCount < 3) {
        return;
    }
    std::size_t at = 1;
    if (profile != nullptr && profile[0] != '\0') {
        at = append_narrow("characters\\", dest, at, destCount);
        at = append_narrow(profile, dest, at, destCount);
        at = append_narrow("\\", dest, at, destCount);
    }
    (void)append_narrow(name, dest, at, destCount);
}

/**
 * Reads one part table into the live table.
 * @param profile Profile directory to read, or nullptr for the flat `custom_parts.txt`.
 */
void load_parts_file(const char* profile) noexcept {
    core::path::Buffer directory{};
    if (!core::path::artifact_directory(owning_module(), directory)) {
        return;
    }
    if (profile != nullptr && profile[0] != '\0') {
        wchar_t suffix[kPathCapacity]{};
        to_wide_suffix("parts.txt", profile, suffix, kPathCapacity);
        if (!core::path::append(directory, suffix)) {
            return;
        }
    } else if (!core::path::append(directory, L"\\custom_parts.txt")) {
        return;
    }
    const HANDLE file = CreateFileW(directory.chars.data(),
                                    GENERIC_READ,
                                    FILE_SHARE_READ,
                                    nullptr,
                                    OPEN_EXISTING,
                                    FILE_ATTRIBUTE_NORMAL,
                                    nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return;
    }
    std::vector<char> text(4096, 0);
    DWORD read = 0;
    const BOOL ok = ReadFile(file, text.data(), static_cast<DWORD>(text.size() - 1), &read, nullptr);
    CloseHandle(file);
    if (ok == FALSE || read == 0) {
        return;
    }
    UINT loaded = 0;
    char* cursor = text.data();
    while (cursor != nullptr && *cursor != '\0' && loaded < kMaxParts) {
        char* line = cursor;
        char* next = std::strchr(cursor, '\n');
        if (next != nullptr) {
            *next = '\0';
            cursor = next + 1;
        } else {
            cursor = nullptr;
        }
        if (line[0] == '\0' || line[0] == '#' || line[0] == '\r') {
            continue;
        }
        char name[32]{};
        char albedo[64]{};
        char material[64]{};
        unsigned start = 0;
        unsigned count = 0;
        const int fields = sscanf_s(line,
                                    "%31s %u %u %63s %63s",
                                    name,
                                    static_cast<unsigned>(sizeof(name)),
                                    &start,
                                    &count,
                                    albedo,
                                    static_cast<unsigned>(sizeof(albedo)),
                                    material,
                                    static_cast<unsigned>(sizeof(material)));
        if (fields < 4 || count == 0 || name[0] == '\0' || albedo[0] == '\0') {
            continue;
        }
        // `hide` where a texture belongs drops the range instead of painting it, so a part table
        // can remove stock geometry the injected mesh replaces without touching a package.
        const bool hidden = std::strcmp(albedo, "hide") == 0;
        wchar_t fileWide[kPathCapacity]{};
        wchar_t materialWide[kPathCapacity]{};
        if (!hidden) {
            to_wide_suffix(albedo, profile, fileWide, kPathCapacity);
            if (fields >= 5 && material[0] != '\0' && std::strcmp(material, "-") != 0) {
                to_wide_suffix(material, profile, materialWide, kPathCapacity);
            }
        }
        seed_part(loaded,
                  start,
                  count,
                  name,
                  hidden ? nullptr : fileWide,
                  materialWide[0] != L'\0' ? materialWide : nullptr);
        g_parts[loaded].hidden = hidden;
        ++loaded;
    }
    if (loaded == 0) {
        return;
    }
    g_partCount = loaded;
    std::array<char, 96> note{};
    const int written = std::snprintf(note.data(),
                                      note.size(),
                                      "ev=custom_albedo stage=parts source=file count=%u",
                                      loaded);
    if (written > 0) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::info,
                         std::string_view(note.data(), static_cast<std::size_t>(written)));
    }
}

/** Releases the textures the previous profile uploaded, so a swap cannot leak them. */
void release_images() noexcept {
    for (GpuImage& image : g_images) {
        release_com(image.view);
        release_com(image.texture);
    }
    for (GpuImage& image : g_materials) {
        release_com(image.view);
        release_com(image.texture);
    }
}

/**
 * Fills `g_profiles` from the subdirectories of `characters\`.
 * A directory counts only when it holds a `parts.txt`, so a stray folder of loose textures is
 * not offered as a character the switcher cannot draw.
 */
void scan_profiles() noexcept {
    g_profileCount = 0;
    core::path::Buffer directory{};
    if (!core::path::artifact_directory(owning_module(), directory)
        || !core::path::append(directory, L"\\characters\\*")) {
        return;
    }
    WIN32_FIND_DATAW found{};
    const HANDLE search = FindFirstFileW(directory.chars.data(), &found);
    if (search == INVALID_HANDLE_VALUE) {
        return;
    }
    do {
        if ((found.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0
            || found.cFileName[0] == L'.') {
            continue;
        }
        core::path::Buffer table{};
        if (!core::path::artifact_directory(owning_module(), table)
            || !core::path::append(table, kProfileRoot) || !core::path::append(table, L"\\")
            || !core::path::append(table, found.cFileName)
            || !core::path::append(table, kProfileTable)
            || GetFileAttributesW(table.chars.data()) == INVALID_FILE_ATTRIBUTES) {
            continue;
        }
        Profile& profile = g_profiles[g_profileCount];
        profile = {};
        std::size_t at = 0;
        for (; found.cFileName[at] != L'\0' && at + 1 < kProfileNameCapacity; ++at) {
            const wchar_t wide = found.cFileName[at];
            profile.name[at] = wide < 128 ? static_cast<char>(wide) : '_';
        }
        profile.name[at] = '\0';
        ++g_profileCount;
    } while (g_profileCount < kMaxProfiles && FindNextFileW(search, &found) != FALSE);
    FindClose(search);
}

/** One texture the survey has already named, so a per-frame redraw logs once. */
struct Surveyed {
    UINT width;
    UINT height;
    UINT format;
};

Surveyed g_surveyed[kMaxSurveyed]{};
std::size_t g_surveyedCount{};
bool g_survey{};

/** @return True when `menu_survey.txt` exists beside the settings file. */
[[nodiscard]] bool survey_requested() noexcept {
    core::path::Buffer path{};
    if (!core::path::artifact_directory(owning_module(), path)
        || !core::path::append(path, kSurveyFile)) {
        return false;
    }
    return GetFileAttributesW(path.chars.data()) != INVALID_FILE_ATTRIBUTES;
}

/**
 * Name every distinct texture bound while drawing, so a screen can be identified by what it draws.
 *
 * The title screen has no model and no part table, so nothing else here can see it. Its lockup art
 * is a wide quad with a distinctive aspect ratio, which is what makes a dimensions listing enough
 * to pick it out. Dedupe is by (width, height, format): a UI texture is redrawn every frame, and
 * logging per draw would fill the log in under a second.
 *
 * Off unless `menu_survey.txt` exists, and stops after kMaxSurveyed regardless — this runs inside
 * DrawIndexed, which is called thousands of times per frame.
 */
void survey_textures(ID3D11DeviceContext* context) noexcept {
    if (!g_survey || g_surveyedCount >= kMaxSurveyed) {
        return;
    }
    ID3D11ShaderResourceView* views[4]{};
    context->PSGetShaderResources(0, 4, views);
    for (ID3D11ShaderResourceView* view : views) {
        if (view == nullptr) {
            continue;
        }
        ID3D11Resource* resource = nullptr;
        view->GetResource(&resource);
        if (resource != nullptr) {
            ID3D11Texture2D* texture = nullptr;
            if (SUCCEEDED(resource->QueryInterface(__uuidof(ID3D11Texture2D),
                                                   reinterpret_cast<void**>(&texture)))
                && texture != nullptr) {
                D3D11_TEXTURE2D_DESC desc{};
                texture->GetDesc(&desc);
                bool seen = false;
                for (std::size_t i = 0; i < g_surveyedCount; ++i) {
                    if (g_surveyed[i].width == desc.Width && g_surveyed[i].height == desc.Height
                        && g_surveyed[i].format == static_cast<UINT>(desc.Format)) {
                        seen = true;
                        break;
                    }
                }
                if (!seen && g_surveyedCount < kMaxSurveyed) {
                    g_surveyed[g_surveyedCount] = {desc.Width, desc.Height,
                                                   static_cast<UINT>(desc.Format)};
                    ++g_surveyedCount;
                    std::array<char, 192> line{};
                    const int written = std::snprintf(
                        line.data(), line.size(),
                        "ev=custom_albedo stage=survey n=%zu w=%u h=%u fmt=%u mips=%u aspect=%.3f",
                        g_surveyedCount, desc.Width, desc.Height,
                        static_cast<UINT>(desc.Format), desc.MipLevels,
                        desc.Height != 0 ? static_cast<double>(desc.Width) / desc.Height : 0.0);
                    if (written > 0) {
                        core::log::write(core::log::Channel::client, core::log::Level::info,
                                         std::string_view(line.data(),
                                                          static_cast<std::size_t>(written)));
                    }
                }
                texture->Release();
            }
            resource->Release();
        }
        view->Release();
    }
}

/**
 * Save one bound texture to PNG beside the settings file.
 *
 * Needed because a menu texture can hold several things at once: the "DESTINY 2" lockup carries
 * the wordmark *and* the glyph between its two halves in one 1050x114 image. Replacing only the
 * glyph means compositing over the original, and the original exists only as a GPU resource -
 * there is no file to open. This is how it becomes one.
 *
 * A staging copy is the only way to read it back: the bound texture has no CPU access. Only 32-bit
 * formats are handled; a BC-compressed source maps as blocks, which is not worth decoding here
 * when the textures we want to edit are all RGBA8.
 */
[[nodiscard]] bool save_texture_png(ID3D11Device* device,
                                    ID3D11DeviceContext* context,
                                    ID3D11Texture2D* source,
                                    const D3D11_TEXTURE2D_DESC& desc) noexcept {
    const DXGI_FORMAT format = desc.Format;
    const bool is32 = format == DXGI_FORMAT_R8G8B8A8_TYPELESS
                      || format == DXGI_FORMAT_R8G8B8A8_UNORM
                      || format == DXGI_FORMAT_R8G8B8A8_UNORM_SRGB
                      || format == DXGI_FORMAT_B8G8R8A8_UNORM
                      || format == DXGI_FORMAT_B8G8R8X8_UNORM;
    if (!is32) {
        return false;
    }
    D3D11_TEXTURE2D_DESC staged = desc;
    staged.Usage = D3D11_USAGE_STAGING;
    staged.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    staged.BindFlags = 0;
    staged.MiscFlags = 0;
    staged.MipLevels = 1;
    staged.ArraySize = 1;
    ID3D11Texture2D* copy = nullptr;
    if (FAILED(device->CreateTexture2D(&staged, nullptr, &copy)) || copy == nullptr) {
        return false;
    }
    context->CopyResource(copy, source);
    D3D11_MAPPED_SUBRESOURCE mapped{};
    if (FAILED(context->Map(copy, 0, D3D11_MAP_READ, 0, &mapped))) {
        copy->Release();
        return false;
    }
    std::vector<std::uint32_t> pixels(static_cast<std::size_t>(desc.Width) * desc.Height);
    const bool swapRB = format == DXGI_FORMAT_B8G8R8A8_UNORM
                        || format == DXGI_FORMAT_B8G8R8X8_UNORM;
    for (UINT y = 0; y < desc.Height; ++y) {
        const auto* row = reinterpret_cast<const std::uint32_t*>(
            static_cast<const std::uint8_t*>(mapped.pData) + static_cast<std::size_t>(y)
            * mapped.RowPitch);
        for (UINT x = 0; x < desc.Width; ++x) {
            std::uint32_t texel = row[x];
            if (swapRB) {
                texel = (texel & 0xFF00FF00u) | ((texel & 0xFFu) << 16) | ((texel >> 16) & 0xFFu);
            }
            pixels[static_cast<std::size_t>(y) * desc.Width + x] = texel;
        }
    }
    context->Unmap(copy, 0);
    copy->Release();

    core::path::Buffer path{};
    std::array<wchar_t, 64> leaf{};
    std::swprintf(leaf.data(), leaf.size(), L"\\menudump_%ux%u.png", desc.Width, desc.Height);
    if (!core::path::artifact_directory(owning_module(), path)
        || !core::path::append(path, std::wstring_view(leaf.data()))) {
        return false;
    }
    IWICImagingFactory* factory = nullptr;
    if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
                                IID_PPV_ARGS(&factory)))) {
        return false;
    }
    IWICStream* stream = nullptr;
    IWICBitmapEncoder* encoder = nullptr;
    IWICBitmapFrameEncode* frame = nullptr;
    bool ok = false;
    if (SUCCEEDED(factory->CreateStream(&stream))
        && SUCCEEDED(stream->InitializeFromFilename(path.chars.data(), GENERIC_WRITE))
        && SUCCEEDED(factory->CreateEncoder(GUID_ContainerFormatPng, nullptr, &encoder))
        && SUCCEEDED(encoder->Initialize(stream, WICBitmapEncoderNoCache))
        && SUCCEEDED(encoder->CreateNewFrame(&frame, nullptr))
        && SUCCEEDED(frame->Initialize(nullptr))
        && SUCCEEDED(frame->SetSize(desc.Width, desc.Height))) {
        WICPixelFormatGUID wanted = GUID_WICPixelFormat32bppRGBA;
        if (SUCCEEDED(frame->SetPixelFormat(&wanted))
            && SUCCEEDED(frame->WritePixels(
                desc.Height, desc.Width * 4,
                static_cast<UINT>(pixels.size() * sizeof(std::uint32_t)),
                reinterpret_cast<BYTE*>(pixels.data())))
            && SUCCEEDED(frame->Commit()) && SUCCEEDED(encoder->Commit())) {
            ok = true;
        }
    }
    if (frame != nullptr) frame->Release();
    if (encoder != nullptr) encoder->Release();
    if (stream != nullptr) stream->Release();
    factory->Release();
    return ok;
}

/** One texture size to tint, and the colour to tint it. */
struct Probe {
    UINT width;
    UINT height;
    /** DXGI format to require, or 0 for any. Several screens share a size but not a format. */
    UINT format;
    ID3D11ShaderResourceView* view;
};

Probe g_probes[kMaxProbes]{};
std::size_t g_probeCount{};

/**
 * Read `menu_probe.txt` and build one solid-colour texture per line: `<width> <height> RRGGBB`.
 *
 * The survey names the textures a screen draws but cannot say *which* is which — several wide
 * quads on the title screen are plausible lockups. Tinting each a different colour answers all of
 * them in a single launch and a single screenshot, rather than one guess per launch.
 *
 * Sizes, not pointers: a UI texture is recreated between frames, so its address is not stable
 * while its dimensions are.
 */
void load_probes(ID3D11Device* device) noexcept {
    core::path::Buffer path{};
    if (!core::path::artifact_directory(owning_module(), path)
        || !core::path::append(path, kProbeFile)) {
        return;
    }
    const HANDLE file = CreateFileW(
        path.chars.data(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return;
    }
    // Comments count toward this, and the file carries the measured geometry for each screen.
    std::array<char, 4096> text{};
    DWORD read = 0;
    const BOOL ok = ReadFile(file, text.data(), static_cast<DWORD>(text.size() - 1), &read, nullptr);
    CloseHandle(file);
    if (ok == FALSE || read == 0) {
        return;
    }
    const char* cursor = text.data();
    while (*cursor != '\0' && g_probeCount < kMaxProbes) {
        while (*cursor == '\r' || *cursor == '\n' || *cursor == ' ' || *cursor == '\t') {
            ++cursor;
        }
        if (*cursor == '#') {
            while (*cursor != '\0' && *cursor != '\n') {
                ++cursor;
            }
            continue;
        }
        unsigned width = 0;
        unsigned height = 0;
        std::array<char, 96> token{};
        int consumed = 0;
        if (sscanf_s(cursor, "%u %u %95s%n", &width, &height, token.data(),
                     static_cast<unsigned>(token.size()), &consumed)
            != 3) {
            break;
        }
        cursor += consumed;
        // An optional `@<dxgi format>` before the value narrows the match. The title screen and
        // the character select screen both bind 1920x1200, so size alone cannot tell them apart -
        // but their formats differ, and the survey reports the format of every texture it names.
        unsigned format = 0;
        if (token[0] == '@') {
            format = std::strtoul(token.data() + 1, nullptr, 10);
            if (sscanf_s(cursor, " %95s%n", token.data(), static_cast<unsigned>(token.size()),
                         &consumed)
                != 1) {
                break;
            }
            cursor += consumed;
        }
        ID3D11ShaderResourceView* view = nullptr;
        // A dot means a filename. WIC decodes jpg as readily as png, so the extension only has to
        // be something WIC knows - the name is passed through untouched.
        const bool isFile = std::strchr(token.data(), '.') != nullptr;
        if (isFile) {
            std::array<wchar_t, kPathCapacity> wide{};
            wide[0] = L'\\';
            std::size_t at = 1;
            for (std::size_t i = 0; token[i] != '\0' && at + 1 < wide.size(); ++i, ++at) {
                wide[at] = static_cast<wchar_t>(static_cast<unsigned char>(token[i]));
            }
            std::vector<std::uint32_t> pixels;
            UINT imageWidth = 0;
            UINT imageHeight = 0;
            GpuImage image{};
            if (load_png(std::wstring_view(wide.data(), at), pixels, imageWidth, imageHeight)
                && upload_image(device, pixels, imageWidth, imageHeight,
                                DXGI_FORMAT_R8G8B8A8_UNORM_SRGB, image)) {
                // The view keeps the resource alive, so the texture reference can go now.
                if (image.texture != nullptr) {
                    image.texture->Release();
                }
                view = image.view;
            }
        } else {
            const unsigned colour = std::strtoul(token.data(), nullptr, 16);
            const std::uint32_t texel = 0xFF000000u | ((colour & 0xFFu) << 16)
                                        | (colour & 0xFF00u) | ((colour >> 16) & 0xFFu);
            D3D11_TEXTURE2D_DESC desc{};
            desc.Width = 1;
            desc.Height = 1;
            desc.MipLevels = 1;
            desc.ArraySize = 1;
            desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
            desc.SampleDesc.Count = 1;
            desc.Usage = D3D11_USAGE_IMMUTABLE;
            desc.BindFlags = D3D11_BIND_SHADER_RESOURCE;
            D3D11_SUBRESOURCE_DATA seed{};
            seed.pSysMem = &texel;
            seed.SysMemPitch = sizeof(texel);
            ID3D11Texture2D* texture = nullptr;
            if (SUCCEEDED(device->CreateTexture2D(&desc, &seed, &texture)) && texture != nullptr) {
                if (FAILED(device->CreateShaderResourceView(texture, nullptr, &view))) {
                    view = nullptr;
                }
                texture->Release();
            }
        }
        if (view == nullptr) {
            continue;
        }
        g_probes[g_probeCount] = {width, height, format, view};
        ++g_probeCount;
        std::array<char, 192> line{};
        const int written =
            std::snprintf(line.data(), line.size(),
                          "ev=custom_albedo stage=probe n=%zu w=%u h=%u fmt=%u src=%s",
                          g_probeCount, width, height, format, token.data());
        if (written > 0) {
            core::log::write(core::log::Channel::client, core::log::Level::info,
                             std::string_view(line.data(), static_cast<std::size_t>(written)));
        }
    }
}

/**
 * Swap any bound texture whose size matches a probe for that probe's flat colour.
 *
 * @return True when something was swapped, in which case the caller must restore afterwards.
 */
/** One distinct (texture, draw) pairing a probe has matched, so each is reported once. */
struct ProbeHit {
    UINT width;
    UINT height;
    UINT format;
    UINT start;
    UINT count;
};

ProbeHit g_hits[32]{};
std::size_t g_hitCount{};

/**
 * Name each distinct draw a probe lands on, once.
 *
 * Size and format together still cannot separate every screen - the title screen's top band, the
 * boot screen and the screen that loads into character select all bind 1920x1200 at format 98. The
 * **draw** is the thing that actually differs between UI elements, the same way the character's
 * parts are told apart, so this records the index range alongside the texture. If the three come
 * back with different ranges, they can be addressed separately without any guessing about timing.
 */
void note_probe_hit(UINT width, UINT height, UINT format, UINT start, UINT count) noexcept {
    if (g_hitCount >= std::size(g_hits)) {
        return;
    }
    for (std::size_t i = 0; i < g_hitCount; ++i) {
        if (g_hits[i].width == width && g_hits[i].height == height && g_hits[i].format == format
            && g_hits[i].start == start && g_hits[i].count == count) {
            return;
        }
    }
    g_hits[g_hitCount] = {width, height, format, start, count};
    ++g_hitCount;
    std::array<char, 192> line{};
    const int written = std::snprintf(
        line.data(), line.size(),
        "ev=custom_albedo stage=probe_hit n=%zu w=%u h=%u fmt=%u start=%u count=%u", g_hitCount,
        width, height, format, start, count);
    if (written > 0) {
        core::log::write(core::log::Channel::client, core::log::Level::info,
                         std::string_view(line.data(), static_cast<std::size_t>(written)));
    }
}

[[nodiscard]] bool apply_probes(ID3D11DeviceContext* context,
                                ID3D11ShaderResourceView** saved,
                                UINT slots,
                                UINT startIndex,
                                UINT indexCount) noexcept {
    if (g_probeCount == 0) {
        return false;
    }
    context->PSGetShaderResources(0, slots, saved);
    bool swapped = false;
    ID3D11ShaderResourceView* replaced[kProbeSlots]{};
    for (UINT slot = 0; slot < slots; ++slot) {
        replaced[slot] = saved[slot];
        if (saved[slot] == nullptr) {
            continue;
        }
        ID3D11Resource* resource = nullptr;
        saved[slot]->GetResource(&resource);
        if (resource == nullptr) {
            continue;
        }
        ID3D11Texture2D* texture = nullptr;
        if (SUCCEEDED(resource->QueryInterface(__uuidof(ID3D11Texture2D),
                                               reinterpret_cast<void**>(&texture)))
            && texture != nullptr) {
            D3D11_TEXTURE2D_DESC desc{};
            texture->GetDesc(&desc);
            for (std::size_t i = 0; i < g_probeCount; ++i) {
                if (g_probes[i].width != desc.Width || g_probes[i].height != desc.Height) {
                    continue;
                }
                // Format 0 means "any", so size-only rules keep working unchanged.
                if (g_probes[i].format != 0
                    && g_probes[i].format != static_cast<UINT>(desc.Format)) {
                    continue;
                }
                replaced[slot] = g_probes[i].view;
                swapped = true;
                note_probe_hit(desc.Width, desc.Height, static_cast<UINT>(desc.Format), startIndex,
                               indexCount);
                break;
            }
            texture->Release();
        }
        resource->Release();
    }
    if (swapped) {
        context->PSSetShaderResources(0, slots, replaced);
    }
    return swapped;
}

/** One texture size to save, and whether it has been saved this run. */
struct DumpRequest {
    UINT width;
    UINT height;
    bool done;
};

DumpRequest g_dumps[kMaxProbes]{};
std::size_t g_dumpCount{};

/** Read `menu_dump.txt`: one `<width> <height>` per line. */
void load_dump_list() noexcept {
    core::path::Buffer path{};
    if (!core::path::artifact_directory(owning_module(), path)
        || !core::path::append(path, kDumpFile)) {
        return;
    }
    const HANDLE file = CreateFileW(
        path.chars.data(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return;
    }
    std::array<char, 512> text{};
    DWORD read = 0;
    const BOOL ok = ReadFile(file, text.data(), static_cast<DWORD>(text.size() - 1), &read, nullptr);
    CloseHandle(file);
    if (ok == FALSE || read == 0) {
        return;
    }
    const char* cursor = text.data();
    while (*cursor != '\0' && g_dumpCount < kMaxProbes) {
        while (*cursor == '\r' || *cursor == '\n' || *cursor == ' ' || *cursor == '\t') {
            ++cursor;
        }
        if (*cursor == '#') {
            while (*cursor != '\0' && *cursor != '\n') {
                ++cursor;
            }
            continue;
        }
        unsigned width = 0;
        unsigned height = 0;
        int consumed = 0;
        if (sscanf_s(cursor, "%u %u%n", &width, &height, &consumed) != 2) {
            break;
        }
        cursor += consumed;
        g_dumps[g_dumpCount] = {width, height, false};
        ++g_dumpCount;
    }
}

void dump_textures(ID3D11DeviceContext* context) noexcept {
    if (g_dumpCount == 0 || g_device == nullptr) {
        return;
    }
    bool pending = false;
    for (std::size_t i = 0; i < g_dumpCount; ++i) {
        pending = pending || !g_dumps[i].done;
    }
    if (!pending) {
        return;
    }
    ID3D11ShaderResourceView* views[kProbeSlots]{};
    context->PSGetShaderResources(0, kProbeSlots, views);
    for (ID3D11ShaderResourceView* view : views) {
        if (view == nullptr) {
            continue;
        }
        ID3D11Resource* resource = nullptr;
        view->GetResource(&resource);
        if (resource != nullptr) {
            ID3D11Texture2D* texture = nullptr;
            if (SUCCEEDED(resource->QueryInterface(__uuidof(ID3D11Texture2D),
                                                   reinterpret_cast<void**>(&texture)))
                && texture != nullptr) {
                D3D11_TEXTURE2D_DESC desc{};
                texture->GetDesc(&desc);
                for (std::size_t i = 0; i < g_dumpCount; ++i) {
                    if (g_dumps[i].done || g_dumps[i].width != desc.Width
                        || g_dumps[i].height != desc.Height) {
                        continue;
                    }
                    const bool saved = save_texture_png(g_device, context, texture, desc);
                    g_dumps[i].done = true;
                    std::array<char, 160> line{};
                    const int written = std::snprintf(
                        line.data(), line.size(),
                        "ev=custom_albedo stage=dump w=%u h=%u fmt=%u result=%s",
                        desc.Width, desc.Height, static_cast<UINT>(desc.Format),
                        saved ? "ok" : "fail");
                    if (written > 0) {
                        core::log::write(core::log::Channel::client, core::log::Level::info,
                                         std::string_view(line.data(),
                                                          static_cast<std::size_t>(written)));
                    }
                }
                texture->Release();
            }
            resource->Release();
        }
        view->Release();
    }
}

/** @return Slot of the profile `active.txt` names, or `g_profileCount` when it names none. */
[[nodiscard]] std::size_t remembered_profile() noexcept {
    core::path::Buffer path{};
    if (!core::path::artifact_directory(owning_module(), path)
        || !core::path::append(path, kActiveFile)) {
        return g_profileCount;
    }
    const HANDLE file = CreateFileW(
        path.chars.data(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return g_profileCount;
    }
    std::array<char, kProfileNameCapacity> text{};
    DWORD read = 0;
    const BOOL ok = ReadFile(file, text.data(), static_cast<DWORD>(text.size() - 1), &read, nullptr);
    CloseHandle(file);
    if (ok == FALSE || read == 0) {
        return g_profileCount;
    }
    for (char& value : text) {
        if (value == '\r' || value == '\n') {
            value = '\0';
        }
    }
    for (std::size_t index = 0; index < g_profileCount; ++index) {
        if (std::strcmp(g_profiles[index].name, text.data()) == 0) {
            return index;
        }
    }
    return g_profileCount;
}

/** Records the switcher's choice so the next launch draws the same character. */
void remember_profile(std::size_t index) noexcept {
    core::path::Buffer path{};
    if (!core::path::artifact_directory(owning_module(), path)
        || !core::path::append(path, kActiveFile)) {
        return;
    }
    const HANDLE file = CreateFileW(
        path.chars.data(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return;
    }
    const char* name = index < g_profileCount ? g_profiles[index].name : "";
    DWORD written = 0;
    (void)WriteFile(file, name, static_cast<DWORD>(std::strlen(name)), &written, nullptr);
    CloseHandle(file);
}

[[nodiscard]] bool create_images(ID3D11Device* device) noexcept {
    for (std::size_t index = 0; index < g_partCount && index < kMaxParts; ++index) {
        std::vector<std::uint32_t> pixels;
        UINT width = 0;
        UINT height = 0;
        UINT albedoWidth = 0;
        UINT albedoHeight = 0;
        // A hidden range is never drawn, so it needs no texture and a missing file is not an error.
        if (g_parts[index].hidden) {
            continue;
        }
        if (!load_png(g_parts[index].file, pixels, albedoWidth, albedoHeight)
            || !upload_image(device,
                             pixels,
                             albedoWidth,
                             albedoHeight,
                             DXGI_FORMAT_R8G8B8A8_UNORM_SRGB,
                             g_images[index])) {
            std::array<char, 128> line{};
            const int written = std::snprintf(line.data(),
                                              line.size(),
                                              "ev=custom_albedo stage=atlas result=fail part=%s",
                                              g_parts[index].name);
            if (written > 0) {
                core::log::write(core::log::Channel::client,
                                 core::log::Level::error,
                                 std::string_view(line.data(), static_cast<std::size_t>(written)));
            }
            return false;
        }
        pixels.clear();
        width = 0;
        height = 0;
        const wchar_t* material = g_parts[index].hasMaterial ? g_parts[index].material : nullptr;
        const bool loaded = material != nullptr && load_png(material, pixels, width, height);
        if (!loaded) {
            pixels.assign(1, 0xFF008DFF);
            width = 1;
            height = 1;
        }
        if (!upload_image(device,
                          pixels,
                          width,
                          height,
                          DXGI_FORMAT_R8G8B8A8_UNORM,
                          g_materials[index])) {
            return false;
        }
        std::array<char, 192> line{};
        const int written = std::snprintf(line.data(),
                                          line.size(),
                                          "ev=custom_albedo stage=atlas result=ok part=%s "
                                          "w=%u h=%u source=png material=%s",
                                          g_parts[index].name,
                                          albedoWidth,
                                          albedoHeight,
                                          loaded ? "glb" : "default");
        if (written > 0) {
            core::log::write(core::log::Channel::client,
                             core::log::Level::info,
                             std::string_view(line.data(), static_cast<std::size_t>(written)));
        }
    }
    return true;
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
                   const Part& part,
                   UINT start,
                   UINT count,
                   Draw&& draw) noexcept {
    const BoundTargets targets = inspect_targets(context);
    note_targets(targets);
    if (targets.bound == 0 || !is_character_gbuffer(targets)) {
        note_skip_rt(part, targets);
        draw();
        return;
    }
    note_first(part, start, count, targets);
    if (part.hidden) {
        // Dropping the call is the whole blank: no colour, no depth, no G-buffer write for this
        // range. It is gated on the character G-buffer like painting is, because an exact
        // (start, count) collision with a UI quad would otherwise blank the interface - the same
        // trap v13 fell into by subset-matching.
        return;
    }
    if (g_shader == nullptr) {
        draw();
        return;
    }
    const std::size_t index = static_cast<std::size_t>(&part - g_parts.data());
    ID3D11ShaderResourceView* views[2]{g_images[index].view, g_materials[index].view};
    SavedPs saved{};
    capture_ps(context, saved);
    dump_game_ps(saved.shader);
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
    // Before the part match, because the screen this exists to identify has no part table at all.
    survey_textures(context);
    dump_textures(context);
    const Part* part = match(startIndex, indexCount);
    if (part == nullptr) {
        note_miss(startIndex, indexCount);
        // Probing is confined to draws the character never claims, so an identification pass
        // cannot disturb the body. Restore immediately: the game keeps drawing with these slots.
        ID3D11ShaderResourceView* saved[kProbeSlots]{};
        if (apply_probes(context, saved, kProbeSlots, startIndex, indexCount)) {
            next(context, indexCount, startIndex, baseVertex);
            context->PSSetShaderResources(0, kProbeSlots, saved);
            for (ID3D11ShaderResourceView* view : saved) {
                if (view != nullptr) {
                    view->Release();
                }
            }
            return;
        }
        next(context, indexCount, startIndex, baseVertex);
        return;
    }
    draw_replaced(context, *part, startIndex, indexCount, [&]() noexcept {
        next(context, indexCount, startIndex, baseVertex);
    });
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
    const Part* part = match(startIndex, indexCountPerInstance);
    if (part == nullptr) {
        note_miss(startIndex, indexCountPerInstance);
        next(context, indexCountPerInstance, instanceCount, startIndex, baseVertex, startInstance);
        return;
    }
    draw_replaced(context, *part, startIndex, indexCountPerInstance, [&]() noexcept {
        next(context, indexCountPerInstance, instanceCount, startIndex, baseVertex, startInstance);
    });
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

namespace {

/**
 * Reads one profile's part table and uploads its textures over whatever was drawn before.
 * @param index Profile slot, or `g_profileCount` for the flat pre-profile layout.
 * @return True when the table was read and every visible part has a texture.
 */
[[nodiscard]] bool apply_profile(std::size_t index) noexcept {
    if (g_device == nullptr) {
        return false;
    }
    const char* profile = index < g_profileCount ? g_profiles[index].name : nullptr;
    // Seeded first so a table that names fewer parts than the defaults cannot leave stale rows,
    // and so a profile whose file is unreadable still draws the shipped character.
    seed_defaults();
    load_parts_file(profile);
    release_images();
    if (!create_images(g_device)) {
        // A half-applied profile would draw the new ranges with released textures, so a profile
        // whose textures will not load puts the shipped character back rather than a broken one.
        core::log::write(core::log::Channel::client,
                         core::log::Level::error,
                         "ev=custom_albedo stage=profile result=fail action=revert_defaults");
        seed_defaults();
        load_parts_file(nullptr);
        release_images();
        g_activeProfile = g_profileCount;
        (void)create_images(g_device);
        return false;
    }
    g_activeProfile = index;
    std::array<char, 128> line{};
    const int written = std::snprintf(line.data(),
                                      line.size(),
                                      "ev=custom_albedo stage=profile name=%s parts=%u",
                                      profile != nullptr ? profile : "(defaults)",
                                      g_partCount);
    if (written > 0) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::info,
                         std::string_view(line.data(), static_cast<std::size_t>(written)));
    }
    return true;
}

} // namespace

void attach(ID3D11Device* device, ID3D11DeviceContext* context) noexcept {
    detach();
    if (device == nullptr || context == nullptr) {
        return;
    }
    g_device = device;
    // Read once at attach, so the per-draw cost of an unwanted survey is a single bool test.
    g_survey = survey_requested();
    load_probes(device);
    load_dump_list();
    if (g_survey) {
        core::log::write(core::log::Channel::client, core::log::Level::info,
                         "ev=custom_albedo stage=survey result=on "
                         "(menu_survey.txt present; delete it to stop)");
    }
    scan_profiles();
    const std::size_t remembered = remembered_profile();
    seed_defaults();
    load_parts_file(remembered < g_profileCount ? g_profiles[remembered].name : nullptr);
    g_activeProfile = remembered;
    if (!compile_shader(device) || !create_images(device) || !install_hooks(device, context)) {
        detach();
        return;
    }
    std::array<char, 128> line{};
    const int written = std::snprintf(line.data(),
                                      line.size(),
                                      "ev=custom_albedo stage=attach result=ok mode=ours "
                                      "profiles=%zu active=%s",
                                      g_profileCount,
                                      remembered < g_profileCount ? g_profiles[remembered].name
                                                                  : "(defaults)");
    if (written > 0) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::info,
                         std::string_view(line.data(), static_cast<std::size_t>(written)));
    }
}

std::size_t profile_count() noexcept {
    return g_profileCount;
}

std::string_view profile_name(std::size_t index) noexcept {
    return index < g_profileCount ? std::string_view(g_profiles[index].name) : std::string_view{};
}

std::size_t active_profile() noexcept {
    return g_activeProfile;
}

void rescan_profiles() noexcept {
    // The active slot is an index into the old listing, so it is re-resolved by name rather than
    // carried over - a profile added or removed on disk would otherwise shift what is drawn.
    const char* active = g_activeProfile < g_profileCount ? g_profiles[g_activeProfile].name
                                                          : nullptr;
    std::array<char, kProfileNameCapacity> held{};
    if (active != nullptr) {
        (void)std::snprintf(held.data(), held.size(), "%s", active);
    }
    scan_profiles();
    g_activeProfile = g_profileCount;
    for (std::size_t index = 0; index < g_profileCount; ++index) {
        if (held[0] != '\0' && std::strcmp(g_profiles[index].name, held.data()) == 0) {
            g_activeProfile = index;
            break;
        }
    }
}

bool select_profile(std::size_t index) noexcept {
    // Called from the overlay, which draws on the render thread inside Present - the same thread
    // that services DrawIndexed - so the part table and its textures are never swapped underneath
    // a draw that is reading them.
    if (index > g_profileCount || !apply_profile(index)) {
        return false;
    }
    remember_profile(index);
    return true;
}

std::size_t part_count() noexcept {
    return g_partCount;
}

PartView part_at(std::size_t index) noexcept {
    if (index >= g_partCount || index >= kMaxParts) {
        return {};
    }
    const Part& part = g_parts[index];
    return PartView{std::string_view(part.name), part.start, part.count, part.hidden};
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
    release_images();
    // Borrowed from attach, never addrefed, so it is dropped rather than released.
    g_device = nullptr;
    AcquireSRWLockExclusive(&g_psLock);
    g_pixelShaders.clear();
    ReleaseSRWLockExclusive(&g_psLock);
    g_loggedParts = 0;
    g_loggedMisses = 0;
    g_seenMisses.fill(0);
    g_loggedTargets = false;
    g_loggedDepthOnly = false;
    g_dumpedPs = false;
    g_seenTargetKeys = {};
    g_seenTargetCount = 0;
    g_loggedSkips = 0;
}

} // namespace sunrise::client::hooks::custom_albedo
