import React, {useCallback, useEffect, useState} from 'react';
import {Coffee, RefreshCw, MapPin, CalendarDays} from 'lucide-react';

const FACILITIES = [
  'wifi', 'parking', 'outdoor_seating', 'indoor_seating',
  'wheelchair_accessible', 'air_conditioning', 'restroom',
  'power_outlets', 'private_seating', 'group_seating',
  'pet_friendly', 'vegetarian_options', 'vegan_options',
  'halal_options', 'live_music', 'quiet_area'
];

const facilityLabel = value =>
  value.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

const API = (
  import.meta.env.VITE_API_URL || ''
).replace(/\/$/, '');

export default function CafeVenueOverview({token, expectedVenueId}) {
  const [venue, setVenue] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedFacilities, setSelectedFacilities] = useState([]);
  const [saving, setSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState('');


  const loadVenue = useCallback(async (signal) => {
    setLoading(true);
    setError('');

    try {
      const response = await fetch(
        `${API}/api/v1/cafe-partners/venue`,
        {
          headers: {Authorization: `Bearer ${token}`},
          cache: 'no-store',
          signal
        }
      );

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          response.status === 401
            ? 'Café session expired. Please sign in again.'
            : typeof data.error === 'string'
              ? data.error
              : 'Venue information is temporarily unavailable.'
        );
      }

      if (!data.venue || typeof data.venue !== 'object') {
        throw new Error('Invalid venue response.');
      }

      const returnedId = data.venue.venue_id ||
        data.venue.id ||
        data.venue._id;

      if (
        returnedId &&
        String(returnedId) !== String(expectedVenueId)
      ) {
        throw new Error('Venue identity mismatch.');
      }

      if (!signal?.aborted) {
        setVenue(data.venue);
        setSelectedFacilities(
          Array.isArray(data.venue.facilities)
            ? data.venue.facilities.filter(v => FACILITIES.includes(v))
            : []
        );
        setSaveMessage('');
      }
    } catch (failure) {
      if (!signal?.aborted) {
        setError(failure.message || 'Unable to load venue.');
      }
    } finally {
      if (!signal?.aborted) {
        setLoading(false);
      }
    }
  }, [token, expectedVenueId]);

  useEffect(() => {
    const controller = new AbortController();
    loadVenue(controller.signal);
    return () => controller.abort();
  }, [loadVenue]);

  const facilities = Array.isArray(venue?.facilities)
    ? venue.facilities
    : [];

  function toggleFacility(value) {
    setSaveMessage('');
    setSelectedFacilities(previous =>
      previous.includes(value)
        ? previous.filter(item => item !== value)
        : [...previous, value]
    );
  }

  async function saveFacilities() {
    if (saving || loading || !venue) return;

    setSaving(true);
    setError('');
    setSaveMessage('');

    try {
      const facilities = [...selectedFacilities].sort();

      const response = await fetch(
        `${API}/api/v1/cafe-partners/venue/operations`,
        {
          method: 'PATCH',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({facilities}),
          cache: 'no-store'
        }
      );

      const result = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          typeof result.error === 'string'
            ? result.error
            : 'Unable to save facilities.'
        );
      }

      if (
        String(result.venue_id) !== String(expectedVenueId) ||
        result.status !== 'approved' ||
        !Array.isArray(result.facilities) ||
        JSON.stringify(result.facilities) !== JSON.stringify(facilities)
      ) {
        throw new Error('Unexpected venue update response.');
      }

      setVenue(previous => ({
        ...previous,
        facilities: result.facilities
      }));
      setSelectedFacilities(result.facilities);
      setSaveMessage('Facilities updated successfully.');
    } catch (failure) {
      setError(failure.message || 'Unable to save facilities.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="dash-grid">
      <div className="dash-card">
        <div className="dash-card-head">
          <span><Coffee size={17}/> YOUR VENUE</span>
          <button
            type="button"
            className="button button-outline"
            disabled={loading}
            onClick={() => loadVenue()}
          >
            <RefreshCw size={15}/> Refresh
          </button>
        </div>

        {loading && <p>Loading venue information...</p>}

        {error && <p role="alert">{error}</p>}

        {!loading && venue && (
          <>
            <h2>{venue.name || venue.venue_name || 'Your café'}</h2>

            <p>
              <MapPin size={15}/>
              {' '}
              {typeof venue.address === 'string'
                ? venue.address
                : venue.city || 'Address not provided'}
            </p>

            <p>
              Status: {venue.status || venue.approval_status || 'Available'}
            </p>

            {venue.seat_capacity != null && (
              <p>Seating capacity: {venue.seat_capacity}</p>
            )}

            {venue.timezone && (
              <p>Timezone: {venue.timezone}</p>
            )}

            <h3>Manage facilities</h3>
            <p>Select the facilities available at your café.</p>

            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
              gap: 12,
              marginTop: 12
            }}>
              {FACILITIES.map(facility => (
                <label key={facility} style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8
                }}>
                  <input
                    type="checkbox"
                    checked={selectedFacilities.includes(facility)}
                    onChange={() => toggleFacility(facility)}
                    disabled={saving}
                  />
                  {facilityLabel(facility)}
                </label>
              ))}
            </div>

            <button
              type="button"
              className="button button-dark"
              onClick={saveFacilities}
              disabled={
                saving || loading ||
                JSON.stringify([...selectedFacilities].sort()) ===
                JSON.stringify([...facilities].sort())
              }
              style={{marginTop: 20}}
            >
              {saving ? 'Saving...' : 'Save facilities'}
            </button>

            {saveMessage && <p role="status">{saveMessage}</p>}

            {venue.booking_settings && (
              <>
                <h3><CalendarDays size={17}/> Booking settings</h3>
                <p>
                  Booking configuration is available for this venue.
                </p>
              </>
            )}
          </>
        )}
      </div>
    </section>
  );
}