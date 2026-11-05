import { resolveGatewayWsUrl } from '@nova/shared'

import type { NovaConnection } from '@/global'

export function resolveDesktopGatewayWsUrl(
  desktop: Window['hermesDesktop'],
  connection: NovaConnection
): Promise<string> {
  // Only a registry-scoped descriptor may use the *For bridge (see
  // NovaConnection.registryScoped); an absent bridge fails closed rather
  // than minting the peer's URL against the local pool.
  const { connectionId, profile, registryScoped } = connection

  if (!registryScoped || !connectionId) {
    return resolveGatewayWsUrl(desktop, connection)
  }

  const mint = desktop.getGatewayWsUrlFor

  return resolveGatewayWsUrl({ getGatewayWsUrl: mint ? () => mint({ connectionId, profile }) : undefined }, connection)
}
