# TallySeal

**The grand total is right. Two teams are still wrong.**

TallySeal is a Hermes Agent plugin for checking grouped CSV reports. It recomputes each group's count and signed integer total from a source CSV whose SHA-256 you set before the agent starts. It flags missing, extra and duplicate groups, even when errors cancel out across the whole report.

Hermes can ask `tallyseal_check` for the failing record numbers and use that feedback to fix its draft. When you're ready to hand off the report, `hermes tallyseal seal` checks it again and writes the same bytes it checked to a new local file. A passing check on an earlier draft cannot release a later, incorrect draft.

| Team | Source total | Wrong draft | Corrected |
| --- | ---: | ---: | ---: |
| Atlas | 10,000 | 8,500 | 10,000 |
| Boreal | 6,000 | 7,500 | 6,000 |
| Cedar | 4,000 | 4,000 | 4,000 |
| **Grand total** | **20,000** | **20,000** | **20,000** |

## Try it

Use Hermes Agent 0.23.1 or newer on macOS or Linux. With Hermes and its Python environment on your PATH, run the demo from this repository:

```sh
python demo/run.py
```

The demo uses a disposable Hermes profile and the actual plugin loader, tool dispatcher, and CLI. It needs no model sign-in. The baseline accepts the bad grand total and has no TallySeal tool. With the plugin enabled, Hermes reports two mismatches and the seal command exits 2 without writing a file. The corrected report passes. Swapping the bad draft back in is rejected even after the earlier pass. Restoring the corrected report produces a sealed copy whose SHA-256 matches the checked draft.

## Use it on your data

Install from this repository:

```sh
hermes plugins install Venkat-RJ/hermes-tallyseal
```

Put the trusted input in `source.csv` and have Hermes write its draft to `report.csv` in the same job folder. Compute the source digest before the agent starts:

```sh
shasum -a 256 /absolute/job/folder/source.csv
```

In the active profile's `config.yaml`, merge these settings under `plugins`:

```yaml
plugins:
  enabled: [tallyseal]
  entries:
    tallyseal:
      settings:
        root: /absolute/job/folder
        source_sha256: "paste the 64-character lowercase digest here"
        group_by: [team]
        amount_column: amount_minor
```

The source needs the configured group columns and one column containing signed integer minor units. The report needs exactly the group columns plus `row_count` and `total_minor`, one row per group. Column order is flexible. Keys are matched exactly. CSVs must be UTF-8 and no larger than 4 MiB or 50,000 data records each. A record number counts parsed CSV records, with the header as record 1.

Ask Hermes to call `tallyseal_check` after writing its draft. You can run the same check and seal locally:

```sh
hermes tallyseal check
hermes tallyseal seal --out /absolute/handoff/final.csv
shasum -a 256 /absolute/handoff/final.csv
```

The destination folder must exist. An existing destination is never overwritten. The seal command emits the source and report hashes as JSON. The seal is a validated copy, not a signature or a claim that the original source is correct.

## What the plugin accesses

The check tool reads only `source.csv` and `report.csv` directly from the configured local job folder. It uses the host filesystem, not a remote terminal backend. It returns record numbers, counts, match flags and hashes without returning cell contents. Configure it only in profiles that may see that metadata.

The terminal-only seal command writes a new file to an operator-chosen local path. It stages a private temporary copy next to that destination and publishes it atomically after validation. The plugin does not make network calls, launch commands, read credentials, or use telemetry. It adds one tool and one CLI command through Hermes's public registration API.

Keep the profile configuration and source pin under operator control. TallySeal governs its own seal command; it does not intercept unrelated tools or stop someone with filesystem access from changing other copies.

## Developer checks

```sh
python -m unittest discover -s tests -v
hermes plugins validate . --install-deps
hermes plugins doctor . --ci
python demo/run.py
```
