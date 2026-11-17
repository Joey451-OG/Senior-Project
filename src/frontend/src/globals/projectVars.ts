// The API is reached through the page's own origin: the reverse proxy when deployed, Vite's dev-server
// proxy (vite.config.ts) in development. Same origin is what lets the browser send the session cookie,
// and it means the page works under whatever host name and scheme it was loaded from.
export const domain = window.location.hostname

export const auth_url = '/auth'
export const papi_url = '/papi'
// WebSockets need an absolute URL; wss on an https page, or the browser blocks it as mixed content
export const ws_url = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`
