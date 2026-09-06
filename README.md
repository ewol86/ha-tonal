# Tonal for Home Assistant

Bring your Tonal workout history into Home Assistant as sensors — workout counts,
volume, streak gaps, and your Strength Score broken down by region and muscle group.

Built on the API flow from [ToneGet](https://github.com/curlrequests/toneget),
ported to Home Assistant's async stack with a config flow, token refresh, and
on-disk caching so it is cheap to poll.

> ⚠️ **Disclaimer**: Unofficial and community-built. Not affiliated with,
> endorsed by, or connected to Tonal Systems, Inc. Use at your own risk.

## Installation

### HACS (recommended)

1. HACS → **⋮** → **Custom repositories**
2. Add this repository's URL, category **Integration**
3. Install **Tonal**, then restart Home Assistant
4. **Settings → Devices & Services → Add Integration → Tonal**

### Manual

Copy `custom_components/tonal/` into your Home Assistant `config/custom_components/`
directory and restart.

## Configuration

Everything is done in the UI. You are asked for your Tonal email and password.
They are sent directly to Tonal's Auth0 endpoint over HTTPS and stored only in
your local config entry — the integration uses the refresh token for routine
polling and only falls back to the password when the session cannot be renewed.

Under **Configure** you can set:

| Option | Default | Notes |
| --- | --- | --- |
| Update interval | 30 minutes | Workout history barely moves; going below 15 minutes buys you nothing. |
| Look up workout names | on | Fetches each workout template's name once and caches it to disk. Turn off to minimise requests. |

## Entities

All entities live on a single device per Tonal account.

**Totals**

- `sensor.total_workouts`
- `sensor.total_volume` (lb)
- `sensor.total_reps`
- `sensor.custom_workouts` (diagnostic)

**Most recent workout**

- `sensor.last_workout` — timestamp, with the workout summary in its attributes
- `sensor.last_workout_name`
- `sensor.last_workout_volume` (lb)
- `sensor.last_workout_reps`
- `sensor.last_workout_duration` (minutes)
- `sensor.days_since_last_workout`

**Recent activity**

- `sensor.workouts_last_7_days`
- `sensor.workouts_last_30_days`
- `sensor.volume_last_7_days` (lb)

**Strength Score**

- `sensor.strength_score` — overall
- `sensor.strength_score_upper` / `_lower` / `_core`
- One sensor per muscle group Tonal reports (Chest, Back, Quads, …), each with
  its body region and last-updated time as attributes

Entity IDs are derived from your account name, so yours will be prefixed
accordingly (e.g. `sensor.alex_total_volume`).

## Service: `tonal.export_data`

Writes the current data to a JSON file in the same format ToneGet produces, so
anything that already reads a ToneGet export will read this too.

```yaml
action: tonal.export_data
data:
  full: false
  gzip: true
```

| Field | Default | Notes |
| --- | --- | --- |
| `config_entry_id` | first account | Which account to export |
| `file_path` | `/config/tonal_workouts_<timestamp>.json.gz` | Must be your config dir or in `allowlist_external_dirs` |
| `full` | `false` | Keep every raw API field instead of trimming unused ones |
| `gzip` | `true` | Write `.json.gz` |

The service returns the path and file size, so you can use it in a script:

```yaml
sequence:
  - action: tonal.export_data
    response_variable: export
  - action: notify.mobile_app
    data:
      message: "Tonal backup written to {{ export.file_path }}"
```

Pair it with a weekly automation for an off-box backup of your workout history.

## Example automation

```yaml
automation:
  - alias: Nudge me after a rest week
    triggers:
      - trigger: numeric_state
        entity_id: sensor.days_since_last_workout
        above: 6
    actions:
      - action: notify.mobile_app
        data:
          message: >
            Seven days since your last Tonal session. Strength score is
            {{ states('sensor.strength_score') }}.
```

## How it polls

Each refresh makes roughly:

- 1 request for account info, 1 for the profile
- 1 request per 100 workouts (Tonal's page size)
- 2 requests for strength scores

The first run additionally makes one request per distinct workout template to
resolve names; those are cached in `.storage` and never re-fetched. A 500-workout
account therefore settles at about 9 requests per poll — comparable to opening
the app.

## Troubleshooting

- **"Tonal rejected that email and password"** — the same credentials that work
  in the Tonal app should work here. Accounts using a social login (Apple,
  Google) cannot use the password grant.
- **Reauthentication prompt** — Tonal invalidated the refresh token. Re-enter
  your password when Home Assistant asks.
- **No strength score sensors** — those endpoints only return data once Tonal
  has enough workouts to compute a score.
- Enable debug logging to see request-level detail:

```yaml
logger:
  logs:
    custom_components.tonal: debug
```

Download diagnostics from the device page for a redacted snapshot of what the
integration is seeing.

## Notes on data and terms

This reads *your* workout records: timestamps, sets, reps, weights, volume,
scores. It does not download Tonal's programs, videos, coach content, or
movement library — workout templates are read only to keep their id and title so
your sessions have readable names.

Tonal's Terms of Service contain provisions about automated access. Those
provisions target scraping of proprietary content rather than users reading their
own history, but this tool is provided as-is with no warranty and you assume the
risk of using it.

## License

MIT — see [LICENSE](LICENSE).
