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
};

} // namespace sunrise::state::cosmetics
