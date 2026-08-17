#pragma once

namespace sunrise::state::cosmetics {

/**
 * Authored relaxations of the socket-plug rules, for appearance work on a local account.
 *
 * Every field is off by default, so an unconfigured build judges a plug exactly as the game does.
 * Each one drops a separate check, because they fail for different reasons and a caller usually
 * wants only one of them: a pool refusal is about where a plug may go, an ownership refusal is
 * about whether the account holds it, and a cost refusal is about what applying it charges.
 */
struct Settings {
    /**
     * Accepts any installed plug in any ordinary socket lane, ignoring the authored pool.
     * This is what puts a Titan ornament on a Warlock, or a weapon ornament on the wrong frame.
     * The plug still has to be a real installed definition and the lane a real socket.
     */
    bool unrestrictedPlugs{false};
    /**
     * Applies a plug without holding the stack it is drawn from, so an unowned shader still goes
     * on. Ornaments never consumed a stack, so this only ever changes shader behaviour.
     */
    bool ignorePlugOwnership{false};
    /** Applies a plug without paying its authored insertion cost. */
    bool ignoreInsertionCost{false};
    /**
     * Lets an ornament's art arrangement replace the base item's instead of joining the overlays.
     *
     * The render path treats the two halves of a plug's art asymmetrically: a declared gear art
     * index replaces the base item's outright, while a declared arrangement is appended to the
     * overlay list and the base arrangement stays in its own slot. That asymmetry is a candidate
     * cause of ornaments reading as equipped while the base model still renders, which is the
     * reported behaviour.
     *
     * Off by default because it is a hypothesis about what the client wants, not a confirmed fix.
     * Turning it on is the A/B test: equip an ornament that should visibly change a model, launch
     * once with this clear and once with it set, and compare.
     */
    bool ornamentReplacesArrangement{false};
};

} // namespace sunrise::state::cosmetics
