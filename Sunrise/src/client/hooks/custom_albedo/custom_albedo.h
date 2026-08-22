#pragma once

#include <d3d11.h>

namespace sunrise::client::hooks::custom_albedo {

/**
 * Compiles the RGB albedo pixel shader and attaches DrawIndexed hooks on this context.
 * The dye PS on the custom chest (0x81531EE6) luma-gates unique RGB. This
 * replaces that shader for the five known index ranges only. Writes the
 * dumped G-buffer encode and binds the matching GLB albedo per part
 * (TEXCOORD3, TBN on o1, TEXCOORD0.w on o2). Dumps the live game PS once.
 * @param device Device that owns the compiled shader.
 * @param context Immediate context whose DrawIndexed vtable is hooked.
 */
void attach(ID3D11Device* device, ID3D11DeviceContext* context) noexcept;

/** Detaches the draw hooks and releases the compiled shader. Idempotent. */
void detach() noexcept;

} // namespace sunrise::client::hooks::custom_albedo
