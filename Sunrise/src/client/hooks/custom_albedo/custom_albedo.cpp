/**
 * Draw-time replacement for the Scatterhorn-chest dye PS on the five custom
 * parts. Goal is the GLB / Blender look (green SkinTats, charcoal tank, black
 * mask, twirl, teal necklace) — not a tint on the dye tile.
 *
 * Live dye PS (objs/live_chest_ps.asm) contract:
 * - TEXCOORD3.xy is the mesh UV. TEXCOORD0..2 are TBN.
 * - Flat n_map (0,0,1) → worldN = TEXCOORD0.xyz (v0*nz + v1*nx + v2*ny).
 * - o1.xyz = saturate(worldN * ~0.375 + 0.5). o1.w is 0 or ~0.33, never 1.
 * - o2.w = TEXCOORD0.w. o0.w fallback is ~0.2.
 *
 * v11 proved the encode: GLB colours showed, smeared because one 512 quilt
 * was sampled with 0–1 UVs. v12 binds the five GLB albedos per part.
 * Only t0/s0 are replaced; game t1+ stay bound.
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
};

constexpr std::array<Part, 5> kParts{{
    {0, 74358, "tank", L"\\custom_tank.png"},
    {74358, 11574, "mask", L"\\custom_mask.png"},
    {85932, 3570, "necklace", L"\\custom_necklace.png"},
    {89502, 42666, "skin", L"\\custom_skin.png"},
    {132168, 6036, "twirl", L"\\custom_twirl.png"},
}};

struct GpuImage {
    ID3D11Texture2D* texture{};
    ID3D11ShaderResourceView* view{};
};

constexpr char kShaderSource[] = R"(
Texture2D Atlas : register(t0);
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

    // Dye PS TBN: worldN = v0*nz + v1*nx + v2*ny. Flat map => TEXCOORD0.xyz.
    const float3 worldN = normalize(input.tangent.xyz);
    output.encodedNormal = float4(saturate(worldN * 0.375 + 0.5), 0.0);
    output.material = float4(0.5, 0.25, 0.0, input.tangent.w);
    return output;
}
)";

constexpr UINT kSamplerSlots = 8;
constexpr UINT kRenderTargets = D3D11_SIMULTANEOUS_RENDER_TARGET_COUNT;

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
std::uint32_t g_loggedParts{};
bool g_loggedTargets{};
bool g_loggedDepthOnly{};
bool g_dumpedPs{};
SRWLOCK g_psLock{SRWLOCK_INIT};
std::vector<PsRecord> g_pixelShaders{};

[[nodiscard]] const Part* match(UINT start, UINT count) noexcept {
    for (const Part& part : kParts) {
        if (part.start == start && part.count == count) {
            return &part;
        }
    }
    return nullptr;
}

void note_first(const Part& part, UINT start, UINT count) noexcept {
    const std::uint32_t bit = 1u << static_cast<std::uint32_t>(&part - kParts.data());
    if ((g_loggedParts & bit) != 0) {
        return;
    }
    g_loggedParts |= bit;
    std::array<char, 160> line{};
    const int written = std::snprintf(line.data(),
                                      line.size(),
                                      "ev=custom_albedo stage=draw part=%s start=%u count=%u "
                                      "result=ok",
                                      part.name,
                                      start,
                                      count);
    if (written > 0) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::info,
                         std::string_view(line.data(), static_cast<std::size_t>(written)));
    }
}

[[nodiscard]] UINT note_targets(ID3D11DeviceContext* context) noexcept {
    ID3D11RenderTargetView* views[kRenderTargets]{};
    ID3D11DepthStencilView* depth = nullptr;
    context->OMGetRenderTargets(kRenderTargets, views, &depth);
    UINT formats[kRenderTargets]{};
    UINT bound = 0;
    for (UINT slot = 0; slot < kRenderTargets; ++slot) {
        if (views[slot] == nullptr) {
            continue;
        }
        D3D11_RENDER_TARGET_VIEW_DESC description{};
        views[slot]->GetDesc(&description);
        formats[slot] = static_cast<UINT>(description.Format);
        ++bound;
        views[slot]->Release();
    }
    const UINT hasDepth = depth != nullptr ? 1u : 0u;
    if (depth != nullptr) {
        depth->Release();
    }
    const bool shouldLog = bound == 0 ? !g_loggedDepthOnly : !g_loggedTargets;
    if (!shouldLog) {
        return bound;
    }
    if (bound == 0) {
        g_loggedDepthOnly = true;
    } else {
        g_loggedTargets = true;
    }
    std::array<char, 192> line{};
    const int written = std::snprintf(line.data(),
                                      line.size(),
                                      "ev=custom_albedo stage=targets nrt=%u "
                                      "f0=%u f1=%u f2=%u depth=%u",
                                      bound,
                                      formats[0],
                                      formats[1],
                                      formats[2],
                                      hasDepth);
    if (written > 0) {
        core::log::write(core::log::Channel::client,
                         core::log::Level::info,
                         std::string_view(line.data(), static_cast<std::size_t>(written)));
    }
    return bound;
}

template <typename Interface> void release_com(Interface*& object) noexcept {
    if (object != nullptr) {
        object->Release();
        object = nullptr;
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

[[nodiscard]] bool create_images(ID3D11Device* device) noexcept {
    for (std::size_t index = 0; index < kParts.size(); ++index) {
        std::vector<std::uint32_t> pixels;
        UINT width = 0;
        UINT height = 0;
        if (!load_png(kParts[index].file, pixels, width, height)) {
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
        D3D11_TEXTURE2D_DESC description{};
        description.Width = width;
        description.Height = height;
        description.MipLevels = 1;
        description.ArraySize = 1;
        description.Format = DXGI_FORMAT_R8G8B8A8_UNORM_SRGB;
        description.SampleDesc.Count = 1;
        description.Usage = D3D11_USAGE_IMMUTABLE;
        description.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        const D3D11_SUBRESOURCE_DATA initial{pixels.data(), width * 4, 0};
        GpuImage& image = g_images[index];
        if (FAILED(device->CreateTexture2D(&description, &initial, &image.texture))
            || image.texture == nullptr
            || FAILED(device->CreateShaderResourceView(image.texture, nullptr, &image.view))
            || image.view == nullptr) {
            return false;
        }
        std::array<char, 160> line{};
        const int written = std::snprintf(line.data(),
                                          line.size(),
                                          "ev=custom_albedo stage=atlas result=ok part=%s "
                                          "w=%u h=%u source=png",
                                          kParts[index].name,
                                          width,
                                          height);
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
void draw_replaced(ID3D11DeviceContext* context, const Part& part, Draw&& draw) noexcept {
    if (g_shader == nullptr) {
        draw();
        return;
    }
    if (note_targets(context) == 0) {
        draw();
        return;
    }
    const std::size_t index = static_cast<std::size_t>(&part - kParts.data());
    ID3D11ShaderResourceView* view = g_images[index].view;
    ID3D11PixelShader* previous = nullptr;
    UINT instances = 0;
    context->PSGetShader(&previous, nullptr, &instances);
    dump_game_ps(previous);
    ID3D11SamplerState* previousSamplers[kSamplerSlots]{};
    context->PSGetSamplers(0, kSamplerSlots, previousSamplers);
    ID3D11ShaderResourceView* previousView = nullptr;
    context->PSGetShaderResources(0, 1, &previousView);
    ID3D11BlendState* previousBlend = nullptr;
    FLOAT blendFactor[4]{};
    UINT sampleMask = 0;
    context->OMGetBlendState(&previousBlend, blendFactor, &sampleMask);
    context->PSSetShader(g_shader, nullptr, 0);
    if (g_sampler != nullptr) {
        context->PSSetSamplers(0, 1, &g_sampler);
    }
    if (view != nullptr) {
        context->PSSetShaderResources(0, 1, &view);
    }
    if (g_blend != nullptr) {
        const FLOAT opaque[4]{1.0f, 1.0f, 1.0f, 1.0f};
        context->OMSetBlendState(g_blend, opaque, 0xFFFFFFFFu);
    }
    draw();
    context->PSSetShader(previous, nullptr, 0);
    context->PSSetSamplers(0, kSamplerSlots, previousSamplers);
    context->PSSetShaderResources(0, 1, &previousView);
    context->OMSetBlendState(previousBlend, blendFactor, sampleMask);
    release_com(previous);
    for (ID3D11SamplerState* sampler : previousSamplers) {
        release_com(sampler);
    }
    release_com(previousView);
    release_com(previousBlend);
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
        next(context, indexCount, startIndex, baseVertex);
        return;
    }
    note_first(*part, startIndex, indexCount);
    draw_replaced(context, *part, [&]() noexcept {
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
        next(context, indexCountPerInstance, instanceCount, startIndex, baseVertex, startInstance);
        return;
    }
    note_first(*part, startIndex, indexCountPerInstance);
    draw_replaced(context, *part, [&]() noexcept {
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
                     "ev=custom_albedo stage=attach result=ok mode=parts");
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
    AcquireSRWLockExclusive(&g_psLock);
    g_pixelShaders.clear();
    ReleaseSRWLockExclusive(&g_psLock);
    g_loggedParts = 0;
    g_loggedTargets = false;
    g_loggedDepthOnly = false;
    g_dumpedPs = false;
}

} // namespace sunrise::client::hooks::custom_albedo
