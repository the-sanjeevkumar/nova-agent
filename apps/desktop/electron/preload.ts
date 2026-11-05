import { contextBridge, ipcRenderer, webFrame, webUtils } from 'electron'

import type { DesktopProfileRoute } from './desktop-profile'
import type { HudModifierApi, HudModifierStatus } from './hud-modifier-types'
import { customWindowControlsEnabled } from './window-controls'

// Which translucency the OS can back. Asked synchronously because the renderer
// needs it before its first paint, and answered by main because deciding it
// needs `os.release()` — a sandboxed preload may only require electron, events,
// timers and url, so importing node:os here throws before contextBridge runs
// and takes the ENTIRE bridge down with it (window.hermesDesktop undefined =>
// "Desktop IPC bridge is unavailable"). No reply means no glass, which degrades
// to an ordinary opaque window rather than a page thinned over nothing.
const translucencySupport = ipcRenderer.sendSync('nova:translucency:support')
const hudWindowing = ipcRenderer.sendSync('nova:hud:windowing')
const hudNativeDrag = hudWindowing?.nativeDrag === true

const launchFlags: { localModels?: boolean; assumeFreshInstall?: boolean } | undefined =
  ipcRenderer.sendSync('nova:feature-flags')

// Local, sanitized skin payload for the first renderer theme paint. This does
// not wait on `gateway.ready`, so an unreachable remote primary cannot force
// the built-in palette over the skin configured on this machine.
const localSkin = ipcRenderer.sendSync('nova:skin:local')

// This machine's UI preferences store (#145213), read before the first paint so
// a preference set in another window or in this machine's Webapp paints at once.
const uiPreferencesSnapshot = ipcRenderer.sendSync('nova:ui-preferences:snapshot')

import { unwrapExpectedNotFound } from './api-expected-404'

contextBridge.exposeInMainWorld('hermesDesktop', {
  glassSupported: translucencySupport?.glass === true,
  translucencySupported: translucencySupport?.translucency === true,
  // Launch-flag fact: the app was started with --local, so the renderer may
  // show the local-models surfaces. Static for the window's lifetime.
  localModelsEnabled: launchFlags?.localModels === true,
  // Dev-only launch fact: an unpackaged app started with --fresh-install, so
  // onboarding treats this launch as a fresh install. Always false when packaged.
  assumeFreshInstall: launchFlags?.assumeFreshInstall === true,
  localSkin: localSkin && typeof localSkin === 'object' ? localSkin : null,
  uiPreferences: {
    snapshot: uiPreferencesSnapshot && typeof uiPreferencesSnapshot === 'object' ? uiPreferencesSnapshot : null,
    read: () => ipcRenderer.invoke('nova:ui-preferences:read'),
    write: payload => ipcRenderer.invoke('nova:ui-preferences:write', payload),
    onChanged: callback => {
      const listener = (_event, snapshot) => callback(snapshot)
      ipcRenderer.on('nova:ui-preferences:changed', listener)

      return () => ipcRenderer.removeListener('nova:ui-preferences:changed', listener)
    }
  },
  getConnection: (profile, opts) => ipcRenderer.invoke('nova:connection', profile, opts),
  // Loopback origin that hosts YouTube's player for the file:// renderer.
  getEmbedHostOrigin: () => ipcRenderer.invoke('nova:embed-host:origin'),
  // Registry-scoped backend resolution: { connectionId, profile } → descriptor.
  getConnectionFor: payload => ipcRenderer.invoke('nova:connection:for', payload),
  getProfileRoutes: profiles => ipcRenderer.invoke('nova:plugin-profile-routes', profiles),
  revalidateConnection: () => ipcRenderer.invoke('nova:connection:revalidate'),
  getGatewayWsUrl: profile => ipcRenderer.invoke('nova:gateway:ws-url', profile),
  // Registry-scoped fresh WS URL: { connectionId, profile } → result shape of
  // getGatewayWsUrl, minted against that connection's backend.
  getGatewayWsUrlFor: payload => ipcRenderer.invoke('nova:gateway:ws-url-for', payload),
  // { connectionId, profile, pluginId, path } → one-use local plugin socket URL, null, or { refused: 'permission' }.
  getPluginWsUrl: payload => ipcRenderer.invoke('nova:plugin:ws-url', payload),
  // Union agent roster across every registered connection.
  getAgentRoster: () => ipcRenderer.invoke('nova:agents:roster'),
  openSessionWindow: (sessionId, opts) => ipcRenderer.invoke('nova:window:openSession', sessionId, opts),
  openSessionInTerminal: (sessionId, opts) => ipcRenderer.invoke('nova:window:openInTerminal', sessionId, opts),
  openWindow: (options?: DesktopProfileRoute) => ipcRenderer.invoke('nova:window:openInstance', options),
  openBrowserWindow: tabId => ipcRenderer.invoke('nova:window:openBrowser', tabId),
  windowRelay: {
    send: payload => ipcRenderer.send('nova:window:relay', payload),
    onMessage: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('nova:window:relay', listener)

      return () => ipcRenderer.removeListener('nova:window:relay', listener)
    }
  },
  onBrowserPopoutClosed: callback => {
    const listener = (_event, tabId) => callback(tabId)
    ipcRenderer.on('nova:browser-popout:closed', listener)

    return () => ipcRenderer.removeListener('nova:browser-popout:closed', listener)
  },
  claimAmbientCue: key => ipcRenderer.invoke('nova:ambient:claim', key),
  windowControls: {
    custom: customWindowControlsEnabled(),
    minimize: () => ipcRenderer.send('nova:window-control', 'minimize'),
    toggleMaximize: () => ipcRenderer.send('nova:window-control', 'toggle-maximize'),
    close: () => ipcRenderer.send('nova:window-control', 'close')
  },
  wakeIndicator: {
    getState: () => ipcRenderer.invoke('nova:wake-indicator:get'),
    setState: state => ipcRenderer.send('nova:wake-indicator:set', state),
    onState: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('nova:wake-indicator:state', listener)

      return () => ipcRenderer.removeListener('nova:wake-indicator:state', listener)
    }
  },
  chatOnboarding: {
    size: mode => ipcRenderer.send('nova:window:size', mode)
  },
  petOverlay: {
    // Main renderer → main process: window lifecycle + drag. `request` is
    // `{ bounds, screen }`; resolves with the screen bounds it actually used.
    open: request => ipcRenderer.invoke('nova:pet-overlay:open', request),
    close: () => ipcRenderer.invoke('nova:pet-overlay:close'),
    setBounds: bounds => ipcRenderer.send('nova:pet-overlay:set-bounds', bounds),
    setIgnoreMouse: ignore => ipcRenderer.send('nova:pet-overlay:ignore-mouse', ignore),
    // Flip the overlay focusable (and focus it) while the composer needs keys.
    setFocusable: focusable => ipcRenderer.send('nova:pet-overlay:set-focusable', focusable),
    // Main renderer → overlay (forwarded by main): push the latest pet state.
    pushState: payload => ipcRenderer.send('nova:pet-overlay:state', payload),
    // Overlay → main renderer (forwarded by main): pop back in / composer submit.
    control: payload => ipcRenderer.send('nova:pet-overlay:control', payload),
    // Overlay subscribes to state pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('nova:pet-overlay:state', listener)

      return () => ipcRenderer.removeListener('nova:pet-overlay:state', listener)
    },
    // Main renderer subscribes to overlay control messages.
    onControl: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('nova:pet-overlay:control', listener)

      return () => ipcRenderer.removeListener('nova:pet-overlay:control', listener)
    }
  },
  // HUD mode: the chrome-free floating chat. A full app renderer (own gateway)
  // sized as a floating bar, so it mounts the real composer. Main owns the
  // window; `onChanged` keeps every window's toggle truthful.
  hud: {
    nativeDrag: hudNativeDrag,
    windowing: {
      clientPlacement: hudWindowing?.clientPlacement !== false,
      controlDrag: hudWindowing?.controlDrag === true,
      nativeDrag: hudNativeDrag,
      solid: hudWindowing?.solid === true,
      workspaceTransfer: hudWindowing?.workspaceTransfer === true
    },
    open: request => ipcRenderer.invoke('nova:hud:open', request),
    close: () => ipcRenderer.invoke('nova:hud:close'),
    setIgnoreMouse: ignore => ipcRenderer.send('nova:hud:ignore-mouse', ignore),
    beginMove: () => ipcRenderer.send('nova:hud:begin-move'),
    endMove: () => ipcRenderer.send('nova:hud:end-move'),
    moveBy: delta => ipcRenderer.send('nova:hud:move-by', delta),
    setWorkspaceTransfer: transferring => ipcRenderer.send('nova:hud:workspace-transfer', transferring),
    setBounds: bounds => ipcRenderer.send('nova:hud:set-bounds', bounds),
    resetLayout: () => ipcRenderer.invoke('nova:hud:reset-layout'),
    // Whether the band covers the window below the bar. Main pairs it with the
    // user's translucency setting to decide the native frost (macOS vibrancy /
    // Windows 11 DWM backdrop) — see hudFrostFor.
    setFrost: showing => ipcRenderer.invoke('nova:hud:frost', showing),
    // The HUD tells main which session it is on; main hands that back to the
    // app window when the HUD closes, so the app can re-home onto it.
    setSession: sessionId => ipcRenderer.send('nova:hud:session', sessionId),
    onGoto: callback => {
      const listener = (_event, sessionId) => callback(sessionId)
      ipcRenderer.on('nova:hud:goto', listener)

      return () => ipcRenderer.removeListener('nova:hud:goto', listener)
    },
    onChanged: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('nova:hud:changed', listener)

      return () => ipcRenderer.removeListener('nova:hud:changed', listener)
    },
    // Linux only, and silent elsewhere: where the cursor is, in page
    // coordinates, or null when it has left the window. Stands in for the
    // mousemove that `setIgnoreMouseEvents(true, { forward: true })` delivers on
    // macOS and Windows but not here.
    onCursor: callback => {
      const listener = (_event, point) => callback(point)
      ipcRenderer.on('nova:hud:cursor', listener)

      return () => ipcRenderer.removeListener('nova:hud:cursor', listener)
    },
    // Main's game-overlay watch: whether a fullscreen app (a game) is under
    // the HUD, so the renderer can step back to the low-opacity overlay
    // treatment while one owns the screen.
    onGameOverlay: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('nova:hud:game-overlay', listener)

      return () => ipcRenderer.removeListener('nova:hud:game-overlay', listener)
    }
  },
  hudModifier: {
    getSettings: () => ipcRenderer.invoke('nova:hud-modifier:settings:get'),
    setEnabled: enabled => ipcRenderer.invoke('nova:hud-modifier:settings:set', enabled),
    openPermissionSettings: () => ipcRenderer.invoke('nova:hud-modifier:permission'),
    onStatus: callback => {
      const listener = (_event: Electron.IpcRendererEvent, status: HudModifierStatus) => callback(status)
      ipcRenderer.on('nova:hud-modifier:status', listener)

      return () => ipcRenderer.removeListener('nova:hud-modifier:status', listener)
    }
  } satisfies HudModifierApi,
  // macOS native screenshot gesture; captures require a main-issued request.
  screenshot:
    process.platform === 'darwin'
      ? {
          getSettings: () => ipcRenderer.invoke('nova:screenshot:settings:get'),
          setEnabled: enabled => ipcRenderer.invoke('nova:screenshot:settings:set', enabled),
          openPermissionSettings: kind => ipcRenderer.invoke('nova:screenshot:permission', kind),
          capture: requestId => ipcRenderer.invoke('nova:screenshot:capture', requestId),
          onStatus: callback => {
            const listener = (_event, status) => callback(status)
            ipcRenderer.on('nova:screenshot:status', listener)

            return () => ipcRenderer.removeListener('nova:screenshot:status', listener)
          },
          onRequest: callback => {
            const channel = 'nova:screenshot:request'
            const listener = (_event, requestId) => callback(requestId)

            if (ipcRenderer.listenerCount(channel) === 0) {
              ipcRenderer.send('nova:screenshot:subscribe', true)
            }

            ipcRenderer.on(channel, listener)

            return () => {
              ipcRenderer.removeListener(channel, listener)

              if (ipcRenderer.listenerCount(channel) === 0) {
                ipcRenderer.send('nova:screenshot:subscribe', false)
              }
            }
          }
        }
      : undefined,
  // Quick Entry: the global-hotkey mini composer window. Main owns the OS
  // shortcut + the persisted preference; the quick window only captures text
  // and hands it back, and the primary renderer submits it through the normal
  // prompt path.
  quickEntry: {
    getSettings: () => ipcRenderer.invoke('nova:quick-entry:settings:get'),
    setSettings: patch => ipcRenderer.invoke('nova:quick-entry:settings:set', patch),
    // Invoke returns the delivery result so the draft is not lost (#85590).
    submit: payload => ipcRenderer.invoke('nova:quick-entry:submit', payload),
    // Main cannot invoke the primary renderer, so it receives this ack (#85590).
    ackSubmit: (correlationId, result) => ipcRenderer.send('nova:quick-entry:ack', { correlationId, result }),
    dismiss: () => ipcRenderer.send('nova:quick-entry:dismiss'),
    // Primary renderer → main → quick window: gateway connection state + the
    // recent-session options the target picker offers. Main caches the latest
    // payload so a freshly spawned quick window starts from truth.
    pushState: payload => ipcRenderer.send('nova:quick-entry:state', payload),
    // Quick window subscribes to those pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('nova:quick-entry:state', listener)

      return () => ipcRenderer.removeListener('nova:quick-entry:state', listener)
    },
    // Main → primary renderer: a submit captured by the quick window.
    onSubmit: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('nova:quick-entry:submit', listener)

      return () => ipcRenderer.removeListener('nova:quick-entry:submit', listener)
    },
    // Main → quick window: you were just summoned (reset draft + refocus).
    onShown: callback => {
      const listener = () => callback()
      ipcRenderer.on('nova:quick-entry:shown', listener)

      return () => ipcRenderer.removeListener('nova:quick-entry:shown', listener)
    },
    // Main → quick window: the outcome of a submit whose relay already timed
    // out. Delivery is now KNOWN — reconcile the unknown state instead of
    // leaving the user to resend a prompt that may already be delivered.
    onLateResult: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('nova:quick-entry:late-result', listener)

      return () => ipcRenderer.removeListener('nova:quick-entry:late-result', listener)
    }
  },
  getBootProgress: () => ipcRenderer.invoke('nova:boot-progress:get'),
  getConnectionConfig: profile => ipcRenderer.invoke('nova:connection-config:get', profile),
  saveConnectionConfig: payload => ipcRenderer.invoke('nova:connection-config:save', payload),
  applyConnectionConfig: payload => ipcRenderer.invoke('nova:connection-config:apply', payload),
  testConnectionConfig: payload => ipcRenderer.invoke('nova:connection-config:test', payload),
  // Opt-in OS-keychain encryption for stored gateway secrets (default off —
  // see secret-storage-policy.ts). get never touches the OS keychain.
  getSecretStorageEncryption: () => ipcRenderer.invoke('nova:secret-storage:get'),
  setSecretStorageEncryption: (on: boolean) => ipcRenderer.invoke('nova:secret-storage:set', on),
  // v2 multi-connection registry: named agent sources (local / remote / cloud / ssh).
  connections: {
    list: () => ipcRenderer.invoke('nova:connections:list'),
    save: payload => ipcRenderer.invoke('nova:connections:save', payload),
    remove: id => ipcRenderer.invoke('nova:connections:remove', id),
    setPrimary: id => ipcRenderer.invoke('nova:connections:set-primary', id),
    setLaunchMode: mode => ipcRenderer.invoke('nova:connections:set-launch-mode', mode),
    setLastUsed: id => ipcRenderer.invoke('nova:connections:set-last-used', id),
    test: id => ipcRenderer.invoke('nova:connections:test', id),
    updateManaged: id => ipcRenderer.invoke('nova:connections:update-managed', id),
    // Fan out `nova update` to every eligible registered connection.
    // Optional excludeIds skips rows the caller updates through another path.
    updateAll: options => ipcRenderer.invoke('nova:connections:update-all', options),
    // Registry lifecycle push (main → renderer): a connection was removed or
    // materially edited, so secondaries scoped to it must be disposed (and,
    // for edits, re-dialed at the new target).
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('nova:connections:changed', listener)

      return () => ipcRenderer.removeListener('nova:connections:changed', listener)
    }
  },
  sshConfigHosts: () => ipcRenderer.invoke('nova:ssh-config:hosts'),
  sshResolveHost: host => ipcRenderer.invoke('nova:ssh-config:resolve', host),
  probeConnectionConfig: remoteUrl => ipcRenderer.invoke('nova:connection-config:probe', remoteUrl),
  // `options` lets a registry-editor draft sign in BEFORE it is saved: the
  // main process settles the draft's connection id up front so the login
  // window writes into the per-connection cookie jar the saved entry will
  // read (not the legacy shared jar an unsaved URL would fall back to).
  oauthLoginConnectionConfig: (remoteUrl, options) =>
    ipcRenderer.invoke('nova:connection-config:oauth-login', remoteUrl, options),
  oauthLogoutConnectionConfig: remoteUrl => ipcRenderer.invoke('nova:connection-config:oauth-logout', remoteUrl),
  // Nova Cloud: one portal login powers discovery + silent per-agent sign-in
  // (cloud-auto-discovery Phase 3).
  cloud: {
    status: () => ipcRenderer.invoke('nova:cloud:status'),
    login: () => ipcRenderer.invoke('nova:cloud:login'),
    logout: () => ipcRenderer.invoke('nova:cloud:logout'),
    discover: org => ipcRenderer.invoke('nova:cloud:discover', org),
    agentSignIn: dashboardUrl => ipcRenderer.invoke('nova:cloud:agent-sign-in', dashboardUrl)
  },
  profile: {
    getDefault: () => ipcRenderer.invoke('nova:profile:default:get'),
    setDefault: (route: DesktopProfileRoute) => ipcRenderer.invoke('nova:profile:default:set', route),
    onDefaultChanged: (callback: (route: DesktopProfileRoute | null) => void) => {
      const listener = (_event: Electron.IpcRendererEvent, route: DesktopProfileRoute | null) => callback(route)
      ipcRenderer.on('nova:profile:default:changed', listener)

      return () => ipcRenderer.removeListener('nova:profile:default:changed', listener)
    },
    get: () => ipcRenderer.invoke('nova:profile:get'),
    remember: name => ipcRenderer.invoke('nova:profile:remember', name),
    set: name => ipcRenderer.invoke('nova:profile:set', name)
  },
  // The handler resolves an expected 404 with a sentinel instead of rejecting
  // (Electron logs a stack for every rejected invoke). Turn it back into the
  // rejection the renderer expects — see electron/api-expected-404.ts.
  api: request => ipcRenderer.invoke('nova:api', request).then(unwrapExpectedNotFound),
  notify: payload => ipcRenderer.invoke('nova:notify', payload),
  claimStartupLatency: () => ipcRenderer.invoke('nova:startup-latency:claim'),
  requestMicrophoneAccess: () => ipcRenderer.invoke('nova:requestMicrophoneAccess'),
  readWindowBelow: () => ipcRenderer.invoke('nova:window:readBelow'),
  readFileDataUrl: filePath => ipcRenderer.invoke('nova:readFileDataUrl', filePath),
  readFileDataUrlForAttach: filePath => ipcRenderer.invoke('nova:readFileDataUrlForAttach', filePath),
  dataUrlReadMax: {
    get: () => ipcRenderer.invoke('nova:data-url-read-max:get'),
    set: maxMb => ipcRenderer.invoke('nova:data-url-read-max:set', maxMb)
  },
  readFileText: filePath => ipcRenderer.invoke('nova:readFileText', filePath),
  readPluginSource: (filePath: string) => ipcRenderer.invoke('nova:readPluginSource', filePath),
  selectPaths: options => ipcRenderer.invoke('nova:selectPaths', options),
  selectSavePath: options => ipcRenderer.invoke('nova:selectSavePath', options),
  saveTextFileAs: request => ipcRenderer.invoke('nova:saveTextFileAs', request),
  writeClipboard: text => ipcRenderer.invoke('nova:writeClipboard', text),
  readClipboard: () => ipcRenderer.invoke('nova:readClipboard'),
  saveGatewayFile: payload => ipcRenderer.invoke('nova:saveGatewayFile', payload),
  saveImageFromUrl: url => ipcRenderer.invoke('nova:saveImageFromUrl', url),
  contextMenuEdit: command => ipcRenderer.invoke('nova:context-menu:edit', command),
  contextMenuCopyImage: () => ipcRenderer.invoke('nova:context-menu:copy-image'),
  contextMenuSpellcheck: action => ipcRenderer.invoke('nova:context-menu:spellcheck', action),
  contextMenuGuestAddWord: payload => ipcRenderer.invoke('nova:context-menu:guest-add-word', payload),
  spellcheckDictionary: (op, word) => ipcRenderer.invoke('nova:spellcheck:dictionary', { op, word }),
  onContextMenuSpellcheck: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nova:context-menu-spellcheck', listener)

    return () => ipcRenderer.removeListener('nova:context-menu-spellcheck', listener)
  },
  saveImageBuffer: (data, ext, name) => ipcRenderer.invoke('nova:saveImageBuffer', { data, ext, name }),
  capturePreview: payload => ipcRenderer.invoke('nova:capturePreview', payload),
  savePastedText: text => ipcRenderer.invoke('nova:savePastedText', { text }),
  saveClipboardImage: () => ipcRenderer.invoke('nova:saveClipboardImage'),
  getPathForFile: file => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  normalizePreviewTarget: (target, baseDir) => ipcRenderer.invoke('nova:normalizePreviewTarget', target, baseDir),
  watchPreviewFile: url => ipcRenderer.invoke('nova:watchPreviewFile', url),
  watchDirectory: dir => ipcRenderer.invoke('nova:watchDirectory', dir),
  stopPreviewFileWatch: id => ipcRenderer.invoke('nova:stopPreviewFileWatch', id),
  setActiveWork: payload => ipcRenderer.send('nova:active-work', payload),
  setTitleBarTheme: payload => ipcRenderer.send('nova:titlebar-theme', payload),
  setNativeTheme: mode => ipcRenderer.send('nova:native-theme', mode),
  setTranslucency: payload => ipcRenderer.send('nova:translucency', payload),
  setKeepAwake: mode => ipcRenderer.send('nova:keep-awake', mode),
  // Once per computer, after a timed sleep interrupted a turn with keep-awake off.
  onKeepAwakeOffer: callback => {
    const listener = () => callback()
    ipcRenderer.on('nova:keep-awake-offer', listener)

    return () => ipcRenderer.removeListener('nova:keep-awake-offer', listener)
  },
  minimizeToTray: {
    get: () => ipcRenderer.invoke('nova:minimize-to-tray:get'),
    set: on => ipcRenderer.invoke('nova:minimize-to-tray:set', on),
    onChanged: callback => {
      const listener = (_event, status) => callback(status)
      ipcRenderer.on('nova:minimize-to-tray:changed', listener)

      return () => ipcRenderer.removeListener('nova:minimize-to-tray:changed', listener)
    }
  },
  setDisableF12: blocked => ipcRenderer.send('nova:devtools:disable-f12', blocked),
  setF12ShortcutActive: active => ipcRenderer.send('nova:f12ShortcutActive', Boolean(active)),
  onF12Shortcut: callback => {
    const listener = (_event, input) => callback(input)
    ipcRenderer.on('nova:f12-shortcut', listener)

    return () => ipcRenderer.removeListener('nova:f12-shortcut', listener)
  },
  setPreviewShortcutActive: active => ipcRenderer.send('nova:previewShortcutActive', Boolean(active)),
  setPreviewGuestHidden: (webContentsId, hidden) =>
    ipcRenderer.send('nova:preview-guest-hidden', { webContentsId, hidden: Boolean(hidden) }),
  openExternal: url => ipcRenderer.invoke('nova:openExternal', url),
  freeTierChallenge: {
    // Load the account service's challenge page in a hidden window (revealed
    // only if the page asks for the human). Resolves with how it ended.
    run: request => ipcRenderer.invoke('nova:freeTierChallenge:run', request)
  },
  mcpOauth: {
    // One-shot loopback listener for MCP OAuth against remote backends: bind
    // on this machine, hand redirectUri to mcp.servers.oauth.start, then wait
    // for the provider redirect and relay code/state via oauth.callback.
    listen: () => ipcRenderer.invoke('nova:mcp-oauth:listen'),
    wait: (id, timeoutMs) => ipcRenderer.invoke('nova:mcp-oauth:wait', id, timeoutMs),
    cancel: id => ipcRenderer.invoke('nova:mcp-oauth:cancel', id)
  },
  openPreviewInBrowser: url => ipcRenderer.invoke('nova:openPreviewInBrowser', url),
  reachPreviewUrl: url => ipcRenderer.invoke('nova:preview:reach', url),
  setActiveConnectionRoute: route => ipcRenderer.send('nova:connection:active-route', route),
  fetchLinkTitle: url => ipcRenderer.invoke('nova:fetchLinkTitle', url),
  resolveFavicon: url => ipcRenderer.invoke('nova:resolveFavicon', url),
  sanitizeWorkspaceCwd: cwd => ipcRenderer.invoke('nova:workspace:sanitize', cwd),
  settings: {
    getDefaultProjectDir: () => ipcRenderer.invoke('nova:setting:defaultProjectDir:get'),
    setDefaultProjectDir: dir => ipcRenderer.invoke('nova:setting:defaultProjectDir:set', dir),
    pickDefaultProjectDir: () => ipcRenderer.invoke('nova:setting:defaultProjectDir:pick')
  },
  preparedSubmissions: {
    read: () => ipcRenderer.invoke('nova:prepared-submissions:read'),
    update: (key, entry) => ipcRenderer.invoke('nova:prepared-submissions:update', key, entry),
    compareAndSet: (key, expected, entry) =>
      ipcRenderer.invoke('nova:prepared-submissions:compare-and-set', key, expected, entry)
  },
  zoom: {
    // Current zoom of this window, as { level, percent }.
    get: () => ipcRenderer.invoke('nova:zoom:get'),
    // Synchronous zoom factor (1 = 100%). Coordinate math needs it in the
    // same tick as the event it converts, so no IPC round-trip here.
    factor: () => webFrame.getZoomFactor(),
    setPercent: percent => ipcRenderer.send('nova:zoom:set-percent', percent),
    // Fires on every zoom change, including the Ctrl/Cmd +/-/0 shortcuts,
    // so the settings UI can stay in sync with the keyboard.
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('nova:zoom:changed', listener)

      return () => ipcRenderer.removeListener('nova:zoom:changed', listener)
    }
  },
  revealLogs: () => ipcRenderer.invoke('nova:logs:reveal'),
  getRecentLogs: () => ipcRenderer.invoke('nova:logs:recent'),
  // Fire-and-forget: persists a renderer error-boundary catch (with component
  // stack) to desktop.log so crashes survive the window (#79428).
  reportRendererError: report => ipcRenderer.send('nova:logs:renderer-error', report),
  logLine: (line: string): void => ipcRenderer.send('nova:logs:renderer-line', line),
  readDir: dirPath => ipcRenderer.invoke('nova:fs:readDir', dirPath),
  gitRoot: startPath => ipcRenderer.invoke('nova:fs:gitRoot', startPath),
  revealPath: targetPath => ipcRenderer.invoke('nova:fs:reveal', targetPath),
  openDir: dirPath => ipcRenderer.invoke('nova:fs:openDir', dirPath),
  desktopPluginsRoot: () => ipcRenderer.invoke('nova:fs:desktopPluginsRoot'),
  reconcileDesktopPlugins: async () => (await ipcRenderer.invoke('nova:fs:reconcileDesktopPlugins')).published,
  reconcileDesktopPluginsReport: () => ipcRenderer.invoke('nova:fs:reconcileDesktopPlugins'),
  logsRoot: (profile?: string) => ipcRenderer.invoke('nova:fs:logsRoot', profile),
  renamePath: (targetPath, newName) => ipcRenderer.invoke('nova:fs:rename', targetPath, newName),
  writeTextFile: (filePath, content) => ipcRenderer.invoke('nova:fs:writeText', filePath, content),
  trashPath: targetPath => ipcRenderer.invoke('nova:fs:trash', targetPath),
  pluginFiles: {
    request: (pluginId, labels) => ipcRenderer.invoke('nova:pluginFiles:request', pluginId, labels),
    grants: pluginId => ipcRenderer.invoke('nova:pluginFiles:grants', pluginId),
    revoke: (pluginId, folderId) => ipcRenderer.invoke('nova:pluginFiles:revoke', pluginId, folderId),
    op: (pluginId, request) => ipcRenderer.invoke('nova:pluginFiles:op', pluginId, request),
    onChanged: callback => {
      const listener = (_event, change) => callback(change)
      ipcRenderer.on('nova:pluginFiles:changed', listener)

      return () => ipcRenderer.removeListener('nova:pluginFiles:changed', listener)
    }
  },
  git: {
    worktreeList: repoPath => ipcRenderer.invoke('nova:git:worktreeList', repoPath),
    worktreeAdd: (repoPath, options) => ipcRenderer.invoke('nova:git:worktreeAdd', repoPath, options),
    worktreeRemove: (repoPath, worktreePath, options) =>
      ipcRenderer.invoke('nova:git:worktreeRemove', repoPath, worktreePath, options),
    branchSwitch: (repoPath, branch) => ipcRenderer.invoke('nova:git:branchSwitch', repoPath, branch),
    branchList: repoPath => ipcRenderer.invoke('nova:git:branchList', repoPath),
    baseBranchList: repoPath => ipcRenderer.invoke('nova:git:baseBranchList', repoPath),
    repoStatus: repoPath => ipcRenderer.invoke('nova:git:repoStatus', repoPath),
    fileDiff: (repoPath, filePath) => ipcRenderer.invoke('nova:git:fileDiff', repoPath, filePath),
    scanRepos: (roots, options) => ipcRenderer.invoke('nova:git:scanRepos', roots, options),
    review: {
      list: (repoPath, scope, baseRef) => ipcRenderer.invoke('nova:git:review:list', repoPath, scope, baseRef),
      diff: (repoPath, filePath, scope, baseRef, staged) =>
        ipcRenderer.invoke('nova:git:review:diff', repoPath, filePath, scope, baseRef, staged),
      stage: (repoPath, filePath) => ipcRenderer.invoke('nova:git:review:stage', repoPath, filePath),
      unstage: (repoPath, filePath) => ipcRenderer.invoke('nova:git:review:unstage', repoPath, filePath),
      revert: (repoPath, filePath) => ipcRenderer.invoke('nova:git:review:revert', repoPath, filePath),
      revParse: (repoPath, ref) => ipcRenderer.invoke('nova:git:review:revParse', repoPath, ref),
      commit: (repoPath, message, push) => ipcRenderer.invoke('nova:git:review:commit', repoPath, message, push),
      commitContext: repoPath => ipcRenderer.invoke('nova:git:review:commitContext', repoPath),
      push: repoPath => ipcRenderer.invoke('nova:git:review:push', repoPath),
      shipInfo: repoPath => ipcRenderer.invoke('nova:git:review:shipInfo', repoPath),
      prList: (repoPath, branches, numbers) =>
        ipcRenderer.invoke('nova:git:review:prList', repoPath, branches, numbers),
      createPr: repoPath => ipcRenderer.invoke('nova:git:review:createPr', repoPath)
    }
  },
  terminal: {
    attach: id => ipcRenderer.invoke('nova:terminal:attach', id),
    cwd: id => ipcRenderer.invoke('nova:terminal:cwd', id),
    dispose: id => ipcRenderer.invoke('nova:terminal:dispose', id),
    resize: (id, size) => ipcRenderer.invoke('nova:terminal:resize', id, size),
    start: options => ipcRenderer.invoke('nova:terminal:start', options),
    write: (id, data) => ipcRenderer.invoke('nova:terminal:write', id, data),
    onData: (id, callback) => {
      const channel = `nova:terminal:${id}:data`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    },
    onExit: (id, callback) => {
      const channel = `nova:terminal:${id}:exit`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    }
  },
  onClosePreviewRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('nova:close-preview-requested', listener)

    return () => ipcRenderer.removeListener('nova:close-preview-requested', listener)
  },
  onPreviewNav: callback => {
    const listener = (_event, command) => callback(command)
    ipcRenderer.on('nova:preview-nav', listener)

    return () => ipcRenderer.removeListener('nova:preview-nav', listener)
  },
  onOpenFolderRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('nova:open-folder-requested', listener)

    return () => ipcRenderer.removeListener('nova:open-folder-requested', listener)
  },
  onOpenUpdatesRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('nova:open-updates', listener)

    return () => ipcRenderer.removeListener('nova:open-updates', listener)
  },
  onDeepLink: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nova:deep-link', listener)

    return () => ipcRenderer.removeListener('nova:deep-link', listener)
  },
  signalDeepLinkReady: () => ipcRenderer.invoke('nova:deep-link-ready'),
  probePluginRepo: payload => ipcRenderer.invoke('nova:plugin:probe', payload),
  installDesktopPlugin: payload => ipcRenderer.invoke('nova:plugin:installDesktop', payload),
  removeDesktopPlugin: payload => ipcRenderer.invoke('nova:plugin:removeDesktop', payload),
  onWindowStateChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nova:window-state-changed', listener)

    return () => ipcRenderer.removeListener('nova:window-state-changed', listener)
  },
  onFocusSession: callback => {
    const listener = (_event, sessionId) => callback(sessionId)
    ipcRenderer.on('nova:focus-session', listener)

    return () => ipcRenderer.removeListener('nova:focus-session', listener)
  },
  onNotificationAction: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nova:notification-action', listener)

    return () => ipcRenderer.removeListener('nova:notification-action', listener)
  },
  onNotificationActivate: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nova:notification-activate', listener)

    return () => ipcRenderer.removeListener('nova:notification-activate', listener)
  },
  onExternalOpenFailed: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nova:external-open-failed', listener)

    return () => ipcRenderer.removeListener('nova:external-open-failed', listener)
  },
  onPreviewFileChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nova:preview-file-changed', listener)

    return () => ipcRenderer.removeListener('nova:preview-file-changed', listener)
  },
  onBackendExit: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nova:backend-exit', listener)

    return () => ipcRenderer.removeListener('nova:backend-exit', listener)
  },
  // Soft gateway-mode apply finished tearing down the primary backend. Renderer
  // should wipe session lists + re-dial without a window reload.
  onConnectionApplied: callback => {
    const listener = () => callback()
    ipcRenderer.on('nova:connection:applied', listener)

    return () => ipcRenderer.removeListener('nova:connection:applied', listener)
  },
  onPowerResume: callback => {
    const listener = () => callback()
    ipcRenderer.on('nova:power-resume', listener)

    return () => ipcRenderer.removeListener('nova:power-resume', listener)
  },
  // AC ↔ battery transitions; renderers slow their backstop polls on battery.
  getOnBattery: () => ipcRenderer.invoke('nova:power-battery:get'),
  onBatteryChanged: callback => {
    const listener = (_event, onBattery) => callback(Boolean(onBattery))
    ipcRenderer.on('nova:power-battery', listener)

    return () => ipcRenderer.removeListener('nova:power-battery', listener)
  },
  onBootProgress: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nova:boot-progress', listener)

    return () => ipcRenderer.removeListener('nova:boot-progress', listener)
  },
  // First-launch bootstrap progress -- emitted by the install.ps1 stage
  // runner in main.ts (apps/desktop/electron/bootstrap-runner.ts).
  // Renderer's install overlay subscribes to live events and queries the
  // current snapshot via getBootstrapState() to recover after a devtools
  // reload mid-bootstrap.
  getBootstrapState: () => ipcRenderer.invoke('nova:bootstrap:get'),
  probeLocalBackend: () => ipcRenderer.invoke('nova:local-backend:probe'),
  continueBootstrapLocal: () => ipcRenderer.invoke('nova:bootstrap:continue-local'),
  recycleBackend: profile => ipcRenderer.invoke('nova:backend:recycle', profile),
  resetBootstrap: () => ipcRenderer.invoke('nova:bootstrap:reset'),
  updateHold: {
    recheck: () => ipcRenderer.invoke('nova:update-hold:recheck'),
    quit: () => ipcRenderer.invoke('nova:update-hold:quit'),
    startAnyway: (request: { holdId: string; confirmed: true }) =>
      ipcRenderer.invoke('nova:update-hold:start-anyway', request)
  },
  repairBootstrap: () => ipcRenderer.invoke('nova:bootstrap:repair'),
  cancelBootstrap: () => ipcRenderer.invoke('nova:bootstrap:cancel'),
  onBootstrapEvent: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nova:bootstrap:event', listener)

    return () => ipcRenderer.removeListener('nova:bootstrap:event', listener)
  },
  getVersion: (scope?: { connectionId?: string; profile?: string }) => ipcRenderer.invoke('nova:version', scope),
  relaunchApp: () => ipcRenderer.invoke('nova:app:relaunch'),
  getMachineProfile: () => ipcRenderer.invoke('nova:machine:profile'),
  getRemoteDisplayReason: () => ipcRenderer.invoke('nova:get-remote-display-reason'),
  uninstall: {
    summary: () => ipcRenderer.invoke('nova:uninstall:summary'),
    run: mode => ipcRenderer.invoke('nova:uninstall:run', { mode }),
    openAppsSettings: () => ipcRenderer.invoke('nova:uninstall:openAppsSettings')
  },
  updates: {
    check: opts => ipcRenderer.invoke('nova:updates:check', opts),
    apply: opts => ipcRenderer.invoke('nova:updates:apply', opts),
    setChannel: name => ipcRenderer.invoke('nova:updates:channel:set', name),
    onProgress: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('nova:updates:progress', listener)

      return () => ipcRenderer.removeListener('nova:updates:progress', listener)
    },
    takePendingRun: () => ipcRenderer.invoke('nova:updates:metric:take'),
    ackPendingRun: sent => ipcRenderer.invoke('nova:updates:metric:ack', sent),
    onPendingRun: callback => {
      const listener = () => callback()
      ipcRenderer.on('nova:updates:metric:pending', listener)

      return () => ipcRenderer.removeListener('nova:updates:metric:pending', listener)
    }
  },
  desktopMetrics: {
    setEnabled: (on, profile) => ipcRenderer.invoke('nova:desktop-metrics:set-enabled', on, profile),
    takeRendererCrashes: () => ipcRenderer.invoke('nova:desktop-metrics:crash:take'),
    ackRendererCrashes: sent => ipcRenderer.invoke('nova:desktop-metrics:crash:ack', sent)
  },
  themes: {
    fetchMarketplace: id => ipcRenderer.invoke('nova:vscode-theme:fetch', id),
    searchMarketplace: query => ipcRenderer.invoke('nova:vscode-theme:search', query)
  },
  // Find-in-page (Ctrl/Cmd+F): delegates to Electron's
  // webContents.findInPage on the IPC sender's window so a Cmd+F pressed
  // in a secondary session window searches THAT window, not the primary.
  // `onFoundInPage` returns the unsubscribe fn; the renderer wires it via
  // `initFindInPageListener` in store/find-in-page.ts and tears it down
  // when the FindBar unmounts.
  findInPage: (query, options) => ipcRenderer.invoke('nova:find-in-page', query, options),
  stopFindInPage: () => ipcRenderer.invoke('nova:stop-find-in-page'),
  onFoundInPage: callback => {
    const listener = (_event, result) => callback(result)
    ipcRenderer.on('nova:found-in-page', listener)

    return () => ipcRenderer.removeListener('nova:found-in-page', listener)
  },
  // Main-process `before-input-event` forwards Ctrl/Cmd+F here so renderer
  // can open the FindBar even when the GTK compositor has already grabbed
  // the chord at the windowing layer (#81727).
  onOpenFindBarRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('nova:open-find-bar', listener)

    return () => ipcRenderer.removeListener('nova:open-find-bar', listener)
  }
})
