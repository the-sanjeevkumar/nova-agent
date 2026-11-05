import { describe, expect, it } from 'vitest'

import {
  normalizeNovaOpenString,
  pathFromNovaDeepLink,
  pathFromOpenDeepLink,
  resolveNovaOpenPath
} from './hermes-open-target'

describe('normalizeNovaOpenString', () => {
  it('accepts hash-router paths and strips a leading hash', () => {
    expect(normalizeNovaOpenString('/index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeNovaOpenString('#/index-network/intent/1')).toBe('/index-network/intent/1')
  })

  it('maps plugin-scoped nova:// deep links to the same path', () => {
    expect(normalizeNovaOpenString('nova://index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeNovaOpenString('nova://index-network/intent/1?focus=true')).toBe(
      '/index-network/intent/1?focus=true'
    )
  })

  it('maps nova://open/… deep links by stripping the open host', () => {
    expect(normalizeNovaOpenString('nova://open/index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeNovaOpenString('nova://open/settings/plugins')).toBe('/settings/plugins')
  })

  it('rejects reserved nova kinds and unsafe paths', () => {
    expect(normalizeNovaOpenString('nova://blueprint/morning-brief')).toBeNull()
    expect(normalizeNovaOpenString('nova://plugin/install')).toBeNull()
    expect(normalizeNovaOpenString('https://example.com/x')).toBeNull()
    expect(normalizeNovaOpenString('/../etc/passwd')).toBeNull()
    expect(normalizeNovaOpenString('index-network')).toBeNull()
  })
})

describe('resolveNovaOpenPath', () => {
  it('merges structured path + params', () => {
    expect(resolveNovaOpenPath({ path: '/index-network/intent/1', params: { focus: 'true' } })).toBe(
      '/index-network/intent/1?focus=true'
    )
  })

  it('resolves href the same as a bare string', () => {
    expect(resolveNovaOpenPath({ href: 'nova://index-network/intent/1' })).toBe('/index-network/intent/1')
  })
})

describe('pathFromNovaDeepLink', () => {
  it('builds the navigate path from a plugin-scoped deep-link payload', () => {
    expect(pathFromNovaDeepLink('index-network', 'intent/1')).toBe('/index-network/intent/1')
  })

  it('builds the navigate path from nova://open/… payloads', () => {
    expect(pathFromOpenDeepLink('index-network/intent/1')).toBe('/index-network/intent/1')
    expect(pathFromNovaDeepLink('open', 'agent/42')).toBe('/agent/42')
  })

  it('ignores reserved kinds', () => {
    expect(pathFromNovaDeepLink('blueprint', 'morning-brief')).toBeNull()
    expect(pathFromNovaDeepLink('plugin', 'install')).toBeNull()
  })
})
