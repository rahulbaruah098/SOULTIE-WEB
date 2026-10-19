import React, { useEffect, useState } from 'react';
import { CalendarDays, RefreshCw } from 'lucide-react';

const API = (
  import.meta.env.VITE_API_URL || ''
).replace(/\/$/, '');

const statuses = [
  ['all', 'All bookings'],
  ['pending', 'Pending'],
  ['confirmed', 'Confirmed'],
  ['rejected', 'Rejected'],
  ['cancelled', 'Cancelled'],
  ['completed', 'Completed'],
  ['no_show', 'No-show']
];

function formatDate(value) {
  if (!value) return 'Not specified';

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return 'Not specified';
  }

  return date.toLocaleString();
}

function safeText(value) {
  return typeof value === 'string' ||
    typeof value === 'number'
    ? String(value)
    : '—';
}

const BOOKING_ACTIONS = {
  pending: [
    ['confirmed', 'Confirm'],
    ['rejected', 'Reject'],
    ['cancelled', 'Cancel']
  ],
  confirmed: [
    ['completed', 'Complete'],
    ['no_show', 'No-show'],
    ['cancelled', 'Cancel']
  ]
};
export default function CafeBookingDashboard({
  token,
  expectedVenueId
}) {
  const [status, setStatus] = useState('all');
  const [bookings, setBookings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [actingId, setActingId] = useState(null);
  const [actionError, setActionError] = useState('');
  const [actionMessage, setActionMessage] = useState('');

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      setError('');

      try {
        const params = new URLSearchParams({
          status,
          limit: '50'
        });

        const response = await fetch(
          `${API}/api/v1/cafe-partners/bookings?${params}`,
          {
            headers: {
              Authorization: `Bearer ${token}`
            },
            cache: 'no-store',
            signal: controller.signal
          }
        );

        const result = await response.json().catch(() => ({}));

        if (!response.ok) {
          throw new Error(
            response.status === 401
              ? 'Session expired. Please sign in again.'
              : typeof result.error === 'string'
                ? result.error
                : 'Unable to load bookings.'
          );
        }

        if (
          String(result.venue_id) !== String(expectedVenueId)
        ) {
          throw new Error('Booking venue identity mismatch.');
        }

        if (
          !Array.isArray(result.bookings) ||
          result.bookings.length > 50 ||
          result.bookings.some(
            item =>
              !item ||
              typeof item.reservation_id !== 'string'
          )
        ) {
          throw new Error('Invalid booking response.');
        }

        if (!controller.signal.aborted) {
          setBookings(result.bookings);
        }
      } catch (failure) {
        if (!controller.signal.aborted) {
          setBookings([]);
          setError(
            failure.message || 'Booking information unavailable.'
          );
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    }

    load();

    return () => controller.abort();
  }, [token, expectedVenueId, status, revision]);

  async function submitBookingDecision(booking, decision) {
    if (actingId || loading || !booking) return;

    const permitted = BOOKING_ACTIONS[booking.status] || [];

    if (!permitted.some(([value]) => value === decision)) {
      setActionError('This action is unavailable for the booking.');
      return;
    }

    const label = permitted.find(
      ([value]) => value === decision
    )?.[1] || decision;

    const approved = window.confirm(
      `${label} booking ${booking.reservation_id}?`
    );

    if (!approved) return;

    setActingId(booking.reservation_id);
    setActionError('');
    setActionMessage('');

    try {
      const response = await fetch(
        `${API}/api/v1/cafe-partners/bookings/${encodeURIComponent(
          booking.reservation_id
        )}/decision`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({decision}),
          cache: 'no-store'
        }
      );

      const result = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          response.status === 409
            ? 'Booking status has changed. Refresh before trying again.'
            : typeof result.error === 'string'
              ? result.error
              : 'Unable to update this booking.'
        );
      }

      if (
        String(result.venue_id) !== String(expectedVenueId) ||
        String(result.reservation_id) !==
          String(booking.reservation_id) ||
        result.status !== decision
      ) {
        throw new Error(
          'Booking response could not be verified. Refresh the list.'
        );
      }

      setActionMessage(
        `Booking ${booking.reservation_id} updated to ${
          decision.replace(/_/g, ' ')
        }.`
      );

      setRevision(value => value + 1);
    } catch (failure) {
      setActionError(
        failure.message || 'Booking update could not be confirmed.'
      );
      setRevision(value => value + 1);
    } finally {
      setActingId(null);
    }
  }
  return (
    <section className="dash-card" aria-label="Cafe bookings">
      <div className="dash-card-head">
        <span>
          <CalendarDays size={17}/> RESERVATIONS
        </span>

        <button
          type="button"
          className="button button-outline"
          disabled={loading}
          onClick={() => setRevision(value => value + 1)}
        >
          <RefreshCw size={15}/> Refresh
        </button>
      </div>

      <h2>Booking management</h2>
      <p>View reservations for your approved café.</p>

      <label htmlFor="cafe-booking-filter">
        Booking status
      </label>

      <select
        id="cafe-booking-filter"
        value={status}
        onChange={event => setStatus(event.target.value)}
      >
        {statuses.map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>

      {loading && <p role="status">Loading bookings...</p>}

      {error && <p role="alert">{error}</p>}
      {actionError && <p role="alert">{actionError}</p>}
      {actionMessage && <p role="status">{actionMessage}</p>}

      {!loading && !error && bookings.length === 0 && (
        <p>No bookings found for this filter.</p>
      )}

      {!loading && !error && bookings.length > 0 && (
        <div style={{overflowX: 'auto', marginTop: 20}}>
          <table style={{
            width: '100%',
            borderCollapse: 'collapse',
            textAlign: 'left'
          }}>
            <thead>
              <tr>
                <th scope="col">Booking ID</th>
                <th scope="col">Date and time</th>
                <th scope="col">Guests</th>
                <th scope="col">Status</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>

            <tbody>
              {bookings.map(booking => (
                <tr key={booking.reservation_id}>
                  <td>{booking.reservation_id}</td>
                  <td>{formatDate(booking.start_at)}</td>
                  <td>{safeText(booking.party_size)}</td>
                  <td>
                    <span className="pill">
                      {safeText(booking.status).replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td>
                    <div style={{
                      display: 'flex',
                      gap: 8,
                      flexWrap: 'wrap'
                    }}>
                      {(BOOKING_ACTIONS[booking.status] || []).map(
                        ([decision, label]) => (
                          <button
                            key={decision}
                            type="button"
                            className="button button-outline"
                            disabled={Boolean(actingId) || loading}
                            onClick={() =>
                              submitBookingDecision(booking, decision)
                            }
                          >
                            {actingId === booking.reservation_id
                              ? 'Updating...'
                              : label}
                          </button>
                        )
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && !error && bookings.length === 50 && (
        <p>
          Showing the 50 most recent matching bookings.
        </p>
      )}
    </section>
  );
}