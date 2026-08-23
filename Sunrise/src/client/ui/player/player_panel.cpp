/** The player module's interface. Every control saves at once, so a change survives a restart. */

#include "player_panel.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>
#include <imgui.h>

#include "../../../core/ui/components/toggle/ui_toggle_component.h"
#include "../../hooks/custom_albedo/custom_albedo.h"
#include "../../hooks/inactivity/inactivity_override.h"
#include "../../inactivity/inactivity_settings_store.h"
#include "../../player/player_settings_store.h"

namespace sunrise::client::ui::player {
namespace {

namespace inactivity = client::inactivity;
namespace toggle = core::ui::components::toggle;
namespace custom_albedo = client::hooks::custom_albedo;

/** Grid width. Seven columns lays the fourteen lanes out in two rows. */
constexpr int kLaneColumns = 7;

/**
 * Draws one lane's field and, under it, the milliseconds the Client is holding in that lane now.
 * @param index Lane in block order.
 * @param configured Configuration updated on an edit.
 * @param status What the override reached, for the live figure.
 * @return True when this lane changed.
 */
[[nodiscard]] bool draw_lane(std::size_t index,
                             inactivity::Settings& configured,
                             const hooks::inactivity::Status& status) noexcept {
    const bool orbit = index == inactivity::kOrbitLane;
    bool changed = false;
    ImGui::PushID(static_cast<int>(index));
    ImGui::BeginDisabled(orbit);
    ImGui::TextUnformatted(inactivity::kActivities[index].column.data());
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled)) {
        ImGui::SetTooltip("%s", inactivity::kActivities[index].name.data());
    }
    // The live figure is the one the Client is timing by, so it reads as active and the set value
    // is dimmed. A lane held at its longest is timing nothing, so neither is active.
    const std::uint32_t live = status.liveValid ? status.live[index] : 0;
    const bool liveActive = status.liveValid && live != inactivity::kMaximumTimeoutMs;

    ImGui::SetNextItemWidth(-FLT_MIN);
    std::uint32_t milliseconds = configured.timeouts[index];
    if (liveActive) {
        ImGui::PushStyleColor(ImGuiCol_Text, ImGui::GetStyle().Colors[ImGuiCol_TextDisabled]);
    }
    ImGui::InputScalar("##lane",
                       ImGuiDataType_U32,
                       &milliseconds,
                       nullptr,
                       nullptr,
                       "%u",
                       ImGuiInputTextFlags_CharsDecimal);
    if (liveActive) {
        ImGui::PopStyleColor();
    }
    if (ImGui::IsItemDeactivatedAfterEdit()) {
        configured.timeouts[index] =
            std::clamp(milliseconds, inactivity::kMinimumTimeoutMs, inactivity::kMaximumTimeoutMs);
        changed = true;
    }
    ImGui::EndDisabled();
    // Outside the disabled block: the live value reads the same whether or not the field can be
    // edited.
    if (!status.liveValid) {
        ImGui::TextDisabled("-");
    } else if (liveActive) {
        ImGui::Text("%u", live);
    } else {
        ImGui::TextDisabled("%u", live);
    }
    ImGui::PopID();
    return changed;
}

/** @param status What the override reached, for the live grace. */
void draw_inactivity_clocks(const hooks::inactivity::Status& status) noexcept {
    // Read from the draw rather than the hold, so the Client is only asked while this section is
    // on screen to answer to.
    const hooks::inactivity::Timers timers = hooks::inactivity::timers();
    if (timers.idleValid) {
        ImGui::Text("Idle %.1f s", static_cast<double>(timers.idleMs) / 1000.0);
    } else {
        ImGui::TextDisabled("Idle -");
    }
    ImGui::SameLine();
    if (timers.sessionValid) {
        ImGui::Text("Session %.1f s", static_cast<double>(timers.sessionMs) / 1000.0);
    } else {
        ImGui::TextDisabled("Session -");
    }
    if (!status.liveGraceValid) {
        return;
    }
    const bool passed =
        status.liveGraceMs == 0 || (timers.sessionValid && timers.sessionMs > status.liveGraceMs);
    ImGui::SameLine();
    // Reported, never written.
    const double grace = static_cast<double>(status.liveGraceMs) / 1000.0;
    if (passed) {
        ImGui::TextDisabled("Grace %.1f s (passed)", grace);
    } else {
        ImGui::Text("Grace %.1f s (no kick until then)", grace);
    }
}

/** Draws the inactivity section: the main switch, the lane grid and the Client's clocks. */
void draw_inactivity() noexcept {
    inactivity::Settings configured = inactivity::get();
    // Taken once, so every line below and the grid all describe the same poll.
    const hooks::inactivity::Status status = hooks::inactivity::status();

    ImGui::TextUnformatted("Inactivity");
    ImGui::Separator();
    ImGui::TextWrapped("Disable AFK timeouts from activities kicking to orbit and the title "
                       "screen.");
    ImGui::Spacing();

    // The two switches are exclusive, so turning this one on drops the set timeouts.
    bool changed = toggle::control("Enabled##inactivity", configured.enabled);
    if (changed && configured.enabled) {
        configured.custom = false;
    }

    if (ImGui::CollapsingHeader("Advanced##inactivity")) {
        // A per-lane timeout has nothing to act on once every lane is already removed.
        ImGui::BeginDisabled(configured.enabled);
        if (toggle::control("Use set timeouts##inactivity_custom", configured.custom)) {
            if (configured.custom) {
                configured.enabled = false;
            }
            changed = true;
        }
        ImGui::EndDisabled();
        ImGui::TextDisabled("In milliseconds");
        ImGui::Spacing();
        ImGui::BeginDisabled(configured.enabled || !configured.custom);
        if (ImGui::BeginTable("lanes", kLaneColumns, ImGuiTableFlags_SizingStretchSame)) {
            for (std::size_t index = 0; index < inactivity::kActivityCount; ++index) {
                ImGui::TableNextColumn();
                changed = draw_lane(index, configured, status) || changed;
            }
            ImGui::EndTable();
        }
        ImGui::EndDisabled();
        ImGui::Spacing();
        draw_inactivity_clocks(status);
    }

    if (changed) {
        (void)inactivity::publish(configured);
    }
}

/** Draws the custom character model and VRM switcher section. */
void draw_custom_character() noexcept {
    ImGui::TextUnformatted("Custom Character (VRM / GLB)");
    ImGui::Separator();
    ImGui::TextWrapped(
        "This list is draw-hook profiles: albedos and index ranges. The body in the "
        "world is whatever was last injected onto the Scatterhorn chest. Close Destiny "
        "and run vrm_injector.py --inject to install a VRM as the full Warlock, not as "
        "a helmet.");
    ImGui::Spacing();

    bool enabled = custom_albedo::is_enabled();
    if (toggle::control("Enable Custom Model##custom_char_toggle", enabled)) {
        custom_albedo::set_enabled(enabled);
    }

    ImGui::Spacing();

    const std::vector<custom_albedo::ModelProfile> models = custom_albedo::get_available_models();
    const std::string activeId = custom_albedo::get_active_model_id();

    int currentIndex = 0;
    for (std::size_t i = 0; i < models.size(); ++i) {
        if (models[i].id == activeId) {
            currentIndex = static_cast<int>(i);
            break;
        }
    }

    const char* currentLabel = models.empty() ? "None Available" : models[currentIndex].name.c_str();

    ImGui::TextUnformatted("Active Model:");
    ImGui::SetNextItemWidth(260.0f);
    if (ImGui::BeginCombo("##active_model_combo", currentLabel)) {
        for (std::size_t i = 0; i < models.size(); ++i) {
            const bool isSelected = (static_cast<int>(i) == currentIndex);
            if (ImGui::Selectable(models[i].name.c_str(), isSelected)) {
                custom_albedo::select_model(models[i].id);
            }
            if (isSelected) {
                ImGui::SetItemDefaultFocus();
            }
        }
        ImGui::EndCombo();
    }

    ImGui::SameLine();
    if (ImGui::Button("Refresh##btn_refresh_models")) {
        custom_albedo::refresh_models();
    }

    ImGui::SameLine();
    if (ImGui::Button("Open Folder##btn_open_models_dir")) {
        custom_albedo::open_models_folder();
    }

    if (!models.empty() && static_cast<std::size_t>(currentIndex) < models.size()) {
        const auto& active = models[currentIndex];
        ImGui::Spacing();
        ImGui::TextDisabled("Model ID: %s", active.id.c_str());
        if (!active.author.empty() && active.author != "Unknown") {
            ImGui::TextDisabled("Author: %s", active.author.c_str());
        }
        ImGui::TextDisabled("Parts: %u  |  Format: %s  |  Vertices: %u",
                            active.partCount,
                            active.version.empty() ? "GLB" : active.version.c_str(),
                            active.vertexCount);
        if (!active.folderPath.empty()) {
            ImGui::TextDisabled("Path: %s", active.folderPath.c_str());
        }
    }

    const auto searchPaths = custom_albedo::get_model_search_paths();
    if (!searchPaths.empty() && ImGui::CollapsingHeader("Model Search Paths")) {
        for (const auto& sp : searchPaths) {
            ImGui::BulletText("%s", sp.c_str());
        }
    }
}

} // namespace

/** Draws the player module inside the active Core UI frame. */
void draw() noexcept {
    draw_custom_character();

    ImGui::Spacing();
    ImGui::Spacing();

    client::player::Settings settings = client::player::get();

    ImGui::TextUnformatted("Infinite Ammo");
    ImGui::Separator();
    ImGui::TextWrapped("Keep every weapon's reserves full.");
    ImGui::Spacing();

    const bool changed = core::ui::components::toggle::control("Enabled##infinite_ammo",
                                                               settings.infiniteAmmoEnabled);
    if (changed) {
        (void)client::player::publish(settings);
    }

    ImGui::Spacing();
    ImGui::Spacing();
    draw_inactivity();
}

} // namespace sunrise::client::ui::player
