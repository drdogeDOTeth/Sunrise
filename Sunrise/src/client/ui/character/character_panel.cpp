/**
 * The character switcher's interface.
 *
 * A custom character is three separable things, and only two of them live here. Its **paint** is
 * a set of PNGs, and its **part table** maps exact `(StartIndex, IndexCount)` draw ranges onto
 * those PNGs or onto `hide`. Both are plain files, so swapping them is immediate and reversible.
 * Its **geometry** is injected into Tiger package layers, which the game reads at load and which
 * cannot be written while it runs - so a silhouette change still needs `tools/pkg` with Destiny
 * closed. This page is honest about that split rather than pretending a restart is not coming.
 */

#include "character_panel.h"

#include <array>
#include <cstdio>
#include <imgui.h>
#include <string_view>

#include "../../hooks/custom_albedo/custom_albedo.h"

namespace sunrise::client::ui::character {
namespace {

namespace albedo = client::hooks::custom_albedo;

/** Longest profile label drawn, plus the null. */
constexpr std::size_t kLabelCapacity = 64;

/** Result of the last selection, so a failure is reported rather than silently ignored. */
enum class LastAction {
    none,
    applied,
    failed,
    rescanned,
};

LastAction g_last{LastAction::none};
/** Name the last action referred to, held because the profile listing can be rescanned. */
std::array<char, kLabelCapacity> g_lastName{};

/** Copies one profile name into the held label. */
void hold_name(std::string_view name) noexcept {
    const int written = std::snprintf(g_lastName.data(),
                                      g_lastName.size(),
                                      "%.*s",
                                      static_cast<int>(name.size()),
                                      name.data());
    if (written <= 0) {
        g_lastName[0] = '\0';
    }
}

/** Draws the row that selects one profile. @param index Profile slot, or the defaults sentinel. */
void draw_profile_row(std::size_t index, std::string_view name, bool active) noexcept {
    const std::string_view display = name.empty() ? std::string_view("(shipped defaults)") : name;
    std::array<char, kLabelCapacity> label{};
    (void)std::snprintf(label.data(),
                        label.size(),
                        "%.*s##profile%zu",
                        static_cast<int>(display.size()),
                        display.data(),
                        index);
    if (ImGui::RadioButton(label.data(), active) && !active) {
        hold_name(display);
        g_last = albedo::select_profile(index) ? LastAction::applied : LastAction::failed;
    }
}

/** Draws the part table the active profile produced. */
void draw_parts() noexcept {
    const std::size_t parts = albedo::part_count();
    if (parts == 0) {
        ImGui::TextDisabled("No parts are loaded, so nothing is painted or hidden.");
        return;
    }
    if (!ImGui::CollapsingHeader("Part table")) {
        return;
    }
    ImGui::TextDisabled("Ranges are exact (StartIndex, IndexCount) pairs into the injected mesh. "
                        "A hidden range is skipped at draw, which is how stock geometry is removed "
                        "without touching a package.");
    for (std::size_t index = 0; index < parts; ++index) {
        const albedo::PartView part = albedo::part_at(index);
        ImGui::TextDisabled("  %-10.*s  start %-7u  count %-7u  %s",
                            static_cast<int>(part.name.size()),
                            part.name.data(),
                            part.start,
                            part.count,
                            part.hidden ? "hidden" : "painted");
    }
}

} // namespace

void draw() noexcept {
    const std::size_t count = albedo::profile_count();
    const std::size_t active = albedo::active_profile();

    ImGui::TextDisabled("Characters live in bin\\x64\\Sunrise\\characters\\<name>\\, each holding a "
                        "parts.txt and the textures it names. The choice is remembered in "
                        "characters\\active.txt.");

    if (count == 0) {
        ImGui::TextDisabled("No character profiles found. The shipped six-part defaults are drawn.");
    } else {
        for (std::size_t index = 0; index < count; ++index) {
            draw_profile_row(index, albedo::profile_name(index), index == active);
        }
    }
    // The defaults are always offered, so a profile that draws badly is one click from a known
    // good character rather than a file edit and a relaunch.
    draw_profile_row(count, {}, active >= count);

    if (ImGui::Button("Rescan characters")) {
        albedo::rescan_profiles();
        g_last = LastAction::rescanned;
        g_lastName[0] = '\0';
    }
    ImGui::SameLine();
    ImGui::TextDisabled("Picks up a profile directory added while the game is running.");

    switch (g_last) {
    case LastAction::applied:
        ImGui::TextDisabled("Applied %s.", g_lastName.data());
        break;
    case LastAction::failed:
        ImGui::TextDisabled("%s could not be applied; its textures did not load, so the shipped "
                            "defaults were put back. Check the log for stage=profile.",
                            g_lastName.data());
        break;
    case LastAction::rescanned:
        ImGui::TextDisabled("Rescanned: %zu profile(s).", albedo::profile_count());
        break;
    case LastAction::none:
        break;
    }

    ImGui::Separator();
    ImGui::TextDisabled("Switching swaps paint and visibility only. Geometry is injected into "
                        "package layers, which the game reads at load and which cannot be written "
                        "while it is running - a different body still needs tools\\pkg and a "
                        "relaunch.");
    draw_parts();
}

} // namespace sunrise::client::ui::character
