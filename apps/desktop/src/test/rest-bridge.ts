import { vi } from 'vitest'

import type { NovaApiRequest } from '@/global'

/** Install a fake `window.hermesDesktop.api`, the preload bridge every REST helper sends through. */
export function installRestBridge(answer: (request: NovaApiRequest) => object) {
  const api = vi.fn(async (request: NovaApiRequest) => answer(request))

  Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: { api } })

  return api
}
