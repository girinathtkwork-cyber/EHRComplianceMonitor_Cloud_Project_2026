"use strict";

(() => {
  const config = window.EHR_COGNITO_CONFIG || {};
  const enabled = Boolean(window.EHR_API_BASE_URL && config.domain && config.clientId && config.redirectUri);
  const tokenKey = "ehr_access_token";
  const refreshKey = "ehr_refresh_token";
  const expiryKey = "ehr_token_expiry";
  const listeners = new Set();

  function encodeBase64Url(bytes) {
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  }

  function randomValue(size = 32) {
    const bytes = crypto.getRandomValues(new Uint8Array(size));
    return encodeBase64Url(bytes);
  }

  async function challengeFor(verifier) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
    return encodeBase64Url(new Uint8Array(digest));
  }

  function notify() { for (const listener of listeners) listener(); }
  function clearTokens() {
    sessionStorage.removeItem(tokenKey);
    sessionStorage.removeItem(refreshKey);
    sessionStorage.removeItem(expiryKey);
    notify();
  }

  async function exchange(body) {
    const response = await fetch(`${config.domain}/oauth2/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(body),
    });
    const tokens = await response.json();
    if (!response.ok || !tokens.access_token) throw new Error("Cognito sign-in failed. Start sign-in again.");
    sessionStorage.setItem(tokenKey, tokens.access_token);
    if (tokens.refresh_token) sessionStorage.setItem(refreshKey, tokens.refresh_token);
    sessionStorage.setItem(expiryKey, String(Date.now() + Number(tokens.expires_in || 3600) * 1000));
    notify();
  }

  async function handleCallback() {
    const url = new URL(window.location.href);
    const error = url.searchParams.get("error_description") || url.searchParams.get("error");
    const code = url.searchParams.get("code");
    if (!code && !error) return;
    const returnedState = url.searchParams.get("state");
    const expectedState = sessionStorage.getItem("ehr_oauth_state");
    const verifier = sessionStorage.getItem("ehr_pkce_verifier");
    sessionStorage.removeItem("ehr_oauth_state");
    sessionStorage.removeItem("ehr_pkce_verifier");
    history.replaceState({}, document.title, `${url.pathname}${url.hash}`);
    if (error) throw new Error("Cognito sign-in was cancelled or rejected.");
    if (!code || !expectedState || returnedState !== expectedState || !verifier) throw new Error("The sign-in response could not be verified. Start sign-in again.");
    await exchange({ grant_type: "authorization_code", client_id: config.clientId, redirect_uri: config.redirectUri, code, code_verifier: verifier });
  }

  async function refresh() {
    const refreshToken = sessionStorage.getItem(refreshKey);
    if (!refreshToken) return null;
    try {
      await exchange({ grant_type: "refresh_token", client_id: config.clientId, refresh_token: refreshToken });
      return sessionStorage.getItem(tokenKey);
    } catch {
      clearTokens();
      return null;
    }
  }

  async function getAccessToken() {
    if (!enabled) return null;
    await ready;
    const token = sessionStorage.getItem(tokenKey);
    const expiry = Number(sessionStorage.getItem(expiryKey) || 0);
    if (token && expiry > Date.now() + 60_000) return token;
    return refresh();
  }

  async function signIn() {
    if (!enabled) throw new Error("Cloud sign-in is not configured for this dashboard.");
    const verifier = randomValue(48);
    const state = randomValue(32);
    sessionStorage.setItem("ehr_pkce_verifier", verifier);
    sessionStorage.setItem("ehr_oauth_state", state);
    const challenge = await challengeFor(verifier);
    const authorize = new URL(`${config.domain}/oauth2/authorize`);
    authorize.search = new URLSearchParams({
      response_type: "code", client_id: config.clientId, redirect_uri: config.redirectUri,
      scope: "openid email", state, code_challenge_method: "S256", code_challenge: challenge,
    });
    window.location.assign(authorize.toString());
  }

  function signOut() {
    clearTokens();
    if (!enabled) return;
    const logout = new URL(`${config.domain}/logout`);
    logout.search = new URLSearchParams({ client_id: config.clientId, logout_uri: config.logoutUri });
    window.location.assign(logout.toString());
  }

  const ready = enabled ? handleCallback().catch((error) => { clearTokens(); throw error; }) : Promise.resolve();
  window.ehrAuth = {
    enabled,
    ready,
    getAccessToken,
    signIn,
    signOut,
    onChange(listener) { listeners.add(listener); listener(); return () => listeners.delete(listener); },
    hasSession() { return Boolean(sessionStorage.getItem(tokenKey)); },
  };
})();
