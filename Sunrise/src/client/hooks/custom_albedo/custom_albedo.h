#pragma once

#include <d3d11.h>

namespace sunrise::client::hooks::custom_albedo {

/**
 * Compiles the RGB albedo pixel shader and attaches DrawIndexed hooks on this context.
 * The dye PS luma-gates unique RGB. Replaces that shader on the exact
 * index ranges in custom_parts.txt (or the shipped six-part defaults)
 * when the select G-buffer (29/24/28) is bound, then restores the previous
 * PS including class instances. v18 uses GLB roughness on t1 and open AO.
 * Do not sample Destiny t2. v16 TEXCOORD2 is closed. Do not skip the
 * gauntlet-hand draw — first-person needs those hands visible.
 * Dumps the live game PS once.
 * @param device Device that owns the compiled shader.
 * @param context Immediate context whose DrawIndexed vtable is hooked.
 */
void attach(ID3D11Device* device, ID3D11DeviceContext* context) noexcept;

/** Detaches the draw hooks and releases the compiled shader. Idempotent. */
void detach() noexcept;

} // namespace sunrise::client::hooks::custom_albedo
