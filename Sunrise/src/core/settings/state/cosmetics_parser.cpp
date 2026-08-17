#include "../parser.h"

namespace sunrise::core::settings::parser {

/** Parses the optional socket-plug relaxations over the strict defaults. */
bool Parser::cosmetics(state::cosmetics::Settings& output) noexcept {
    if (!consume('{')) {
        return false;
    }
    state::cosmetics::Settings candidate = output;
    bool hasUnrestrictedPlugs = false;
    bool hasIgnorePlugOwnership = false;
    bool hasIgnoreInsertionCost = false;
    bool hasOrnamentReplacesArrangement = false;
    if (consume('}')) {
        return true;
    }
    for (;;) {
        std::string_view key;
        if (!string(key) || !consume(':')) {
            return false;
        }
        if (key == "unrestricted_plugs") {
            if (hasUnrestrictedPlugs || !boolean(candidate.unrestrictedPlugs)) {
                return false;
            }
            hasUnrestrictedPlugs = true;
        } else if (key == "ignore_plug_ownership") {
            if (hasIgnorePlugOwnership || !boolean(candidate.ignorePlugOwnership)) {
                return false;
            }
            hasIgnorePlugOwnership = true;
        } else if (key == "ignore_insertion_cost") {
            if (hasIgnoreInsertionCost || !boolean(candidate.ignoreInsertionCost)) {
                return false;
            }
            hasIgnoreInsertionCost = true;
        } else if (key == "ornament_replaces_arrangement") {
            if (hasOrnamentReplacesArrangement
                || !boolean(candidate.ornamentReplacesArrangement)) {
                return false;
            }
            hasOrnamentReplacesArrangement = true;
        } else if (!skip_value(0)) {
            return false;
        }
        if (consume('}')) {
            output = candidate;
            return true;
        }
        if (!consume(',')) {
            return false;
        }
    }
}

} // namespace sunrise::core::settings::parser
