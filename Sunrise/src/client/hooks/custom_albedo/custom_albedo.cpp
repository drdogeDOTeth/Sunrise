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

struct Part {
    UINT start{};
    UINT count{};
    const char* name{};
    const wchar_t* file{};
    const wchar_t* material{};
};

constexpr std::array<Part, 5> kParts{{
    {0, 74358, "tank", L"\\custom_tank.png", L"\\custom_tank_mr.png"},
    {74358, 11574, "mask", L"\\custom_mask.png", L"\\custom_mask_mr.png"},
    {85932, 3570, "necklace", L"\\custom_necklace.png", L"\\custom_necklace_mr.png"},
    {89502, 42666, "skin", L"\\custom_skin.png", L"\\custom_skin_mr.png"},
    {132168, 6036, "twirl", L"\\custom_twirl.png", nullptr},
}};

constexpr UINT kMeshIndices = 138204;

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
std::array<GpuImage, kParts.size()> g_images{};
std::array<GpuImage, kParts.size()> g_materials{};
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

[[nodiscard]] const Part* match(UINT start, UINT count) noexcept {
    // Exact pairs only. v13 subset-match treated start=0 UI quads as the tank
    // and smashed character select (ALWAYS depth + RT0 RGB).
    for (const Part& part : kParts) {
        if (part.start == start && part.count == count) {
            return &part;
        }
    }
    return nullptr;
}

void note_miss(UINT start, UINT count) noexcept {
    if (g_loggedMisses >= 16 || count == 0) {
        return;
    }
    const UINT end = start + count;
    if (start >= kMeshIndices || end <= start) {
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

void note_first(const Part& part, UINT start, UINT count, const BoundTargets& targets) noexcept {
    const std::uint32_t bit = 1u << static_cast<std::uint32_t>(&part - kParts.data());
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

[[nodiscard]] bool create_images(ID3D11Device* device) noexcept {
    for (std::size_t index = 0; index < kParts.size(); ++index) {
        std::vector<std::uint32_t> pixels;
        UINT width = 0;
        UINT height = 0;
        UINT albedoWidth = 0;
        UINT albedoHeight = 0;
        if (!load_png(kParts[index].file, pixels, albedoWidth, albedoHeight)
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
                                              kParts[index].name);
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
        const wchar_t* material = kParts[index].material;
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
                                          kParts[index].name,
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
    if (g_shader == nullptr) {
        draw();
        return;
    }
    const BoundTargets targets = inspect_targets(context);
    note_targets(targets);
    if (targets.bound == 0 || !is_character_gbuffer(targets)) {
        note_skip_rt(part, targets);
        draw();
        return;
    }
    note_first(part, start, count, targets);
    const std::size_t index = static_cast<std::size_t>(&part - kParts.data());
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
    const Part* part = match(startIndex, indexCount);
    if (part == nullptr) {
        note_miss(startIndex, indexCount);
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

void attach(ID3D11Device* device, ID3D11DeviceContext* context) noexcept {
    detach();
    if (device == nullptr || context == nullptr) {
        return;
    }
    if (!compile_shader(device) || !create_images(device) || !install_hooks(device, context)) {
        detach();
        return;
    }
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
    for (GpuImage& image : g_images) {
        release_com(image.view);
        release_com(image.texture);
    }
    for (GpuImage& image : g_materials) {
        release_com(image.view);
        release_com(image.texture);
    }
    AcquireSRWLockExclusive(&g_psLock);
    g_pixelShaders.clear();
    ReleaseSRWLockExclusive(&g_psLock);
    g_loggedParts = 0;
    g_loggedMisses = 0;
    g_loggedTargets = false;
    g_loggedDepthOnly = false;
    g_dumpedPs = false;
    g_seenTargetKeys = {};
    g_seenTargetCount = 0;
    g_loggedSkips = 0;
}

} // namespace sunrise::client::hooks::custom_albedo
