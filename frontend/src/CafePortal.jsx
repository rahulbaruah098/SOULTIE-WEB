import CafeBookingDashboard from './CafeBookingDashboard.jsx';
import CafeVenueOverview from './CafeVenueOverview.jsx';
import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Coffee, ShieldCheck, LogOut, ArrowRight, RefreshCw
} from 'lucide-react';

const API = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');
const BASE = '/api/v1/cafe-partners';

async function cafeRequest(path, options = {}) {
  const response = await fetch(`${API}${BASE}${path}`, {
    method: options.method || 'GET',
    headers: {
      'Content-Type': 'application/json',
      ...(options.token
        ? { Authorization: `Bearer ${options.token}` }
        : {})
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
    cache: 'no-store'
  });

  const result = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(
      typeof result.error === 'string'
        ? result.error
        : `Request failed (${response.status})`
    );
  }

  return result;
}

export default function CafePortal() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [session, setSession] = useState(null);
  const [identity, setIdentity] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function login(event) {
    event.preventDefault();
    if (busy) return;

    setBusy(true);
    setError('');

    let issuedToken = null;

    try {
      const result = await cafeRequest('/login', {
        method: 'POST',
        body: { email: email.trim(), password }
      });

      if (
        result.account_type !== 'cafe_partner' ||
        result.token_type !== 'Bearer' ||
        typeof result.access_token !== 'string' ||
        !result.access_token
      ) {
        throw new Error('Invalid café session response.');
      }

      issuedToken = result.access_token;

      const profile = await cafeRequest('/me', {
        token: issuedToken
      });

      if (
        profile.account_type !== 'cafe_partner' ||
        profile.partner_id !== result.partner_id ||
        profile.venue_id !== result.venue_id
      ) {
        throw new Error('Café identity verification failed.');
      }

      setSession({ token: issuedToken });
      setIdentity(profile);
      setPassword('');
    } catch (failure) {
      if (issuedToken) {
        await cafeRequest('/logout', {
          method: 'POST',
          token: issuedToken
        }).catch(() => {});
      }

      setSession(null);
      setIdentity(null);
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  async function refresh() {
    if (!session || busy) return;

    setBusy(true);
    setError('');

    try {
      const profile = await cafeRequest('/me', {
        token: session.token
      });

      if (
        profile.account_type !== 'cafe_partner' ||
        profile.partner_id !== identity.partner_id ||
        profile.venue_id !== identity.venue_id
      ) {
        throw new Error('Café session identity changed.');
      }

      setIdentity(profile);
    } catch {
      setSession(null);
      setIdentity(null);
      setError('Session verification failed. Sign in again.');
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    if (busy) return;

    setBusy(true);
    const token = session?.token;

    setSession(null);
    setIdentity(null);
    setPassword('');
    setError('');

    try {
      if (token) {
        await cafeRequest('/logout', {
          method: 'POST',
          token
        });
      }
    } catch {
      setError('Local session cleared. Server logout could not be confirmed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="admin-page">
      <header className="admin-header">
        <Link to="/" className="brand" aria-label="Xuoroni home">
          <span className="brand-mark"><Coffee size={23}/></span>
          <span>XUORONI</span>
        </Link>

        <Link to="/" className="back-link">
          ← Public website
        </Link>
      </header>

      {!session ? (
        <main className="login-card">
          <div className="section-kicker">
            XUORONI / CAFÉ PARTNER
          </div>

          <div className="login-icon"><Coffee/></div>

          <h1>Partner sign in</h1>
          <p>Manage your approved Xuoroni café account.</p>

          <form onSubmit={login}>
            <label>
              Registered email
              <input
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                autoComplete="username"
                required
              />
            </label>

            <label>
              Password
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </label>

            <button className="button button-dark" disabled={busy}>
              {busy ? 'Signing in...' : 'Sign in'}
              <ArrowRight size={17}/>
            </button>
          </form>

          {error && (
            <p role="alert" className="form-msg error">{error}</p>
          )}
        </main>
      ) : (
        <main className="dashboard">
          <div className="dash-title">
            <div>
              <div className="section-kicker">
                XUORONI / CAFÉ PARTNER
              </div>
              <h1>Partner dashboard</h1>
              <p>Your approved café account is connected.</p>
            </div>

            <button
              className="button button-outline"
              disabled={busy}
              onClick={logout}
            >
              <LogOut size={16}/> Sign out
            </button>
          </div>

          <CafeVenueOverview token={session.token} expectedVenueId={identity.venue_id}/>
          <CafeBookingDashboard token={session.token} expectedVenueId={identity.venue_id}/>
          <section className="dash-grid">
            <div className="dash-card">
              <div className="dash-card-head">
                <span>
                  <ShieldCheck size={17}/> ACCOUNT
                </span>
                <span className="pill">AUTHENTICATED</span>
              </div>

              <h2>Café partner</h2>

              <p>Account ID: {identity?.partner_id}</p>
              <p>Venue ID: {identity?.venue_id}</p>

              <button
                type="button"
                className="button button-outline"
                onClick={refresh}
                disabled={busy}
              >
                <RefreshCw size={16}/> Verify session
              </button>
            </div>
          </section>

          {error && (
            <p role="alert" className="form-msg error">{error}</p>
          )}
        </main>
      )}
    </div>
  );
}