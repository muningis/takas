# Plan day (optional)

Plan day turns one day of a training-plan file into a route request. It is **off by default**,
because a training plan is personal. Turn it on in your local `takas.toml` (gitignored):

```toml
[features]
plan = true

[plan]
path = "~/training/plan.json"
# lap_km = 5.0   # only needed when fixed-lap days don't say their lap length
```

Environment overrides: `TAKAS_FEATURE_PLAN=1`, `TAKAS_PLAN=/path/to/plan.json`. With the feature
off, the Plan day tab is hidden and `GET /api/day/{date}` returns 404. takas never writes to the
file.

## Format

A JSON object with a `days` array; [`plan.schema.json`](plan.schema.json) is the full schema.

```json
{
  "days": [
    {"date": "2030-03-01", "type": "Easy", "session": "Easy 7 km", "structure": "7 km easy",
     "km": 7, "minutes": 48, "pace": "6:30–7:05/km"},
    {"date": "2030-03-02", "type": "Hills", "session": "Hills 6×2 min",
     "structure": "15 min easy → 6 × 2 min uphill hard, walk down → 10 min easy", "km": 9, "minutes": 60, "pace": ""},
    {"date": "2030-03-03", "type": "Rest", "session": "Rest", "structure": "", "km": 0, "minutes": 0, "pace": ""}
  ]
}
```

## How a day becomes a route

| `type` (and session text) | Request |
|---|---|
| Easy, Medium-long | loop of the day's km, `mixed` surface |
| Long | loop, `road` |
| any session containing "trail" | loop, `trail` |
| Easy/Shakeout "N min", or no km | loop of N min at easy pace |
| Threshold | flat loop, `road`, prefer lit |
| Hills "N×M min" | hill repeats, ≥ 8 %; warm-up/cool-down minutes from `structure` |
| Vert "… repeats N×M min" | steep repeats, ≥ 12 %, power-hike |
| Vert, otherwise | hilly `trail` loop; gain target from "Aim A–B m+" |
| SIMULATION, Lap-pace, Long "N laps (T km)" | one flat fixed-lap loop (±1 %); lap from "N × X km", "N laps (T km)" or `lap_km` |
| Rest, Walk, Test, RACE | no route |
