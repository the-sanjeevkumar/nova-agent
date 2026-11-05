import type { Usage } from '@nova/shared/gateway-events'

export { usageFromTick } from '@nova/shared/usage-tick'

export const ZERO: Usage = { calls: 0, input: 0, output: 0, total: 0 }
