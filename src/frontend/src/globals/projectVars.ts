// The API runs on the same host the page was loaded from. Keeping the host identical (only
// the port differs) is what lets the browser send the session cookie along with API requests.
export const domain = window.location.hostname
export const port = 3002
