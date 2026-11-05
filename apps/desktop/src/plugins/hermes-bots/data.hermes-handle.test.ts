/**
 * @nova belongs to the default bot only while its source has no real
 * (pre-reservation) `nova` profile. With one, the roster shows exactly one
 * @nova — the real profile — and the default answers to @default, so a
 * typed @nova reaches the same profile the backend's message_agent does.
 */

import { describe, expect, it, vi } from 'vitest'

import { botHandle, botMentionTag, claimDefaultHandles, mergeMultiSourceRoster, resolveRosterMentions } from './data'
import { previewKind } from './row-helpers'
import type { RosterRow } from './types'

vi.mock('@nova/plugin-sdk', async () => {
  const { atom } = await import('nanostores')

  return { atom, host: {}, queryClient: undefined, useQuery: vi.fn(), useValue: vi.fn() }
})

vi.mock('./shared', () => ({ getPluginCtx: () => null, ID: 'hermes-bots' }))

function roster(names: string[]): RosterRow[] {
  const local = { profiles: names.map(name => ({ name })) }

  // What Electron's buildAgentRoster reports for a single local source: bare-name handles.
  const union = {
    agents: names.map(profile => ({
      connectionId: 'local',
      connectionKind: 'local' as const,
      connectionLabel: 'This device',
      handle: profile,
      profile,
      targetProfile: profile
    })),
    primaryConnectionId: 'local'
  }

  return mergeMultiSourceRoster(local, union, 'local').profiles as RosterRow[]
}

describe('the @nova handle', () => {
  it.each([
    [['default', 'researcher'], 'default'],
    [['default', 'nova', 'researcher'], 'nova']
  ])('roster %j shows one @nova and a typed @nova reaches %s', (names, owner) => {
    const rows = roster(names)
    const handles = rows.map(row => botHandle(row.name, row))

    expect(handles.filter(handle => handle === 'nova')).toHaveLength(1)
    expect(rows.map(row => botMentionTag(row)).filter(tag => tag === 'nova')).toHaveLength(1)
    expect(resolveRosterMentions('@nova ping', rows, { name: 'researcher' }).map(row => row.name)).toEqual([owner])
  })

  it('names the default @default once a real nova profile holds the handle', () => {
    const rows = roster(['default', 'nova', 'researcher'])
    const byName = Object.fromEntries(rows.map(row => [row.name, botHandle(row.name, row)]))

    expect(byName).toEqual({ default: 'default', nova: 'nova', researcher: 'researcher' })
    expect(resolveRosterMentions('@default ping', rows, { name: 'researcher' }).map(row => row.name)).toEqual(['default'])
  })

  it('keeps each source separate: a remote nova profile does not rename the local default', () => {
    const rows = claimDefaultHandles<Partial<RosterRow>>([
      { connectionId: 'local', name: 'default' },
      { connectionId: 'mini', name: 'default' },
      { connectionId: 'mini', name: 'nova' }
    ])

    expect(rows.map(row => botHandle(String(row.name), row))).toEqual(['nova', 'default', 'nova'])
  })

  it("never lets a source's stale 'nova' handle on its default answer @nova", () => {
    // An older backend labels its default @nova even beside a real nova profile.
    const rows = claimDefaultHandles<RosterRow>([
      { connectionId: 'mini', handle: 'nova', name: 'default', remoteSource: true },
      { connectionId: 'mini', handle: 'nova', name: 'nova', remoteSource: true },
      { connectionId: 'local', name: 'researcher' }
    ])

    expect(resolveRosterMentions('@nova ping', rows, { name: 'researcher' }).map(row => row.name)).toEqual(['nova'])
  })

  it('names a DM sender in a row preview by its roster handle', () => {
    const rows = claimDefaultHandles<RosterRow>(
      ['default', 'nova', 'researcher'].map(name => ({ connectionId: 'local', name }))
    )

    const sender = (name: string, list: RosterRow[]) =>
      previewKind(`Message from 🤖 ${name} (@x): hi`, list, 'local').fromBot

    expect([sender('default', rows), sender('nova', rows)]).toEqual(['default', 'nova'])
    // No real nova profile: the default is still @nova (#89484).
    expect(sender('default', claimDefaultHandles<RosterRow>([{ connectionId: 'local', name: 'default' }]))).toBe(
      'nova'
    )
  })
})
