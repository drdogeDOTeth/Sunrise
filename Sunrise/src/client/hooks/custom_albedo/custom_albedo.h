#pragma once

#include <d3d11.h>
#include <string>
#include <string_view>
#include <vector>

namespace sunrise::client::hooks::custom_albedo {

/** Metadata for one discoverable custom character model profile. */
struct ModelProfile {
    std::string id{};
    std::string name{};
    std::string author{};
    std::string version{};
    std::string folderPath{};
    UINT vertexCount{};
    UINT partCount{};
    bool active{};
    bool isDefault{};
};

/**
 * Compiles the RGB albedo pixel shader and attaches DrawIndexed hooks on this context.
 * The dye PS luma-gates unique RGB. Replaces that shader on exact index ranges when
 * the select G-buffer (29/24/28) is bound, then restores the previous PS including
 * class instances. Supports dynamic model profiles and VRM conversions.
 * @param device Device that owns the compiled shader.
 * @param context Immediate context whose DrawIndexed vtable is hooked.
 */
void attach(ID3D11Device* device, ID3D11DeviceContext* context) noexcept;

/** Detaches the draw hooks and releases the compiled shader. Idempotent. */
void detach() noexcept;

/** @return True when hooks and shader are actively installed. */
[[nodiscard]] bool is_attached() noexcept;

/** @return True when custom character rendering is enabled. */
[[nodiscard]] bool is_enabled() noexcept;

/** Enables or disables the custom character model draw override. */
void set_enabled(bool enabled) noexcept;

/** @return List of all discovered model profiles (default + models folder). */
[[nodiscard]] std::vector<ModelProfile> get_available_models() noexcept;

/** @return Stable ID of the active model profile. */
[[nodiscard]] std::string get_active_model_id() noexcept;

/** Switches the active character model profile by ID. */
bool select_model(std::string_view modelId) noexcept;

/** Re-scans all candidate models folders for new/updated VRM profiles. */
void refresh_models() noexcept;

/** @return List of all filesystem paths being searched for VRM model profiles. */
[[nodiscard]] std::vector<std::string> get_model_search_paths() noexcept;

/** Opens the primary models folder in Windows Explorer. */
void open_models_folder() noexcept;

} // namespace sunrise::client::hooks::custom_albedo
